# Timelapse Analyzer Suite

This directory contains multi-level development analytics scripts that produce both human-readable markdown and ML-ready JSON/CSV.

## Analyzers

- `analyze_repo.py`: repo-wide throughput, churn, prompt/commit lag
- `analyze_file.py`: per-file churn, coupling, velocity
- `analyze_symbols.py`: per-symbol/function churn attribution within a file
- `analyze_session.py`: per-session prompt-to-commit attribution
- `analyze_last_month.py`: backward-compatible wrapper for legacy monthly report generation

## Quick Start

Run from this directory (`/home/ath/4D-bot/timelapse`):

```bash
python analyze_repo.py --days 30
python analyze_file.py --repo 4D-bot --file arena_v0/cli.py --days 30
python analyze_symbols.py --repo 4D-bot --file arena_v0/cli.py --days 30
python analyze_session.py --repo 4D-bot --session-id <SESSION_ID>
python analyze_last_month.py --days 30
```

Default outputs are written under:

- `reports/repo/`
- `reports/files/`
- `reports/symbols/`
- `reports/sessions/`

## Output Contracts (JSON)

All JSON outputs include provenance fields:

- `schema_version`
- `collector_version`
- `generated_at`
- `source_system`
- `quality_flags`

### 1) Repo-level JSON (`analyze_repo.py`)

Path: `reports/repo/last_30_days.json`

```json
{
  "schema_version": "v0.1",
  "collector_version": "timelapse-analyzers/0.1",
  "generated_at": "2026-02-13T20:00:00Z",
  "source_system": "git+claude+codex",
  "window": {"start": "...", "end": "..."},
  "throughput": {
    "commits": 114,
    "prompts": 1280,
    "insertions": 706131,
    "deletions": 14273,
    "commits_per_day": 3.8
  },
  "mix": {
    "commits_by_repo": {"4D-bot": 66, "SICM": 48},
    "prompts_by_repo": {"4D-bot": 677, "SICM": 603},
    "prompts_by_source": {"codex": 915, "claude": 365}
  },
  "optimization": {
    "rework_ratio_7day": 0.07,
    "median_prompt_lag_hours": 0.05
  },
  "top_churn_files": [
    {"file": "arena_v0/cli.py", "touches": 21, "insertions": 1000, "deletions": 120}
  ],
  "quality_flags": [],
  "commits": [
    {
      "sha": "...",
      "ts": "...",
      "repo": "4D-bot",
      "subject": "...",
      "files": ["arena_v0/cli.py"],
      "insertions": 10,
      "deletions": 5,
      "binary_numstat": false,
      "merge_commit": false
    }
  ]
}
```

ML note: `commits[]` is a flat row-set suitable for direct ingestion.

### 2) File-level JSON (`analyze_file.py`)

Path: `reports/files/<repo>__<file>.json`

```json
{
  "schema_version": "v0.1",
  "collector_version": "timelapse-analyzers/0.1",
  "generated_at": "...",
  "source_system": "git",
  "repo": "4D-bot",
  "file": "arena_v0/cli.py",
  "window": {"start": "...", "end": "..."},
  "summary": {
    "commit_touches": 21,
    "file_insertions": 900,
    "file_deletions": 120,
    "retouch_ratio_7day": 0.42
  },
  "couplings": [
    {
      "file": "arena_v0/cli.py",
      "other_file": "arena_v0/ui_server.py",
      "shared_commits": 8,
      "target_commit_touches": 21,
      "coupling": 0.381
    }
  ],
  "velocity": [
    {
      "bucket_index": 0,
      "bucket_start": "2026-01-15",
      "commit_touches": 3,
      "insertions": 120,
      "deletions": 20
    }
  ],
  "quality_flags": [],
  "commits": []
}
```

ML note: `couplings[]` can be used as an edge list for temporal co-change graphs.

### 3) Symbol-level JSON (`analyze_symbols.py`)

Path: `reports/symbols/<repo>__<file>.json`

```json
{
  "schema_version": "v0.1",
  "collector_version": "timelapse-analyzers/0.1",
  "generated_at": "...",
  "source_system": "git",
  "repo": "4D-bot",
  "file": "arena_v0/cli.py",
  "window": {"start": "...", "end": "..."},
  "quality_flags": ["symbol_fallback_header"],
  "symbol_touches": [
    {
      "repo": "4D-bot",
      "sha": "...",
      "ts": "...",
      "file": "arena_v0/cli.py",
      "symbol_id": "main",
      "symbol_display": "main",
      "touches": 1,
      "added": 12,
      "deleted": 3,
      "churn": 15,
      "extractor": "ast",
      "flags": []
    }
  ],
  "symbols": [
    {
      "symbol_id": "main",
      "symbol_display": "main",
      "touches": 9,
      "added": 130,
      "deleted": 25,
      "churn": 155,
      "first_touch": "...",
      "last_touch": "...",
      "avg_gap_days": 2.3
    }
  ]
}
```

ML note: `symbol_touches[]` is the event stream; `symbols[]` is aggregate features.

### 4) Session-level JSON (`analyze_session.py`)

Path: `reports/sessions/<repo>__<session_id>.json`

```json
{
  "schema_version": "v0.1",
  "collector_version": "timelapse-analyzers/0.1",
  "semconv_version": "opentelemetry-genai-dev",
  "generated_at": "...",
  "source_system": "claude+codex+git",
  "session_id": "...",
  "session_previous_id": null,
  "session_name": null,
  "session_path": null,
  "start_time": "...",
  "end_time": "...",
  "duration_ms": 1280000,
  "totals": {
    "request_count": 27,
    "error_count": 0,
    "input_tokens": null,
    "output_tokens": null,
    "total_tokens": null,
    "cost_usd": null,
    "avg_latency_ms": null,
    "linked_commits": 4,
    "lines_changed": 240,
    "commits_per_prompt": 0.148,
    "lines_per_prompt": 8.88
  },
  "window": {"start": "...", "end": "..."},
  "quality_flags": [],
  "top_files": [{"file": "arena_v0/cli.py", "touches": 2}],
  "commit_attribution": [
    {
      "sha": "...",
      "ts": "...",
      "subject": "...",
      "insertions": 20,
      "deletions": 5,
      "nearest_prompt_text": "...",
      "lag_hours": 0.15,
      "files": ["arena_v0/cli.py"]
    }
  ],
  "traces": [
    {
      "trace_id": "...",
      "parent_trace_id": null,
      "name": "...",
      "start_time": "...",
      "end_time": "...",
      "latency_ms": 0,
      "gen_ai_operation_name": "code_commit",
      "status": "ok",
      "metadata": {"files": ["arena_v0/cli.py"], "lag_hours": 0.15}
    }
  ]
}
```

ML note: this schema is OTel-aligned enough to map into future tracing systems without breaking historical data.

## CSV Outputs

Each analyzer also emits CSV for easy loading in pandas/duckdb:

- Repo: one row per commit
- File: one row per commit touching the target file
- Symbols: one row per symbol-touch event
- Session: one row per linked commit attribution

## Backward Compatibility

`analyze_last_month.py` remains supported and now wraps `analyze_repo.py`.

```bash
python analyze_last_month.py --days 30 --out reports/last_month_review.md
```

## Test

```bash
python -m unittest discover -s tests -v
```
