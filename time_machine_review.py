#!/usr/bin/env python3
"""Time-machine review: intended outcomes, divergence signals, and lazy-prompt analysis.

Outputs:
- reports/time_machine/time_machine_review.json
- reports/time_machine/time_machine_review.md
- reports/time_machine/gpt5mini_responsibility.md
"""

from __future__ import annotations

import argparse
import json
import os
import re
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import median

from lib.config import CLAUDE_SESSION_DIRS, REPOS
from lib.data_loaders import Prompt, load_claude_prompts, load_codex_prompts, load_commits, utc_iso
from lib.metrics import nearest_prompt_lags_hours, rework_ratio

INTENDED_OUTCOMES = [
    "Sustain coherent evolution of 4D-bot and SICM without losing original architecture intent.",
    "Preserve deterministic, auditable behavior while increasing capability.",
    "Keep development legible so you can replay decisions and identify divergence points.",
    "Prioritize high-leverage changes over churn-heavy, low-impact iteration.",
    "Maintain strong prompt-to-implementation alignment with explicit constraints and acceptance checks.",
    "Reduce accidental complexity by catching unclear instructions before they propagate into code.",
    "Create ML-ready historical traces for forecasting churn, coupling risk, and delivery quality.",
]

VAGUE_PATTERNS = [
    r"\bdo it\b",
    r"\bfix it\b",
    r"\bhandle it\b",
    r"\bmake it\b",
    r"\bsame as before\b",
    r"\byou know\b",
    r"\bas discussed\b",
    r"\bcontinue\b",
    r"\bjust\b",
    r"\bwhatever\b",
    r"\bthat thing\b",
    r"\bthis part\b",
]

SUCCESS_CUE_RE = re.compile(r"\b(test|assert|verify|acceptance|should|must|output|report|criteria|pass|fail)\b", re.I)
TARGET_CUE_RE = re.compile(r"(/[\w\-.]+)+|\b\w+\.py\b|\b\w+\.md\b|\b\w+\.json\b")


def _has_target_signal(text: str) -> bool:
    return bool(TARGET_CUE_RE.search(text))


def _has_success_signal(text: str) -> bool:
    return bool(SUCCESS_CUE_RE.search(text))


def detect_lazy_prompt(prompt: str, context_prompts: list[str]) -> tuple[int, list[str]]:
    reasons: list[str] = []
    score = 0

    text = prompt.strip()
    words = text.split()
    context_text = "\n".join(context_prompts).strip()

    has_target_now = _has_target_signal(text)
    has_success_now = _has_success_signal(text)
    has_target_context = _has_target_signal(context_text)
    has_success_context = _has_success_signal(context_text)

    # Short prompts are not inherently bad; only flag when surrounding turns
    # do not supply scope or acceptance anchors.
    if len(words) < 8 and not (has_target_context or has_success_context):
        score += 1
        reasons.append("short_without_context")

    if not has_target_now and not has_target_context:
        score += 1
        reasons.append("no_explicit_target_multi_turn")

    if not has_success_now and not has_success_context:
        score += 1
        reasons.append("no_success_criteria_multi_turn")

    if any(re.search(pat, text, flags=re.I) for pat in VAGUE_PATTERNS):
        score += 1
        reasons.append("vague_reference")

    if text.lower().startswith(("do ", "fix ", "continue", "same ", "just ")) and not (
        has_target_context or has_success_context
    ):
        score += 1
        reasons.append("underspecified_imperative")

    return score, reasons


def enrich_prompts(prompts: list[Prompt]) -> list[dict]:
    prompts = sorted(prompts, key=lambda p: p.ts)
    rows: list[dict] = []
    prior_by_repo: dict[str, list[Prompt]] = defaultdict(list)
    prior_by_repo_session: dict[tuple[str, str], list[Prompt]] = defaultdict(list)

    for p in prompts:
        session_ctx: list[Prompt] = []
        if p.session_id:
            session_ctx = prior_by_repo_session.get((p.repo, p.session_id), [])
        repo_ctx = prior_by_repo.get(p.repo, [])

        # Session-first context (last 3 turns). If missing, fallback to repo stream.
        if session_ctx:
            context_prompts = [x.text for x in session_ctx[-3:]]
            context_scope = "session"
        else:
            context_prompts = [x.text for x in repo_ctx[-3:]]
            context_scope = "repo_fallback"

        score, reasons = detect_lazy_prompt(p.text, context_prompts)
        rows.append(
            {
                "repo": p.repo,
                "ts": p.ts,
                "source": p.source,
                "session_id": p.session_id,
                "text": p.text,
                "lazy_score": score,
                "lazy": score >= 3,
                "reasons": reasons,
                "context_scope": context_scope,
                "context_turns_considered": len(context_prompts),
            }
        )
        prior_by_repo[p.repo].append(p)
        if p.session_id:
            prior_by_repo_session[(p.repo, p.session_id)].append(p)
    return rows


