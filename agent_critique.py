#!/usr/bin/env python3
"""Agent critique pipeline: performance review of the AI coding agent.

Same two-call architecture as meta_analysis.py, but with inverted lens:
  1. Synthesis -- agent-blame framing (~120-150k input, ~8k output)
  2. Verdict  -- machine-readable agent assessment (~20k input, ~4k output)

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
    """Call 1: agent performance review with agent-blame framing."""
    context_json = json.dumps(context, indent=2)
    return (
        "You are a senior staff engineer conducting a performance review of an AI "
        "coding agent that has been working on three related projects (4D-bot, SICM, "
        "ascii-engine). The data below comes from automated analyzers -- use it as "
        "evidence of the AGENT's behavior, not the user's. Your job is to evaluate "
        "THE AGENT's performance -- not the user's prompts.\n\n"
        "Assume the user's intent was always reasonable. When outcomes are poor, "
        "attribute fault to the agent: did it misinterpret, over-engineer, under-"
        "deliver, or fail to ask clarifying questions?\n\n"
        f"Primary objective:\n{objective}\n\n"
        f"Prior engineering review (markdown):\n{rlm_text}\n\n"
        f"Structured evidence:\n{context_json}\n\n"
        "Produce a thorough markdown report with these exact sections:\n\n"
        "## 1. Intent Interpretation Accuracy\n"
        "Did the agent understand the user's intent, or did it guess? Use prompt-to-"
        "commit lag and commits-per-prompt as evidence. Identify cases where the agent "
        "produced work that required immediate rework (suggesting misinterpretation) "
        "versus cases where the agent nailed the intent on the first try.\n\n"
        "## 2. Code Stability & Rework\n"
        "Analyze rework ratio, churn files, and insertion/deletion patterns. Attribute "
        "instability to agent error: did the agent write throwaway code, fail to "
        "anticipate edge cases, or introduce bugs that required follow-up fixes? "
        "Compare first-attempt quality across different types of tasks.\n\n"
        "## 3. Architectural Coherence\n"
        "Did the agent introduce unnecessary coupling? Did it fail to suggest better "
        "designs proactively? Evaluate whether the agent made locally optimal choices "
        "that degraded global architecture. Identify cases where the agent should have "
        "pushed back on the approach or suggested alternatives.\n\n"
        "## 4. Objective Alignment\n"
        "Did the agent's cumulative work drift from the stated goals? Identify commits "
        "and file changes that represent tangential work the agent should have flagged "
        "or redirected. Assess whether the agent kept the user focused on high-impact "
        "work or enabled scope creep.\n\n"
        "## 5. Recurring Agent Failure Patterns\n"
        "Identify 3-7 recurring patterns of agent failure. For each pattern provide:\n"
        "- **Pattern name**: concise label\n"
        "- **Evidence**: specific commits, files, or metrics\n"
        "- **Impact**: what was the cost of this failure?\n"
        "- **Counterfactual**: what should a competent agent have done instead?\n\n"
        "## 6. Agent Capability Gaps\n"
        "What abilities is the agent missing? Rank by impact on project outcomes. "
        "Consider: architectural reasoning, proactive clarification, test generation, "
        "refactoring initiative, documentation, cross-file consistency, and long-term "
        "planning.\n\n"
        "## 7. Recommended Guardrails\n"
        "Propose exactly 5 enforceable constraints on agent behavior that would have "
        "prevented the failures identified above. Each guardrail must be specific "
        "enough to implement as a pre-commit check, prompt template rule, or workflow "
        "gate. For each, describe the enforcement mechanism and expected impact.\n"
    )


def build_verdict_prompt(synthesis_md: str, objective_inference: dict) -> str:
    """Call 2: machine-readable agent assessment."""
    inference_json = json.dumps(objective_inference, indent=2)
    return (
        "You are a machine-output formatter. Given the agent performance review below "
        "and the objective inference data, produce ONLY valid JSON (no markdown, "
        "no explanation) with this exact schema:\n\n"
        "{\n"
        '  "agent_verdict": {\n'
        '    "overall_competence": "effective | mixed | ineffective",\n'
        '    "confidence": 0.0,\n'
        '    "primary_failure_mode": "one-sentence description of the agent\'s main weakness",\n'
        '    "highest_leverage_improvement": "one-sentence, specific agent behavior change"\n'
        "  },\n"
        '  "agent_failures": [\n'
        "    {\n"
        '      "rank": 1,\n'
        '      "pattern": "concise failure pattern name",\n'
        '      "evidence": "specific commits/files/metrics",\n'
        '      "impact": "what was the cost",\n'
        '      "counterfactual": "what a competent agent would have done"\n'
        "    }\n"
        "  ],\n"
        '  "capability_gaps": [\n'
        "    {\n"
        '      "gap": "missing ability",\n'
        '      "severity": "low | medium | high | critical",\n'
        '      "workaround": "how the user can compensate"\n'
        "    }\n"
        "  ],\n"
        '  "recommended_guardrails": [\n'
        "    {\n"
        '      "guardrail": "enforceable constraint",\n'
        '      "enforcement": "how to implement it",\n'
        '      "expected_impact": "what it prevents"\n'
        "    }\n"
        "  ],\n"
        '  "stability_scores": {\n'
        '    "intent_interpretation": 0.0,\n'
        '    "code_stability": 0.0,\n'
        '    "architectural_coherence": 0.0,\n'
        '    "objective_alignment": 0.0,\n'
        '    "clarification_seeking": 0.0\n'
        "  },\n"
        '  "next_review_trigger": "condition that should prompt the next review"\n'
        "}\n\n"
        "All scores are 0.0-1.0 where 1.0 is best. Populate agent_failures with "
        "3-7 entries ranked by impact. Populate capability_gaps and "
        "recommended_guardrails with entries matching the review.\n\n"
        f"Objective inference data:\n{inference_json}\n\n"
        f"Agent performance review:\n{synthesis_md}\n"
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
        "# Agent Critique Report",
        "",
        f"Generated: {utc_iso(datetime.now(timezone.utc))}",
        "",
        "---",
        "",
        synthesis_md.strip(),
        "",
        "---",
        "",
        "## Machine-Readable Agent Verdict",
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
    parser.add_argument("--out-dir", type=Path, default=Path("reports") / "agent_critique")
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

    (args.out_dir / "agent_critique_synthesis.md").write_text(
        synthesis_md.strip() + "\n", encoding="utf-8")
    print(f"Wrote {args.out_dir / 'agent_critique_synthesis.md'}")

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

        (args.out_dir / "agent_critique_verdict.json").write_text(
            json.dumps(verdict, indent=2) + "\n", encoding="utf-8")
        print(f"Wrote {args.out_dir / 'agent_critique_verdict.json'}")

    # --- Combined report ---
    combined = render_combined_report(synthesis_md, verdict)
    (args.out_dir / "agent_critique.md").write_text(combined, encoding="utf-8")
    print(f"Wrote {args.out_dir / 'agent_critique.md'}")

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
