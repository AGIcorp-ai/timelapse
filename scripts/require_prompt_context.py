#!/usr/bin/env python3
"""CI/local wrapper for prompt-context validation."""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=35)
    parser.add_argument("--max-ratio", type=float, default=0.05)
    parser.add_argument("--min-context-turns", type=int, default=2)
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    cmd = [
        "python",
        str(root / "tools" / "prompts" / "validate_prompt.py"),
        "--days",
        str(args.days),
        "--max-ratio",
        str(args.max_ratio),
        "--min-context-turns",
        str(args.min_context_turns),
    ]
    proc = subprocess.run(cmd, cwd=root, check=False)
    return proc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