def nearest_prompt_before_commit(commit_ts: datetime, repo_rows: list[dict]) -> dict | None:
    nearest = None
    for row in repo_rows:
        if row["ts"] > commit_ts:
            break
        nearest = row
    return nearest


def build_payload_range(start: datetime, end: datetime) -> dict:
    commits = []
    for name, path in REPOS.items():
        commits.extend(load_commits(name, path, start, end))
    commits.sort(key=lambda c: c.ts)

    prompts: list[Prompt] = []
    for name, sdir in CLAUDE_SESSION_DIRS.items():
        prompts.extend(load_claude_prompts(name, sdir, start, end))
    prompts.extend(load_codex_prompts(start, end))
    prompts.sort(key=lambda p: p.ts)

    prompt_rows = enrich_prompts(prompts)
    prompt_rows_by_repo: dict[str, list[dict]] = defaultdict(list)
    for row in prompt_rows:
        prompt_rows_by_repo[row["repo"]].append(row)
    for rows in prompt_rows_by_repo.values():
        rows.sort(key=lambda r: r["ts"])

    lags = nearest_prompt_lags_hours(commits, prompts)
    file_counter = Counter(file_path for c in commits for file_path in c.files)

    lazy_total = sum(1 for r in prompt_rows if r["lazy"])
    lazy_by_repo = Counter(r["repo"] for r in prompt_rows if r["lazy"])
    by_source: dict[str, list[dict]] = defaultdict(list)
    for row in prompt_rows:
        by_source[row["source"]].append(row)

    multi_turn_samples: list[dict] = []
    for source in sorted(by_source):
        rows = [x for x in by_source[source] if x["context_turns_considered"] >= 2]
        if not rows:
            rows = by_source[source]
        for r in rows[:15]:
            multi_turn_samples.append(
                {
                    "repo": r["repo"],
                    "ts": utc_iso(r["ts"]),
                    "source": r["source"],
                    "session_id": r.get("session_id"),
                    "text": r["text"],
                    "context_scope": r["context_scope"],
                    "context_turns_considered": r["context_turns_considered"],
                }
            )

    lazy_commit_links = []
    for c in commits:
        nearest = nearest_prompt_before_commit(c.ts, prompt_rows_by_repo.get(c.repo, []))
        if not nearest:
            continue
        lag_h = (c.ts - nearest["ts"]).total_seconds() / 3600.0
        if lag_h > 6:
            continue
        if nearest["lazy"]:
            lazy_commit_links.append(
                {
                    "repo": c.repo,
                    "sha": c.sha,
                    "ts": utc_iso(c.ts),
                    "subject": c.subject,
                    "lag_hours": round(lag_h, 3),
                    "prompt_text": nearest["text"],
                    "prompt_reasons": nearest["reasons"],
                    "files": c.files,
                    "insertions": c.insertions,
                    "deletions": c.deletions,
                }
            )

    payload = {
        "generated_at": utc_iso(datetime.now(timezone.utc)),
        "window": {
            "start": utc_iso(start),
            "end": utc_iso(end),
            "days": round((end - start).total_seconds() / 86400.0, 3),
        },
        "intended_outcomes": INTENDED_OUTCOMES,
        "stats": {
            "commits": len(commits),
            "prompts": len(prompt_rows),
            "lazy_prompts": lazy_total,
            "lazy_prompt_ratio": round((lazy_total / len(prompt_rows)) if prompt_rows else 0.0, 4),
            "median_prompt_to_commit_lag_hours": median(lags) if lags else None,
            "rework_ratio_7day": rework_ratio(commits, 7),
        },
        "top_churn_files": [{"file": f, "touches": n} for f, n in file_counter.most_common(20)],
        "lazy_prompt_breakdown": {
            "by_repo": dict(lazy_by_repo),
            "reason_counts": dict(Counter(reason for r in prompt_rows if r["lazy"] for reason in r["reasons"])),
            "context_scope_counts": dict(Counter(r["context_scope"] for r in prompt_rows)),
            "examples": [
                {
                    "repo": r["repo"],
                    "ts": utc_iso(r["ts"]),
                    "source": r["source"],
                    "text": r["text"],
                    "reasons": r["reasons"],
                    "context_scope": r["context_scope"],
                    "context_turns_considered": r["context_turns_considered"],
                }
                for r in [x for x in prompt_rows if x["lazy"]][:25]
            ],
        },
        "prompt_context_evidence": {
            "prompt_counts_by_source": dict(Counter(r["source"] for r in prompt_rows)),
            "multi_turn_samples": multi_turn_samples,
        },
        "lazy_prompt_commit_links": lazy_commit_links[:80],
    }
    return payload


