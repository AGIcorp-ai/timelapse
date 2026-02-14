#!/usr/bin/env python3
"""Prompt-context validator for operator workflows.

Modes:
- Single prompt lint: provide --prompt and optional --context values.
- Batch lint: scans recent prompts and enforces a max lazy ratio.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lib.config import CLAUDE_SESSION_DIRS
from lib.data_loaders import Prompt, load_claude_prompts, load_codex_prompts
from time_machine_review import detect_lazy_prompt, enrich_prompts


def lint_one(prompt: str, context: list[str], max_score: int) -> int:
    score, reasons = detect_lazy_prompt(prompt, context)
    print("mode=single")
    print(f"lazy_score={score}")
    print("reasons=" + ",".join(reasons) if reasons else "reasons=none")
    if score > max_score:
        print(f"FAIL: lazy_score {score} > max_score {max_score}")
        return 1
    print("PASS")
    return 0


def load_recent_prompts(days: int) -> list[Prompt]:
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    prompts: list[Prompt] = []
    for repo, session_dir in CLAUDE_SESSION_DIRS.items():
        prompts.extend(load_claude_prompts(repo, session_dir, start, end))
    prompts.extend(load_codex_prompts(start, end))
    prompts.sort(key=lambda p: p.ts)
    return prompts


def lint_batch(days: int, max_ratio: float, min_context_turns: int) -> int:
    rows = enrich_prompts(load_recent_prompts(days))
    if not rows:
        print("mode=batch")
        print("PASS: no prompts in window")
        return 0

    lazy_rows = [r for r in rows if r["lazy"]]
    ratio = len(lazy_rows) / len(rows)
    reason_counts = Counter(reason for r in lazy_rows for reason in r["reasons"])

    under_context = sum(1 for r in rows if r["context_turns_considered"] < min_context_turns)
    print("mode=batch")
    print(f"days={days}")
    print(f"prompts={len(rows)}")
    print(f"lazy_prompts={len(lazy_rows)}")
    print(f"lazy_ratio={ratio:.6f}")
    print(f"context_lt_{min_context_turns}={under_context}")
    print("top_reasons=" + ",".join(f"{k}:{v}" for k, v in reason_counts.most_common(5)))

    if ratio > max_ratio:
        print(f"FAIL: lazy_ratio {ratio:.6f} > max_ratio {max_ratio:.6f}")
        return 1

    print("PASS")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prompt", type=str, help="single prompt text to lint")
    parser.add_argument(
        "--context",
        action="append",
        default=[],
        help="prior context turns for single-prompt lint; pass multiple times",
    )
    parser.add_argument("--max-score", type=int, default=2, help="single mode failure threshold")
    parser.add_argument("--days", type=int, default=35, help="batch mode window")
    parser.add_argument("--max-ratio", type=float, default=0.05, help="batch mode fail threshold")
    parser.add_argument(
        "--min-context-turns",
        type=int,
        default=2,
        help="report prompt rows with less than this context depth",
    )
    args = parser.parse_args()

    if args.prompt:
        return lint_one(args.prompt, args.context, args.max_score)
    return lint_batch(args.days, args.max_ratio, args.min_context_turns)


if __name__ == "__main__":
    raise SystemExit(main())
