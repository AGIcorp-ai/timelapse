#!/usr/bin/env python3
"""Symbol-level churn analyzer for a target file."""

from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from lib.config import COLLECTOR_VERSION, REPORTS_DIR, REPOS, SCHEMA_VERSION
from lib.data_loaders import Commit, load_commits, run_git, utc_iso
from lib.symbol_extractor import (
    extract_symbols,
    map_hunks_to_symbols,
    parse_diff_hunks,
    symbols_from_hunk_headers,
)


def _load_file_at_commit(repo_path: Path, sha: str, file_path: str) -> str:
    return run_git(["git", "show", f"{sha}:{file_path}"], repo_path)


def _diff_for_commit_file(repo_path: Path, sha: str, file_path: str) -> str:
    return run_git(["git", "show", "--format=", "--unified=0", sha, "--", file_path], repo_path)


def _symbol_rows_for_commit(repo_path: Path, commit: Commit, file_path: str) -> tuple[list[dict], list[str]]:
    quality_flags: list[str] = []
    if commit.merge_commit:
        return [], ["merge_skipped"]

    diff_text = _diff_for_commit_file(repo_path, commit.sha, file_path)
    hunks = parse_diff_hunks(diff_text)
    if not hunks:
        return [], []

    source = _load_file_at_commit(repo_path, commit.sha, file_path)
    symbols = extract_symbols(source)

    if symbols:
        touched = map_hunks_to_symbols(hunks, symbols)
        extractor = "ast"
    else:
        touched = symbols_from_hunk_headers(hunks)
        extractor = "hunk_header"
        quality_flags.append("symbol_fallback_header")

    if not touched:
        quality_flags.append("symbol_unresolved")
        touched = {"unknown": len(hunks)}

    rows: list[dict] = []
    for symbol, touches in touched.items():
        added = 0
        deleted = 0
        for h in hunks:
            if extractor == "ast" and symbol in symbols:
                start, end = symbols[symbol]
                changed = set(h.added_lines) | set(h.deleted_lines)
                if not any(start <= ln <= end for ln in changed):
                    continue
            added += len(h.added_lines)
            deleted += len(h.deleted_lines)

        rows.append(
            {
                "repo": commit.repo,
                "sha": commit.sha,
                "ts": utc_iso(commit.ts),
                "file": file_path,
                "symbol_id": symbol,
                "symbol_display": symbol,
                "touches": int(touches),
                "added": added,
                "deleted": deleted,
                "churn": added + deleted,
                "extractor": extractor,
                "flags": quality_flags[:],
            }
        )
    return rows, quality_flags


def _build_aggregate(rows: list[dict]) -> list[dict]:
    grouped: dict[str, dict] = {}
    per_symbol_ts: defaultdict[str, list[datetime]] = defaultdict(list)

    for row in rows:
        sym = row["symbol_id"]
        agg = grouped.setdefault(
            sym,
            {
                "symbol_id": sym,
                "symbol_display": row["symbol_display"],
                "touches": 0,
                "added": 0,
                "deleted": 0,
                "churn": 0,
                "first_touch": row["ts"],
                "last_touch": row["ts"],
                "avg_gap_days": None,
            },
        )
        agg["touches"] += row["touches"]
        agg["added"] += row["added"]
        agg["deleted"] += row["deleted"]
        agg["churn"] += row["churn"]
        agg["first_touch"] = min(agg["first_touch"], row["ts"])
        agg["last_touch"] = max(agg["last_touch"], row["ts"])
        per_symbol_ts[sym].append(datetime.fromisoformat(row["ts"].replace("Z", "+00:00")))

    for sym, stamps in per_symbol_ts.items():
        stamps.sort()
        if len(stamps) < 2:
            continue
        gaps = [(b - a).total_seconds() / 86400.0 for a, b in zip(stamps, stamps[1:])]
        grouped[sym]["avg_gap_days"] = round(sum(gaps) / len(gaps), 4)

    return sorted(grouped.values(), key=lambda r: (r["touches"], r["churn"]), reverse=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, choices=sorted(REPOS))
    parser.add_argument("--file", required=True)
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--out-md", type=Path)
    parser.add_argument("--out-json", type=Path)
    parser.add_argument("--out-csv", type=Path)
    args = parser.parse_args()

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=args.days)
    commits = load_commits(args.repo, REPOS[args.repo], start, end)
    commits = [c for c in commits if args.file in c.files]
    commits.sort(key=lambda c: c.ts)

    rows: list[dict] = []
    quality_flags: set[str] = set()
    for commit in commits:
        commit_rows, flags = _symbol_rows_for_commit(REPOS[args.repo], commit, args.file)
        rows.extend(commit_rows)
        quality_flags.update(flags)
        if commit.binary_numstat:
            quality_flags.add("binary_numstat_present")

    aggregate = _build_aggregate(rows)

    safe_name = args.file.replace("/", "__")
    out_md = args.out_md or (REPORTS_DIR / "symbols" / f"{args.repo}__{safe_name}.md")
    out_json = args.out_json or (REPORTS_DIR / "symbols" / f"{args.repo}__{safe_name}.json")
    out_csv = args.out_csv or (REPORTS_DIR / "symbols" / f"{args.repo}__{safe_name}.csv")

    md_lines = [
        f"# Symbol Analysis: `{args.file}`",
        "",
        f"Repo: {args.repo}",
        f"Window: {start.date()} to {end.date()} (UTC)",
        "",
        "## Top Symbols",
        "",
    ]
    if not aggregate:
        md_lines.append("- none")
    else:
        for row in aggregate[:30]:
            md_lines.append(
                f"- `{row['symbol_display']}`: touches={row['touches']} churn={row['churn']} first={row['first_touch']} last={row['last_touch']}"
            )

    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text("\n".join(md_lines) + "\n")

    payload = {
        "schema_version": SCHEMA_VERSION,
        "collector_version": COLLECTOR_VERSION,
        "generated_at": utc_iso(datetime.now(timezone.utc)),
        "source_system": "git",
        "repo": args.repo,
        "file": args.file,
        "window": {"start": utc_iso(start), "end": utc_iso(end)},
        "quality_flags": sorted(quality_flags),
        "symbol_touches": rows,
        "symbols": aggregate,
    }
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(payload, indent=2) + "\n")

    out_csv.parent.mkdir(parents=True, exist_ok=True)
    with out_csv.open("w", newline="") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=[
                "repo",
                "sha",
                "ts",
                "file",
                "symbol_id",
                "symbol_display",
                "touches",
                "added",
                "deleted",
                "churn",
                "extractor",
                "flags",
            ],
        )
        writer.writeheader()
        for row in rows:
            out_row = dict(row)
            out_row["flags"] = "|".join(out_row["flags"])
            writer.writerow(out_row)

    print(f"Wrote {out_md}")
    print(f"Wrote {out_json}")
    print(f"Wrote {out_csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