def build_payload(days: int) -> dict:
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    payload = build_payload_range(start, end)
    payload["window"]["days"] = days
    return payload


def call_gpt5mini(payload: dict, model: str) -> str:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return "OPENAI_API_KEY not set; skipped GPT-5-mini responsibility analysis."

    prompt = {
        "role": "user",
        "content": [
            {
                "type": "input_text",
                "text": (
                    "You are a strict engineering reviewer. Given this analytics payload, produce:\n"
                    "1) A prioritized list of where system behavior diverged from intended outcomes.\n"
                    "2) Concrete responsibility assignment matrix with columns: outcome, owner, evidence, next action (2-week horizon).\n"
                    "3) A section called 'Lazy Prompting Signals' that identifies operator prompting habits likely causing divergence.\n"
                    "4) A section called 'Guardrails' with exact prompt templates to reduce ambiguity.\n"
                    "Use concise markdown.\n\nPayload:\n"
                    + json.dumps(payload, indent=2)
                ),
            }
        ],
    }

    req_body = {
        "model": model,
        "input": [prompt],
    }

    req = urllib.request.Request(
        "https://api.openai.com/v1/responses",
        data=json.dumps(req_body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            raw = resp.read().decode("utf-8")
        data = json.loads(raw)
    except urllib.error.HTTPError as exc:  # pragma: no cover
        try:
            detail = exc.read().decode("utf-8")
        except Exception:
            detail = ""
        return f"Failed to call {model}: HTTP {exc.code} {detail}".strip()
    except Exception as exc:  # pragma: no cover
        return f"Failed to call {model}: {exc}"

    if isinstance(data, dict) and data.get("output_text"):
        return str(data["output_text"])

    output = data.get("output", []) if isinstance(data, dict) else []
    chunks: list[str] = []
    for item in output:
        for content in item.get("content", []):
            text = content.get("text")
            if text:
                chunks.append(text)
    return "\n".join(chunks).strip() or f"{model} returned no text output."


def render_markdown(payload: dict, gpt_text: str) -> str:
    lines = [
        "# Time Machine Review",
        "",
        f"Window: {payload['window']['start']} -> {payload['window']['end']} ({payload['window']['days']} days)",
        "",
        "## Intended Outcomes",
        "",
    ]
    for idx, outcome in enumerate(payload["intended_outcomes"], start=1):
        lines.append(f"{idx}. {outcome}")

    s = payload["stats"]
    lines.extend(
        [
            "",
            "## Core Signals",
            "",
            f"- Commits: {s['commits']}",
            f"- Prompts: {s['prompts']}",
            f"- Lazy prompts: {s['lazy_prompts']} ({s['lazy_prompt_ratio']:.1%})",
            f"- Rework ratio (7-day): {s['rework_ratio_7day']:.1%}",
            f"- Median prompt->commit lag: {s['median_prompt_to_commit_lag_hours']}",
            "",
            "## Lazy Prompting Signals",
            "",
            "- Context model: each prompt is scored with prior turns (session-first, repo fallback).",
        ]
    )

    reasons = payload["lazy_prompt_breakdown"]["reason_counts"]
    for reason, count in sorted(reasons.items(), key=lambda x: x[1], reverse=True):
        lines.append(f"- {reason}: {count}")

    lines.extend(["", "## GPT-5-mini Responsibility Review", "", gpt_text, ""])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=35)
    parser.add_argument("--out-dir", type=Path, default=Path("reports") / "time_machine")
    parser.add_argument("--model", type=str, default="gpt-5-mini")
    args = parser.parse_args()

    payload = build_payload(args.days)
    gpt_text = call_gpt5mini(payload, args.model)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    json_path = args.out_dir / "time_machine_review.json"
    md_path = args.out_dir / "time_machine_review.md"
    gpt_path = args.out_dir / "gpt5mini_responsibility.md"

    json_path.write_text(json.dumps(payload, indent=2) + "\n")
    gpt_path.write_text(gpt_text.strip() + "\n")
    md_path.write_text(render_markdown(payload, gpt_text))

    print(f"Wrote {json_path}")
    print(f"Wrote {gpt_path}")
    print(f"Wrote {md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
