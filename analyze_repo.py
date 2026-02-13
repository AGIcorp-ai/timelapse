#!/usr/bin/env python3
"""Repo-wide timelapse analyzer with markdown + JSON + CSV outputs."""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

from lib.config import (
    CLAUDE_SESSION_DIRS,
    COLLECTOR_VERSION,
    REPORTS_DIR,
    REPOS,
    SCHEMA_VERSION,
)
from lib.data_loaders import Commit, Prompt, load_claude_prompts, load_codex_prompts, load_commits, utc_iso
from lib.metrics import median_or_none, nearest_prompt_lags_hours, rework_ratio


def build_markdown_report(commits: list[Commit], prompts: list[Prompt], start: datetime, end: datetime) -> str:
    total_insertions = sum(c.insertions for c in commits)
    total_deletions = sum(c.deletions for c in commits)
    span_days = max(1, (end - start).days)
    commits_per_day = len(commits) / span_days

    lags = nearest_prompt_lags_hours(commits, prompts)
    med_lag = median_or_none(lags)

    commits_by_repo = Counter(c.repo for c in commits)
    prompts_by_repo = Counter(p.repo for p in prompts)
    prompts_by_source = Counter(p.source for p in prompts)
    file_counter = Counter(file_path for c in commits for file_path in c.files)
    top_files = file_counter.most_common(15)
    retouch = rework_ratio(commits, window_days=7)

    lines = [
        "# Last-Month Development Review",
        "",
        f"Window: {start.date()} to {end.date()} (UTC)",
        "",
        "## Throughput",
        "",
        f"- Commits: {len(commits)}",
        f"- Prompts captured: {len(prompts)}",
        f"- Lines changed: +{total_insertions} / -{total_deletions}",
        f"- Avg commits/day: {commits_per_day:.2f}",
        "",
        "## Mix",
        "",
        f"- Commits by repo: {dict(commits_by_repo)}",
        f"- Prompts by repo: {dict(prompts_by_repo)}",
        f"- Prompt source mix: {dict(prompts_by_source)}",
        "",
        "## Optimization Proxies",
        "",
        f"- 7-day file re-touch ratio: {retouch:.1%}",
    ]
    if med_lag is not None:
        lines.append(f"- Median prompt-to-commit lag (<=12h pairs): {med_lag:.2f}h")
    else:
        lines.append("- Median prompt-to-commit lag: n/a (insufficient matched pairs)")

    lines.extend(["", "## Top Churn Files", ""])
    for file_path, count in top_files:
        lines.append(f"- `{file_path}`: {count} commit touches")

    lines.extend(
        [
            "",
            "## Suggested Next Experiments",
            "",
            "1. Pick the top 3 churn files and define one explicit acceptance test each before editing.",
            "2. Cap prompt-to-commit latency by batching small edits into 45-minute review blocks.",
            "3. Run weekly 30-day diff and track whether re-touch ratio drops week-over-week.",
        ]
    )
    return "\n".join(lines) + "\n"


