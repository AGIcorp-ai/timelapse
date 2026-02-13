#!/usr/bin/env python3
"""Backward-compatible wrapper for repo-wide timelapse analysis."""

from __future__ import annotations

import argparse
from pathlib import Path

from analyze_repo import run


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=30, help="Lookback window in days")
    parser.add_argument(
        "--out",
        type=Path,
        default=Path(__file__).parent / "reports" / "last_month_review.md",
        help="Markdown output path",
    )
    args = parser.parse_args()

    run(days=args.days, out_md=args.out, out_json=None, out_csv=None)
    print(f"Wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
