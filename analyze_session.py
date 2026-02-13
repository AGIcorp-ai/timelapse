#!/usr/bin/env python3
"""Session-level prompt/commit attribution analyzer."""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

from lib.config import COLLECTOR_VERSION, REPORTS_DIR, REPOS, SCHEMA_VERSION
from lib.data_loaders import load_commits, load_session_events, utc_iso


def _nearest_preceding_prompt(commit_ts: datetime, prompt_events: list) -> tuple[str | None, float | None]:
    nearest = None
    for event in prompt_events:
        if event.ts > commit_ts:
            break
        nearest = event
    if nearest is None:
        return None, None
    lag_hours = (commit_ts - nearest.ts).total_seconds() / 3600.0
    return nearest.text[:300], lag_hours


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, choices=sorted(REPOS))
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--out-md", type=Path)
    parser.add_argument("--out-json", type=Path)
    parser.add_argument("--out-csv", type=Path)
    args = parser.parse_args()

    events = load_session_events(args.repo, args.session_id)
    if not events:
        raise SystemExit(f"No events found for session {args.session_id} in repo {args.repo}")

    events.sort(key=lambda e: e.ts)
    prompts = [e for e in events if e.role == "user"]
    assistants = [e for e in events if e.role == "assistant"]

    session_start = events[0].ts
    session_end = events[-1].ts

    first_prompt = prompts[0].ts if prompts else session_start
    last_prompt = prompts[-1].ts if prompts else session_end

    commit_window_start = first_prompt - timedelta(minutes=5)
    commit_window_end = last_prompt + timedelta(minutes=30)
    commits = load_commits(args.repo, REPOS[args.repo], commit_window_start, commit_window_end)
    commits.sort(key=lambda c: c.ts)

    file_counter = Counter(file for c in commits for file in c.files)
    top_files = [{"file": f, "touches": n} for f, n in file_counter.most_common(10)]

    attribution_rows: list[dict] = []
    lag_samples: list[float] = []
    for commit in commits:
        prompt_text, lag_hours = _nearest_preceding_prompt(commit.ts, prompts)
        if lag_hours is not None and 0.0 <= lag_hours <= 12.0:
            lag_samples.append(lag_hours)

        attribution_rows.append(
            {
                "sha": commit.sha,
                "ts": utc_iso(commit.ts),
                "subject": commit.subject,
                "insertions": commit.insertions,
                "deletions": commit.deletions,
                "nearest_prompt_text": prompt_text,
                "lag_hours": lag_hours,
                "files": commit.files,
            }
        )

    duration_ms = int((session_end - session_start).total_seconds() * 1000)
    prompt_count = len(prompts)
    commit_count = len(commits)
    lines_changed = sum(c.insertions + c.deletions for c in commits)

    commits_per_prompt = (commit_count / prompt_count) if prompt_count else 0.0
    lines_per_prompt = (lines_changed / prompt_count) if prompt_count else 0.0

    safe_name = args.session_id.replace("/", "_")
    out_md = args.out_md or (REPORTS_DIR / "sessions" / f"{args.repo}__{safe_name}.md")
    out_json = args.out_json or (REPORTS_DIR / "sessions" / f"{args.repo}__{safe_name}.json")
    out_csv = args.out_csv or (REPORTS_DIR / "sessions" / f"{args.repo}__{safe_name}.csv")

    md_lines = [
        f"# Session Analysis: `{args.session_id}`",
        "",
        f"Repo: {args.repo}",
        f"Session window: {utc_iso(session_start)} -> {utc_iso(session_end)}",
        f"Commit attribution window: {utc_iso(commit_window_start)} -> {utc_iso(commit_window_end)}",
        "",
        "## Summary",
        "",
        f"- Duration: {duration_ms / 1000.0:.1f}s",
        f"- User prompts: {prompt_count}",
        f"- Assistant messages: {len(assistants)}",
        f"- Linked commits: {commit_count}",
        f"- Commits/prompt: {commits_per_prompt:.3f}",
        f"- Lines changed/prompt: {lines_per_prompt:.2f}",
        "",
        "## Top Files",
        "",
    ]
    if not top_files:
        md_lines.append("- none")
    else:
        for row in top_files:
            md_lines.append(f"- `{row['file']}`: {row['touches']}")

    md_lines.extend(["", "## Commit Attribution", ""])
    for row in attribution_rows:
        lag_str = "n/a" if row["lag_hours"] is None else f"{row['lag_hours']:.2f}h"
        md_lines.append(f"- {row['ts']} `{row['sha'][:7]}` lag={lag_str} {row['subject']}")

    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text("\n".join(md_lines) + "\n")

    quality_flags: list[str] = []
    if not prompts:
        quality_flags.append("no_user_prompts")
    if any(c.binary_numstat for c in commits):
        quality_flags.append("binary_numstat_present")

    trace_rows = [
        {
            "trace_id": row["sha"],
            "parent_trace_id": None,
            "name": row["subject"],
            "start_time": row["ts"],
            "end_time": row["ts"],
            "latency_ms": 0,
            "gen_ai_operation_name": "code_commit",
            "gen_ai_request_model": None,
            "gen_ai_response_model": None,
            "gen_ai_response_id": None,
            "gen_ai_tool_name": None,
            "status": "ok",
            "error_type": None,
            "input_tokens": None,
            "output_tokens": None,
            "total_tokens": None,
            "cost_usd": None,
            "tags": ["commit_linked"],
            "metadata": {"files": row["files"], "lag_hours": row["lag_hours"]},
            "raw": {"vendor_fields": {}},
        }
        for row in attribution_rows
    ]

    payload = {
        "schema_version": SCHEMA_VERSION,
        "collector_version": COLLECTOR_VERSION,
        "semconv_version": "opentelemetry-genai-dev",
        "generated_at": utc_iso(datetime.now(timezone.utc)),
        "source_system": "claude+codex+git",
        "session_id": args.session_id,
        "session_previous_id": None,
        "session_name": None,
        "session_path": None,
        "start_time": utc_iso(session_start),
        "end_time": utc_iso(session_end),
        "duration_ms": duration_ms,
        "enduser_id": None,
        "enduser_pseudo_id": None,
        "environment": "local",
        "totals": {
            "request_count": prompt_count,
            "error_count": 0,
            "input_tokens": None,
            "output_tokens": None,
            "total_tokens": None,
            "cost_usd": None,
            "avg_latency_ms": None,
            "linked_commits": commit_count,
            "lines_changed": lines_changed,
            "commits_per_prompt": round(commits_per_prompt, 6),
            "lines_per_prompt": round(lines_per_prompt, 6),
        },
        "window": {
            "start": utc_iso(commit_window_start),
            "end": utc_iso(commit_window_end),
        },
        "quality_flags": quality_flags,
        "top_files": top_files,
        "commit_attribution": attribution_rows,
        "traces": trace_rows,
    }

    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(payload, indent=2) + "\n")

    out_csv.parent.mkdir(parents=True, exist_ok=True)
    with out_csv.open("w", newline="") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=[
                "sha",
                "ts",
                "subject",
                "insertions",
                "deletions",
                "lag_hours",
                "nearest_prompt_text",
                "files",
            ],
        )
        writer.writeheader()
        for row in attribution_rows:
            out = dict(row)
            out["files"] = "|".join(out["files"])
            writer.writerow(out)

    print(f"Wrote {out_md}")
    print(f"Wrote {out_json}")
    print(f"Wrote {out_csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
