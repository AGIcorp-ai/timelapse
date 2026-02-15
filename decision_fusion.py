#!/usr/bin/env python3
"""Decision fusion pipeline: cross-reference meta-analysis and agent critique.

Same two-call architecture as siblings:
  1. Synthesis -- decision architect cross-referencing both verdicts (~8k input, ~6k output)
  2. Verdict  -- machine-readable fused assessment (~12k input, ~6k output)

Budget: 1,000,000 GPT-5.2 tokens per 24 hours (~62 runs/day headroom).
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from rlm_harness import call_gpt5mini, parse_json_fallback, read_objective, utc_iso


# ---------------------------------------------------------------------------
# Input loaders
# ---------------------------------------------------------------------------

def load_json(path: Path) -> dict:
    """Load a JSON file, returning empty dict on failure."""
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return {}


def load_fusion_inputs(reports_dir: Path) -> tuple[dict, dict, dict]:
    """Load the two verdict JSONs and objective inference.

    Returns (meta_verdict, critique_verdict, objective_inference).
    Raises SystemExit if either verdict is missing -- fusion without
    both inputs is meaningless.
    """
    meta_path = reports_dir / "meta" / "meta_verdict.json"
    critique_path = reports_dir / "agent_critique" / "agent_critique_verdict.json"
    inference_path = reports_dir / "rlm" / "objective_inference.json"

    missing = []
    if not meta_path.exists():
        missing.append(str(meta_path))
    if not critique_path.exists():
        missing.append(str(critique_path))

    if missing:
        print(f"ERROR: Required verdict file(s) missing:\n  " + "\n  ".join(missing))
        print("Run meta_analysis.py and agent_critique.py first.")
        sys.exit(1)

    meta_verdict = load_json(meta_path)
    critique_verdict = load_json(critique_path)
    objective_inference = load_json(inference_path)

    return meta_verdict, critique_verdict, objective_inference


# ---------------------------------------------------------------------------
# Prompt builders
# ---------------------------------------------------------------------------

def build_synthesis_prompt(
    meta_verdict: dict,
    critique_verdict: dict,
    objective_inference: dict,
    objective: str,
) -> str:
    """Call 1: decision architect cross-referencing both verdicts."""
    meta_json = json.dumps(meta_verdict, indent=2)
    critique_json = json.dumps(critique_verdict, indent=2)
    inference_json = json.dumps(objective_inference, indent=2)

    return (
        "You are a decision architect. You do NOT re-analyze raw data. Your sole job "
        "is to cross-reference two pre-digested verdict documents -- one from a senior "
        "staff engineer (meta-analysis) and one from a performance reviewer (agent "
        "critique) -- to produce conflict resolution and execution sequencing.\n\n"
        "The meta-analysis tells the user WHAT to build, fix, and ship.\n"
        "The agent critique tells the user WHERE the AI coding agent fails.\n"
        "Your job: determine whether the agent CAN execute the engineering plan, and "
        "if not, how to decompose or constrain the work so it can.\n\n"
        f"Primary objective:\n{objective}\n\n"
        f"Objective inference:\n{inference_json}\n\n"
        f"Meta-analysis verdict (engineering plan):\n{meta_json}\n\n"
        f"Agent critique verdict (agent performance):\n{critique_json}\n\n"
        "Produce a thorough markdown report with these exact sections:\n\n"
        "## 1. Readiness Assessment\n"
        "Can the agent execute the top engineering work from the meta-analysis? "
        "Cross-reference the highest-priority actions against the agent's failure "
        "patterns, capability gaps, and stability scores. Give a clear ready/guarded/"
        "blocked verdict with reasoning.\n\n"
        "## 2. Action-by-Action Risk Overlay\n"
        "For each priority action from the meta-analysis, annotate it with:\n"
        "- Which agent failure patterns apply to this action\n"
        "- Which capability gaps could block execution\n"
        "- Which guardrails must be active before attempting\n"
        "- Specific execution notes (decomposition, constraints, human checkpoints)\n"
        "Rate each action's agent-risk as clear/guarded/blocked.\n\n"
        "## 3. Compound Risks\n"
        "Identify cases where a meta-analysis risk and an agent failure pattern "
        "multiply each other. For example, if scope creep is a project risk AND the "
        "agent has poor clarification-seeking, these compound. Explain why they "
        "multiply and propose combined mitigations.\n\n"
        "## 4. Blocked Work\n"
        "Identify meta-analysis actions that CANNOT proceed as-is due to agent "
        "capability gaps. For each, specify whether the resolution is: human-only, "
        "decompose into agent-safe sub-steps, defer, or workaround.\n\n"
        "## 5. Tension Resolution\n"
        "Identify cases where recommended guardrails from the critique directly "
        "constrain high-leverage actions from the meta-analysis. For each tension, "
        "explain which side wins and why.\n\n"
        "## 6. Execution Sequence\n"
        "Produce a phased execution plan that accounts for agent limitations. "
        "Each phase should list: actions to take, preconditions (guardrails/gaps "
        "to address first), and expected stability impact (which scores improve).\n"
    )


def build_verdict_prompt(
    synthesis_md: str,
    meta_verdict: dict,
    critique_verdict: dict,
    objective_inference: dict,
) -> str:
    """Call 2: machine-readable fused verdict."""
    meta_json = json.dumps(meta_verdict, indent=2)
    critique_json = json.dumps(critique_verdict, indent=2)
    inference_json = json.dumps(objective_inference, indent=2)

    return (
        "You are a machine-output formatter. Given the decision fusion synthesis "
        "below and the source verdict data, produce ONLY valid JSON (no markdown, "
        "no explanation) with this exact schema:\n\n"
        "{\n"
        '  "fused_verdict": {\n'
        '    "readiness": "ready | guarded | blocked",\n'
        '    "confidence": 0.0,\n'
        '    "headline": "one-sentence: can the agent execute the highest-priority engineering work?",\n'
        '    "meta_trajectory": "echo trajectory from meta verdict",\n'
        '    "agent_competence": "echo competence from critique verdict"\n'
        "  },\n"
        '  "fused_actions": [\n'
        "    {\n"
        '      "rank": 1,\n'
        '      "action": "from meta priority_actions",\n'
        '      "repo": "which repo",\n'
        '      "effort": "low | medium | high",\n'
        '      "agent_risk": "clear | guarded | blocked",\n'
        '      "matching_failures": ["failure pattern names that apply"],\n'
        '      "matching_gaps": ["capability gaps that apply"],\n'
        '      "guardrails_required": ["guardrails to activate"],\n'
        '      "execution_notes": "how to decompose/constrain given agent limitations"\n'
        "    }\n"
        "  ],\n"
        '  "compound_risks": [\n'
        "    {\n"
        '      "meta_risk": "from meta risks[]",\n'
        '      "agent_failure": "from critique agent_failures[]",\n'
        '      "compound_severity": "low | medium | high | critical",\n'
        '      "explanation": "why these multiply",\n'
        '      "mitigation": "combined strategy"\n'
        "    }\n"
        "  ],\n"
        '  "blocked_actions": [\n'
        "    {\n"
        '      "action": "meta action that cannot proceed as-is",\n'
        '      "blocking_gap": "capability_gap that blocks it",\n'
        '      "resolution": "human-only | decompose | defer | workaround",\n'
        '      "decomposition": "agent-safe sub-steps if applicable"\n'
        "    }\n"
        "  ],\n"
        '  "tension_points": [\n'
        "    {\n"
        '      "guardrail": "from critique",\n'
        '      "constrained_action": "from meta",\n'
        '      "tension": "how they conflict",\n'
        '      "resolution": "which wins and why"\n'
        "    }\n"
        "  ],\n"
        '  "stability_overlay": {\n'
        '    "dimensions_below_threshold": ["scores < 0.5"],\n'
        '    "affected_actions": ["actions touching low-stability areas"],\n'
        '    "recommended_focus": "which dimension to improve first"\n'
        "  },\n"
        '  "execution_sequence": [\n'
        "    {\n"
        '      "phase": 1,\n'
        '      "actions": ["action references"],\n'
        '      "preconditions": "guardrails/gaps to address first",\n'
        '      "expected_stability_impact": "which scores improve"\n'
        "    }\n"
        "  ],\n"
        '  "next_review_trigger": "condition"\n'
        "}\n\n"
        "Populate fused_actions from all meta priority_actions, cross-referenced "
        "with critique data. Populate compound_risks with 2-5 entries. "
        "blocked_actions may be empty if no actions are fully blocked. "
        "tension_points should have 2-4 entries. execution_sequence should have "
        "2-4 phases.\n\n"
        f"Meta-analysis verdict:\n{meta_json}\n\n"
        f"Agent critique verdict:\n{critique_json}\n\n"
        f"Objective inference:\n{inference_json}\n\n"
        f"Decision fusion synthesis:\n{synthesis_md}\n"
    )


# ---------------------------------------------------------------------------
# Token tracking (same pattern as agent_critique.py)
# ---------------------------------------------------------------------------

def estimate_tokens(text: str) -> int:
    return len(text) // 4


def track_token_usage(call_name: str, prompt: str, response: str, log: list) -> None:
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
    usage_path = out_dir / "token_usage.json"
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    used = 0

    if usage_path.exists():
        try:
            data = json.loads(usage_path.read_text(encoding="utf-8"))
            for entry in data.get("entries", []):
                if entry.get("ts", "")[:10] == today:
                    used += entry.get("total_tokens_est", 0)
        except (json.JSONDecodeError, OSError):
            pass

    return limit - used


# ---------------------------------------------------------------------------
# Combined report renderer
# ---------------------------------------------------------------------------

def render_combined_report(synthesis_md: str, verdict: dict) -> str:
    lines = [
        "# Decision Fusion Report",
        "",
        f"Generated: {utc_iso(datetime.now(timezone.utc))}",
        "",
        "---",
        "",
        synthesis_md.strip(),
        "",
        "---",
        "",
        "## Machine-Readable Fused Verdict",
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
    parser.add_argument("--out-dir", type=Path, default=Path("reports") / "decision_fusion")
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

    # --- Load inputs ---
    objective = read_objective()
    meta_verdict, critique_verdict, objective_inference = load_fusion_inputs(args.reports_dir)

    # --- Build prompts ---
    synthesis_prompt = build_synthesis_prompt(
        meta_verdict, critique_verdict, objective_inference, objective)
    (args.out_dir / "synthesis_prompt.txt").write_text(
        synthesis_prompt + "\n", encoding="utf-8")

    synthesis_tokens = estimate_tokens(synthesis_prompt)
    print(f"Synthesis prompt: ~{synthesis_tokens:,} tokens ({len(synthesis_prompt):,} chars)")

    if args.dry_run:
        print("\n[DRY RUN] Skipping API calls.")
        print(f"Estimated synthesis input: ~{synthesis_tokens:,} tokens")
        print(f"Estimated synthesis output: ~6,000 tokens")
        if not args.synthesis_only:
            print(f"Estimated verdict input: ~12,000 tokens")
            print(f"Estimated verdict output: ~6,000 tokens")
            total = synthesis_tokens + 6_000 + 12_000 + 6_000
        else:
            total = synthesis_tokens + 6_000
        print(f"Estimated total per run: ~{total:,} tokens")
        print(f"Wrote {args.out_dir / 'synthesis_prompt.txt'}")
        return 0

    # --- Call 1: Synthesis ---
    token_log: list[dict] = []

    print(f"Calling {args.model} for synthesis...")
    synthesis_md = call_gpt5mini(args.model, synthesis_prompt)
    track_token_usage("synthesis", synthesis_prompt, synthesis_md, token_log)

    (args.out_dir / "decision_fusion_synthesis.md").write_text(
        synthesis_md.strip() + "\n", encoding="utf-8")
    print(f"Wrote {args.out_dir / 'decision_fusion_synthesis.md'}")

    # --- Call 2: Verdict ---
    verdict = {}
    if not args.synthesis_only:
        verdict_prompt = build_verdict_prompt(
            synthesis_md, meta_verdict, critique_verdict, objective_inference)
        (args.out_dir / "verdict_prompt.txt").write_text(
            verdict_prompt + "\n", encoding="utf-8")

        verdict_tokens = estimate_tokens(verdict_prompt)
        print(f"Verdict prompt: ~{verdict_tokens:,} tokens ({len(verdict_prompt):,} chars)")
        print(f"Calling {args.model} for verdict...")

        verdict_raw = call_gpt5mini(args.model, verdict_prompt)
        track_token_usage("verdict", verdict_prompt, verdict_raw, token_log)
        verdict = parse_json_fallback(verdict_raw)

        (args.out_dir / "decision_fusion_verdict.json").write_text(
            json.dumps(verdict, indent=2) + "\n", encoding="utf-8")
        print(f"Wrote {args.out_dir / 'decision_fusion_verdict.json'}")

    # --- Combined report ---
    combined = render_combined_report(synthesis_md, verdict)
    (args.out_dir / "decision_fusion.md").write_text(combined, encoding="utf-8")
    print(f"Wrote {args.out_dir / 'decision_fusion.md'}")

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
