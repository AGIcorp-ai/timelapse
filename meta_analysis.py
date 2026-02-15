#!/usr/bin/env python3
"""GPT-5.2 meta-analysis: senior-engineer review of all derived reports.

Two-call architecture:
  1. Synthesis -- cross-report narrative review (~120-150k input, ~8k output)
  2. Verdict  -- machine-readable action plan from synthesis (~20k input, ~4k output)

Budget: 1,000,000 GPT-5.2 tokens per 24 hours (~5 runs/day headroom).
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from rlm_harness import call_gpt5mini, parse_json_fallback, read_objective, utc_iso


# ---------------------------------------------------------------------------
# File loaders
# ---------------------------------------------------------------------------

def load_report_json(path: Path) -> dict:
    """Load a JSON report, returning empty dict on failure."""
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return {}


def load_report_text(path: Path) -> str:
    """Load a text/markdown report, returning empty string on failure."""
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


# ---------------------------------------------------------------------------
# Payload compression
# ---------------------------------------------------------------------------

def summarize_repo_metrics(data: dict) -> dict:
    """Extract only aggregate sections from last_30_days.json.

    Cuts ~217k bytes down to ~15-20k tokens by keeping throughput, mix,
    optimization, quality flags, top 10 churn files, and the 50 most recent
    commits (subject + repo + ts only, no file lists).
    """
    summary: dict = {}
    for key in ("schema_version", "generated_at", "window", "throughput", "mix",
                "optimization", "quality_flags"):
        if key in data:
            summary[key] = data[key]

    churn = data.get("top_churn_files", [])
    summary["top_churn_files"] = churn[:10]

    commits = data.get("commits", [])
    summary["recent_commits"] = [
        {"sha": c.get("sha", "")[:12], "ts": c.get("ts", ""), "repo": c.get("repo", ""),
         "subject": c.get("subject", "")}
        for c in commits[:50]
    ]
    summary["total_commits_in_window"] = len(commits)

    return summary


# ---------------------------------------------------------------------------
# Context assembly
# ---------------------------------------------------------------------------

def build_context_payload(reports_dir: Path) -> dict:
    """Assemble all report data into one dict for the synthesis call."""
    repo_raw = load_report_json(reports_dir / "repo" / "last_30_days.json")
    repo_summary = summarize_repo_metrics(repo_raw) if repo_raw else {}

    return {
        "repo_metrics": repo_summary,
        "time_machine": load_report_json(
            reports_dir / "time_machine" / "time_machine_review.json"),
        "objective_timeline": load_report_json(
            reports_dir / "rlm" / "objective_timeline.json"),
        "objective_inference": load_report_json(
            reports_dir / "rlm" / "objective_inference.json"),
        "data_volume": load_report_json(
            reports_dir / "rlm" / "data_volume.json"),
    }


# ---------------------------------------------------------------------------
# Prompt builders
# ---------------------------------------------------------------------------

def build_synthesis_prompt(context: dict, objective: str, rlm_text: str) -> str:
    """Call 1: senior engineer review of the actual project work."""
    context_json = json.dumps(context, indent=2)
    return (
        "You are a senior staff engineer reviewing the engineering output of "
        "three related projects (4D-bot, SICM, ascii-engine). The data below "
        "comes from automated analyzers -- use it as evidence, not as the subject "
        "of your review. Your job is to assess the actual software being built "
        "and recommend what to build, fix, or ship next.\n\n"
        f"Primary objective:\n{objective}\n\n"
        f"Prior engineering review (markdown):\n{rlm_text}\n\n"
        f"Structured evidence:\n{context_json}\n\n"
        "Produce a thorough markdown report with these exact sections:\n\n"
        "## 1. Project Health\n"
        "What is the current state of each project (4D-bot, SICM, ascii-engine)? "
        "Where is momentum concentrated? What has stalled? Use commit velocity, "
        "churn patterns, and rework ratio as evidence.\n\n"
        "## 2. Architecture & Technical Debt\n"
        "Based on the high-churn files and commit patterns, where is the codebase "
        "accumulating accidental complexity? What refactors or design changes "
        "would reduce future rework?\n\n"
        "## 3. Goal Convergence\n"
        "Is the recent work actually advancing the stated objective, or is effort "
        "being spent on tangential work? Cite specific commits/files as evidence.\n\n"
        "## 4. Highest-Impact Work\n"
        "Ranked list of max 7 engineering tasks. Each must describe a concrete "
        "change to the project code/architecture (not to the analytics pipeline). "
        "Rank by impact on the primary objective.\n\n"
        "## 5. Risk Register\n"
        "What could go wrong in the next 2 weeks? Identify coupling risks, "
        "incomplete features, and areas where the codebase is fragile.\n\n"
        "## 6. 7-Day Sprint\n"
        "Exactly 3 concrete engineering tasks to execute in the next 7 days. "
        "Each should specify which repo, which files, and what the change is. "
        "Be specific enough to hand to another engineer.\n"
    )


def build_verdict_prompt(synthesis_md: str, objective_inference: dict) -> str:
    """Call 2: machine-readable engineering action plan."""
    inference_json = json.dumps(objective_inference, indent=2)
    return (
        "You are a machine-output formatter. Given the engineering review below "
        "and the objective inference data, produce ONLY valid JSON (no markdown, "
        "no explanation) with this exact schema:\n\n"
        "{\n"
        '  "verdict": {\n'
        '    "trajectory": "converging | diverging | stalled",\n'
        '    "confidence": 0.0,\n'
        '    "primary_risk": "one-sentence engineering risk",\n'
        '    "highest_leverage_action": "one-sentence, specific to project code"\n'
        "  },\n"
        '  "priority_actions": [\n'
        "    {\n"
        '      "rank": 1,\n'
        '      "action": "concrete code/architecture change",\n'
        '      "repo": "which repo",\n'
        '      "rationale": "why this moves the needle",\n'
        '      "effort": "low | medium | high"\n'
        "    }\n"
        "  ],\n"
        '  "tech_debt": [\n'
        "    {\n"
        '      "location": "repo/file or module",\n'
        '      "issue": "what is wrong",\n'
        '      "fix": "what to do"\n'
        "    }\n"
        "  ],\n"
        '  "risks": [\n'
        "    {\n"
        '      "risk": "what could go wrong",\n'
        '      "likelihood": "low | medium | high",\n'
        '      "mitigation": "what to do about it"\n'
        "    }\n"
        "  ],\n"
        '  "next_review_trigger": "condition that should prompt the next review"\n'
        "}\n\n"
        f"Objective inference data:\n{inference_json}\n\n"
        f"Engineering review:\n{synthesis_md}\n"
    )


# ---------------------------------------------------------------------------
# Token tracking
# ---------------------------------------------------------------------------

def estimate_tokens(text: str) -> int:
    """Rough token estimate: len(text) / 4."""
    return len(text) // 4


def track_token_usage(call_name: str, prompt: str, response: str, log: list) -> None:
    """Append a usage entry to the in-memory log."""
    log.append({
        "call": call_name,
        "ts": utc_iso(datetime.now(timezone.utc)),
        "prompt_chars": len(prompt),
        "response_chars": len(response),
        "prompt_tokens_est": estimate_tokens(prompt),
        "response_tokens_est": estimate_tokens(response),
        "total_tokens_est": estimate_tokens(prompt) + estimate_tokens(response),
    })


def check_daily_budget(out_dir: Path, limit: int) -> int:
    """Read prior token_usage.json from same UTC date, return remaining budget."""
    usage_path = out_dir / "token_usage.json"
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    used = 0

    if usage_path.exists():
        try:
            data = json.loads(usage_path.read_text(encoding="utf-8"))
            for entry in data.get("entries", []):
                entry_date = entry.get("ts", "")[:10]
                if entry_date == today:
                    used += entry.get("total_tokens_est", 0)
        except (json.JSONDecodeError, OSError):
            pass

    return limit - used


# ---------------------------------------------------------------------------
# Combined report renderer
# ---------------------------------------------------------------------------

def render_combined_report(synthesis_md: str, verdict: dict) -> str:
    """Combine synthesis narrative and verdict JSON into final report."""
    lines = [
        "# Meta-Analysis Report",
        "",
        f"Generated: {utc_iso(datetime.now(timezone.utc))}",
        "",
        "---",
        "",
        synthesis_md.strip(),
        "",
        "---",
        "",
        "## Machine-Readable Verdict",
        "",
        "```json",
        json.dumps(verdict, indent=2),
        "```",
        "",
    ]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=str, default="gpt-5.2")
    parser.add_argument("--out-dir", type=Path, default=Path("reports") / "meta")
    parser.add_argument("--reports-dir", type=Path, default=Path("reports"))
    parser.add_argument("--dry-run", action="store_true",
                        help="Build prompts and print token estimates; no API calls")
    parser.add_argument("--synthesis-only", action="store_true",
                        help="Run only the synthesis call, skip verdict")
    parser.add_argument("--budget-limit", type=int, default=1_000_000,
                        help="Daily token budget (default 1,000,000)")
    parser.add_argument("--skip-budget-check", action="store_true",
                        help="Bypass daily budget check")
    args = parser.parse_args()

    args.out_dir.mkdir(parents=True, exist_ok=True)

    # --- Budget check ---
    if not args.skip_budget_check and not args.dry_run:
        remaining = check_daily_budget(args.out_dir, args.budget_limit)
        if remaining <= 0:
            print(f"Daily budget exhausted ({args.budget_limit} tokens). "
                  f"Use --skip-budget-check to override.")
            return 1
        print(f"Budget remaining today: ~{remaining:,} tokens")

    # --- Load reports ---
    objective = read_objective()
    rlm_text = load_report_text(args.reports_dir / "rlm" / "rlm_head_engineer.md")
    context = build_context_payload(args.reports_dir)
    objective_inference = load_report_json(
        args.reports_dir / "rlm" / "objective_inference.json")

    # --- Build prompts ---
    synthesis_prompt = build_synthesis_prompt(context, objective, rlm_text)
    # Save prompt for audit even in dry-run
    (args.out_dir / "synthesis_prompt.txt").write_text(
        synthesis_prompt + "\n", encoding="utf-8")

    synthesis_tokens = estimate_tokens(synthesis_prompt)
    print(f"Synthesis prompt: ~{synthesis_tokens:,} tokens ({len(synthesis_prompt):,} chars)")

    if args.dry_run:
        print("\n[DRY RUN] Skipping API calls.")
        print(f"Estimated synthesis input: ~{synthesis_tokens:,} tokens")
        print(f"Estimated synthesis output: ~8,000 tokens")
        if not args.synthesis_only:
            print(f"Estimated verdict input: ~20,000 tokens")
            print(f"Estimated verdict output: ~4,000 tokens")
            total = synthesis_tokens + 8_000 + 20_000 + 4_000
        else:
            total = synthesis_tokens + 8_000
        print(f"Estimated total per run: ~{total:,} tokens")
        print(f"Wrote {args.out_dir / 'synthesis_prompt.txt'}")
        return 0

    # --- Call 1: Synthesis ---
    token_log: list[dict] = []

    print(f"Calling {args.model} for synthesis...")
    synthesis_md = call_gpt5mini(args.model, synthesis_prompt)
    track_token_usage("synthesis", synthesis_prompt, synthesis_md, token_log)

    (args.out_dir / "meta_synthesis.md").write_text(
        synthesis_md.strip() + "\n", encoding="utf-8")
    print(f"Wrote {args.out_dir / 'meta_synthesis.md'}")

    # --- Call 2: Verdict ---
    verdict = {}
    if not args.synthesis_only:
        verdict_prompt = build_verdict_prompt(synthesis_md, objective_inference)
        (args.out_dir / "verdict_prompt.txt").write_text(
            verdict_prompt + "\n", encoding="utf-8")

        verdict_tokens = estimate_tokens(verdict_prompt)
        print(f"Verdict prompt: ~{verdict_tokens:,} tokens ({len(verdict_prompt):,} chars)")
        print(f"Calling {args.model} for verdict...")

        verdict_raw = call_gpt5mini(args.model, verdict_prompt)
        track_token_usage("verdict", verdict_prompt, verdict_raw, token_log)
        verdict = parse_json_fallback(verdict_raw)

        (args.out_dir / "meta_verdict.json").write_text(
            json.dumps(verdict, indent=2) + "\n", encoding="utf-8")
        print(f"Wrote {args.out_dir / 'meta_verdict.json'}")

    # --- Combined report ---
    combined = render_combined_report(synthesis_md, verdict)
    (args.out_dir / "meta_analysis.md").write_text(combined, encoding="utf-8")
    print(f"Wrote {args.out_dir / 'meta_analysis.md'}")

    # --- Token usage (accumulate with prior entries from today) ---
    usage_path = args.out_dir / "token_usage.json"
    prior_entries: list[dict] = []
    if usage_path.exists():
        try:
            prior = json.loads(usage_path.read_text(encoding="utf-8"))
            prior_entries = prior.get("entries", [])
        except (json.JSONDecodeError, OSError):
            pass

    all_entries = prior_entries + token_log
    total_today = sum(e.get("total_tokens_est", 0) for e in all_entries
                      if e.get("ts", "")[:10] == datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    usage_data = {
        "updated_at": utc_iso(datetime.now(timezone.utc)),
        "daily_budget": args.budget_limit,
        "total_tokens_today": total_today,
        "remaining_today": args.budget_limit - total_today,
        "entries": all_entries,
    }
    usage_path.write_text(json.dumps(usage_data, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {usage_path}")
    print(f"Tokens used this run: ~{sum(e['total_tokens_est'] for e in token_log):,}")
    print(f"Tokens used today: ~{total_today:,} / {args.budget_limit:,}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
