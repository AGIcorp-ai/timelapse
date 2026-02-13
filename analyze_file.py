#!/usr/bin/env python3
"""File-level churn, coupling, and velocity analyzer."""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

from lib.config import COLLECTOR_VERSION, REPORTS_DIR, REPOS, SCHEMA_VERSION
from lib.data_loaders import Commit, load_commits, utc_iso
from lib.metrics import coupling_scores, churn_velocity, per_file_retouch_ratio


def _filter_file_commits(commits: list[Commit], file_path: str) -> list[Commit]:
    return [c for c in commits if file_path in c.files]


def build_markdown(
    repo: str,
    file_path: str,
    start: datetime,
    end: datetime,
    file_commits: list[Commit],
    couplings: list[dict],
    velocity: list[dict],
) -> str:
    total_ins = sum(c.file_stats.get(file_path, (0, 0))[0] for c in file_commits)
    total_dels = sum(c.file_stats.get(file_path, (0, 0))[1] for c in file_commits)

    lines = [
        f"# File Analysis: `{file_path}`",
        "",
        f"Repo: {repo}",
        f"Window: {start.date()} to {end.date()} (UTC)",
        "",
        "## Summary",
        "",
        f"- Commit touches: {len(file_commits)}",
        f"- Lines changed in file: +{total_ins} / -{total_dels}",
        f"- Re-touch ratio (7-day): {per_file_retouch_ratio(file_commits, file_path, 7):.1%}",
        "",
        "## Top Coupled Files",
        "",
    ]

    if not couplings:
        lines.append("- none")
    else:
        for row in couplings[:15]:
            lines.append(
                f"- `{row['other_file']}`: shared={row['shared_commits']}, coupling={row['coupling']:.2f}"
            )

    lines.extend(["", "## Churn Velocity (weekly)", ""])
    if not velocity:
        lines.append("- none")
    else:
        for bucket in velocity:
            lines.append(
                f"- week#{bucket['bucket_index']}: touches={bucket['commit_touches']} +{bucket['insertions']} -{bucket['deletions']}"
            )

    lines.extend(["", "## Commit History", ""])
    for commit in file_commits[:80]:
        ins, dels = commit.file_stats.get(file_path, (0, 0))
        lines.append(
            f"- {commit.ts.date()} `{commit.sha[:7]}` +{ins}/-{dels} {commit.subject}"
        )

    return "\n".join(lines) + "\n"


def write_csv(path: Path, file_path: str, file_commits: list[Commit]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=[
                "sha",
                "ts",
                "subject",
                "file",
                "file_insertions",
                "file_deletions",
                "commit_insertions",
                "commit_deletions",
                "binary_numstat",
                "merge_commit",
            ],
        )
        writer.writeheader()
        for commit in file_commits:
            ins, dels = commit.file_stats.get(file_path, (0, 0))
            writer.writerow(
                {
                    "sha": commit.sha,
                    "ts": utc_iso(commit.ts),
                    "subject": commit.subject,
                    "file": file_path,
                    "file_insertions": ins,
                    "file_deletions": dels,
                    "commit_insertions": commit.insertions,
                    "commit_deletions": commit.deletions,
                    "binary_numstat": int(commit.binary_numstat),
                    "merge_commit": int(commit.merge_commit),
                }
            )


def build_json(
    repo: str,
    file_path: str,
    start: datetime,
    end: datetime,
    file_commits: list[Commit],
    couplings: list[dict],
    velocity: list[dict],
) -> dict:
    quality_flags: list[str] = []
    if any(c.binary_numstat for c in file_commits):
        quality_flags.append("binary_numstat_present")
    if not file_commits:
        quality_flags.append("no_commits_for_file")

    total_ins = sum(c.file_stats.get(file_path, (0, 0))[0] for c in file_commits)
    total_dels = sum(c.file_stats.get(file_path, (0, 0))[1] for c in file_commits)

    return {
        "schema_version": SCHEMA_VERSION,
        "collector_version": COLLECTOR_VERSION,
        "generated_at": utc_iso(datetime.now(timezone.utc)),
        "source_system": "git",
        "repo": repo,
        "file": file_path,
        "window": {"start": utc_iso(start), "end": utc_iso(end)},
        "summary": {
            "commit_touches": len(file_commits),
            "file_insertions": total_ins,
            "file_deletions": total_dels,
            "retouch_ratio_7day": round(per_file_retouch_ratio(file_commits, file_path, 7), 6),
        },
        "couplings": couplings,
        "velocity": velocity,
        "quality_flags": quality_flags,
        "commits": [
            {
                "sha": c.sha,
                "ts": utc_iso(c.ts),
                "subject": c.subject,
                "file_insertions": c.file_stats.get(file_path, (0, 0))[0],
                "file_deletions": c.file_stats.get(file_path, (0, 0))[1],
                "commit_insertions": c.insertions,
                "commit_deletions": c.deletions,
                "binary_numstat": c.binary_numstat,
                "merge_commit": c.merge_commit,
            }
            for c in file_commits
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, choices=sorted(REPOS))
    parser.add_argument("--file", required=True, help="Repo-relative file path")
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--out-md", type=Path)
    parser.add_argument("--out-json", type=Path)
    parser.add_argument("--out-csv", type=Path)
    args = parser.parse_args()

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=args.days)
    commits = load_commits(args.repo, REPOS[args.repo], start, end)
    commits.sort(key=lambda c: c.ts)

    file_commits = _filter_file_commits(commits, args.file)
    couplings = coupling_scores(commits, args.file)
    velocity = churn_velocity(commits, args.file, bucket_days=7)

    safe_name = args.file.replace("/", "__")
    out_md = args.out_md or (REPORTS_DIR / "files" / f"{args.repo}__{safe_name}.md")
    out_json = args.out_json or (REPORTS_DIR / "files" / f"{args.repo}__{safe_name}.json")
    out_csv = args.out_csv or (REPORTS_DIR / "files" / f"{args.repo}__{safe_name}.csv")

    md = build_markdown(args.repo, args.file, start, end, file_commits, couplings, velocity)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text(md)

    payload = build_json(args.repo, args.file, start, end, file_commits, couplings, velocity)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(payload, indent=2) + "\n")

    write_csv(out_csv, args.file, file_commits)

    print(f"Wrote {out_md}")
    print(f"Wrote {out_json}")
    print(f"Wrote {out_csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