def build_repo_json(commits: list[Commit], prompts: list[Prompt], start: datetime, end: datetime) -> dict:
    total_insertions = sum(c.insertions for c in commits)
    total_deletions = sum(c.deletions for c in commits)
    span_days = max(1, (end - start).days)
    lags = nearest_prompt_lags_hours(commits, prompts)

    commits_by_repo = Counter(c.repo for c in commits)
    prompts_by_repo = Counter(p.repo for p in prompts)
    prompts_by_source = Counter(p.source for p in prompts)

    top_files_counter = Counter(file_path for c in commits for file_path in c.files)
    top_files = []
    for file_path, touches in top_files_counter.most_common(25):
        ins = sum(c.file_stats.get(file_path, (0, 0))[0] for c in commits)
        dels = sum(c.file_stats.get(file_path, (0, 0))[1] for c in commits)
        top_files.append(
            {
                "file": file_path,
                "touches": touches,
                "insertions": ins,
                "deletions": dels,
            }
        )

    quality_flags: list[str] = []
    if any(c.binary_numstat for c in commits):
        quality_flags.append("binary_numstat_present")

    return {
        "schema_version": SCHEMA_VERSION,
        "collector_version": COLLECTOR_VERSION,
        "generated_at": utc_iso(datetime.now(timezone.utc)),
        "source_system": "git+claude+codex",
        "window": {"start": utc_iso(start), "end": utc_iso(end)},
        "throughput": {
            "commits": len(commits),
            "prompts": len(prompts),
            "insertions": total_insertions,
            "deletions": total_deletions,
            "commits_per_day": round(len(commits) / span_days, 4),
        },
        "mix": {
            "commits_by_repo": dict(commits_by_repo),
            "prompts_by_repo": dict(prompts_by_repo),
            "prompts_by_source": dict(prompts_by_source),
        },
        "optimization": {
            "rework_ratio_7day": round(rework_ratio(commits, window_days=7), 6),
            "median_prompt_lag_hours": median_or_none(lags),
        },
        "top_churn_files": top_files,
        "quality_flags": quality_flags,
        "commits": [
            {
                "sha": c.sha,
                "ts": utc_iso(c.ts),
                "repo": c.repo,
                "subject": c.subject,
                "files": c.files,
                "insertions": c.insertions,
                "deletions": c.deletions,
                "binary_numstat": c.binary_numstat,
                "merge_commit": c.merge_commit,
            }
            for c in commits
        ],
    }


def write_commit_csv(path: Path, commits: list[Commit]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=[
                "sha",
                "ts",
                "repo",
                "subject",
                "insertions",
                "deletions",
                "binary_numstat",
                "merge_commit",
                "files",
            ],
        )
        writer.writeheader()
        for commit in commits:
            writer.writerow(
                {
                    "sha": commit.sha,
                    "ts": utc_iso(commit.ts),
                    "repo": commit.repo,
                    "subject": commit.subject,
                    "insertions": commit.insertions,
                    "deletions": commit.deletions,
                    "binary_numstat": int(commit.binary_numstat),
                    "merge_commit": int(commit.merge_commit),
                    "files": "|".join(commit.files),
                }
            )


def collect_data(days: int) -> tuple[list[Commit], list[Prompt], datetime, datetime]:
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)

    commits: list[Commit] = []
    for repo_name, repo_path in REPOS.items():
        commits.extend(load_commits(repo_name, repo_path, start, end))
    commits.sort(key=lambda c: c.ts)

    prompts: list[Prompt] = []
    for repo_name, session_dir in CLAUDE_SESSION_DIRS.items():
        prompts.extend(load_claude_prompts(repo_name, session_dir, start, end))
    prompts.extend(load_codex_prompts(start, end))
    prompts.sort(key=lambda p: p.ts)

    return commits, prompts, start, end


def run(
    days: int,
    out_md: Path,
    out_json: Path | None,
    out_csv: Path | None,
) -> None:
    commits, prompts, start, end = collect_data(days)

    md = build_markdown_report(commits, prompts, start, end)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text(md)

    if out_json is not None:
        payload = build_repo_json(commits, prompts, start, end)
        out_json.parent.mkdir(parents=True, exist_ok=True)
        out_json.write_text(json.dumps(payload, indent=2) + "\n")

    if out_csv is not None:
        write_commit_csv(out_csv, commits)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--out-md", type=Path, default=REPORTS_DIR / "repo" / "last_30_days.md")
    parser.add_argument("--out-json", type=Path, default=REPORTS_DIR / "repo" / "last_30_days.json")
    parser.add_argument("--out-csv", type=Path, default=REPORTS_DIR / "repo" / "commits_last_30_days.csv")
    args = parser.parse_args()

    run(days=args.days, out_md=args.out_md, out_json=args.out_json, out_csv=args.out_csv)
    print(f"Wrote {args.out_md}")
    print(f"Wrote {args.out_json}")
    print(f"Wrote {args.out_csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
