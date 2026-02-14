#!/usr/bin/env python3
"""Infer and plot primary user objective over time."""

from __future__ import annotations

import argparse
import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

from lib.config import CLAUDE_SESSION_DIRS, EXTRA_CLAUDE_SESSION_DIRS, REPOS
from lib.data_loaders import load_claude_prompts, load_codex_prompts, load_commits
from time_machine_review import build_payload_range


def utc_iso(ts: datetime) -> str:
    return ts.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def call_model(model: str, prompt: str) -> str:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return '{"inferred_primary_goal":"OPENAI_API_KEY missing","confidence":0.0,"evidence":[]}'

    req_body = {
        "model": model,
        "input": [{"role": "user", "content": [{"type": "input_text", "text": prompt}]}],
    }
    req = urllib.request.Request(
        "https://api.openai.com/v1/responses",
        data=json.dumps(req_body).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            raw = resp.read().decode("utf-8")
        data = json.loads(raw)
    except urllib.error.HTTPError as exc:  # pragma: no cover
        try:
            detail = exc.read().decode("utf-8")
        except Exception:
            detail = ""
        return json.dumps({"inferred_primary_goal": f"HTTP {exc.code}: {detail}", "confidence": 0.0, "evidence": []})
    except Exception as exc:  # pragma: no cover
        return json.dumps({"inferred_primary_goal": f"error: {exc}", "confidence": 0.0, "evidence": []})

    if isinstance(data, dict) and data.get("output_text"):
        return str(data["output_text"]).strip()

    chunks: list[str] = []
    for item in data.get("output", []):
        for content in item.get("content", []):
            text = content.get("text")
            if text:
                chunks.append(text)
    return "\n".join(chunks).strip()


def parse_json(raw: str) -> dict:
    try:
        return json.loads(raw)
    except Exception:
        start = raw.find("{")
        end = raw.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(raw[start : end + 1])
            except Exception:
                pass
    return {"inferred_primary_goal": raw[:400], "confidence": 0.0, "evidence": []}


def summarize_payload(payload: dict) -> dict:
    return {
        "window": payload["window"],
        "stats": payload["stats"],
        "top_churn_files": payload["top_churn_files"][:5],
        "lazy_reason_counts": payload["lazy_prompt_breakdown"]["reason_counts"],
        "lazy_context_scopes": payload["lazy_prompt_breakdown"].get("context_scope_counts", {}),
        "prompt_context_evidence": payload.get("prompt_context_evidence", {}),
        "lazy_prompt_commit_links": payload["lazy_prompt_commit_links"][:8],
    }


def infer_objective_for_window(model: str, payload: dict) -> dict:
    compact = summarize_payload(payload)
    prompt = (
        "Infer the user's primary objective for this time window from evidence.\n"
        "Use multi-turn interpretation: short prompts can be good when context is strong.\n"
        "Use prompt_context_evidence to account for both Claude and Codex conversation streams.\n"
        "Return JSON only:\n"
        '{'
        '"inferred_primary_goal":"one sentence",'
        '"confidence":0.0,'
        '"evidence":["short bullet","short bullet"],'
        '"execution_gap":"one sentence on where agent was not succinct/precise"'
        "}\n\n"
        "Payload:\n"
        + json.dumps(compact, indent=2)
    )
    raw = call_model(model, prompt)
    row = parse_json(raw)
    row["window"] = payload["window"]
    row["stats"] = payload["stats"]
    return row


def render_markdown(rows: list[dict], days: int, window_days: int, step_days: int) -> str:
    lines = [
        "# Primary Objective Timeline",
        "",
        f"Range: last {days} days | window={window_days}d | step={step_days}d",
        "",
        "## Chronological Objectives",
        "",
    ]
    for i, r in enumerate(rows, start=1):
        w = r["window"]
        goal = r.get("inferred_primary_goal", "").strip().replace("\n", " ")
        conf = r.get("confidence", 0.0)
        lines.append(f"{i}. {w['start']} -> {w['end']}")
        lines.append(f"   Primary user objective: {goal}")
        lines.append(f"   Confidence: {conf}")
        gap = r.get("execution_gap")
        if gap:
            lines.append(f"   Execution gap: {gap}")
    return "\n".join(lines) + "\n"


def render_html(rows: list[dict], out_md: str) -> str:
    points = []
    n = max(1, len(rows))
    width = 960
    height = 280
    pad = 40
    for i, r in enumerate(rows):
        x = pad + (i * (width - 2 * pad) / max(1, n - 1))
        y = height - pad - (max(0.0, min(1.0, float(r.get("confidence", 0.0)))) * (height - 2 * pad))
        points.append((x, y, r))
    polyline = " ".join(f"{x:.1f},{y:.1f}" for x, y, _ in points)
    items = []
    for r in rows:
        items.append(
            "<li><strong>"
            + r["window"]["start"][:10]
            + " -> "
            + r["window"]["end"][:10]
            + "</strong> — <code>Primary user objective:</code> "
            + str(r.get("inferred_primary_goal", "")).replace("<", "&lt;")
            + "</li>"
        )
    return f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <title>Primary Objective Timeline</title>
  <style>
    body {{ font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, Arial; margin: 24px; color: #111; }}
    .card {{ border: 1px solid #ddd; border-radius: 12px; padding: 16px; margin-bottom: 16px; }}
    svg {{ width: 100%; max-width: {width}px; height: auto; }}
    code {{ background: #f4f4f4; padding: 1px 4px; border-radius: 4px; }}
  </style>
</head>
<body>
  <h1>Primary Objective Timeline</h1>
  <div class="card">
    <h2>Confidence Over Time</h2>
    <svg viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg">
      <rect x="0" y="0" width="{width}" height="{height}" fill="#fff" stroke="#ddd"/>
      <line x1="{pad}" y1="{height-pad}" x2="{width-pad}" y2="{height-pad}" stroke="#999"/>
      <line x1="{pad}" y1="{pad}" x2="{pad}" y2="{height-pad}" stroke="#999"/>
      <polyline fill="none" stroke="#0f766e" stroke-width="3" points="{polyline}" />
      {''.join(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4" fill="#0f766e" />' for x,y,_ in points)}
    </svg>
  </div>
  <div class="card">
    <h2>Chronological List</h2>
    <ol>
      {''.join(items)}
    </ol>
  </div>
  <div class="card">
    <h2>Markdown Source</h2>
    <pre>{out_md.replace('<', '&lt;')}</pre>
  </div>
</body>
</html>
"""


def detect_full_history_start(end: datetime) -> datetime:
    seed = datetime(2000, 1, 1, tzinfo=timezone.utc)
    starts: list[datetime] = []

    for repo, repo_path in REPOS.items():
        commits = load_commits(repo, repo_path, seed, end)
        if commits:
            starts.append(min(c.ts for c in commits))

    for repo, session_dir in CLAUDE_SESSION_DIRS.items():
        prompts = load_claude_prompts(repo, session_dir, seed, end)
        if prompts:
            starts.append(min(p.ts for p in prompts))
    for repo, session_dirs in EXTRA_CLAUDE_SESSION_DIRS.items():
        for d in session_dirs:
            prompts = load_claude_prompts(repo, d, seed, end)
            if prompts:
                starts.append(min(p.ts for p in prompts))

    codex_prompts = load_codex_prompts(seed, end)
    if codex_prompts:
        starts.append(min(p.ts for p in codex_prompts))

    if not starts:
        return end - timedelta(days=35)
    return min(starts)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=35)
    parser.add_argument("--full-history", action="store_true", help="auto-span from earliest commit/prompt")
    parser.add_argument("--window-days", type=int, default=7)
    parser.add_argument("--step-days", type=int, default=7)
    parser.add_argument("--model", type=str, default="gpt-5-mini")
    parser.add_argument("--out-dir", type=Path, default=Path("reports") / "rlm")
    args = parser.parse_args()

    end = datetime.now(timezone.utc)
    if args.full_history:
        start = detect_full_history_start(end)
        days = max(1, int((end - start).total_seconds() / 86400))
    else:
        start = end - timedelta(days=args.days)
        days = args.days

    rows: list[dict] = []
    cursor = start + timedelta(days=args.window_days)
    while cursor <= end + timedelta(seconds=1):
        w_start = cursor - timedelta(days=args.window_days)
        payload = build_payload_range(w_start, cursor)
        row = infer_objective_for_window(args.model, payload)
        rows.append(row)
        cursor += timedelta(days=args.step_days)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    json_path = args.out_dir / "objective_timeline.json"
    md_path = args.out_dir / "objective_timeline.md"
    html_path = args.out_dir / "objective_timeline.html"

    md = render_markdown(rows, days, args.window_days, args.step_days)
    json_path.write_text(json.dumps({"generated_at": utc_iso(end), "rows": rows}, indent=2) + "\n", encoding="utf-8")
    md_path.write_text(md, encoding="utf-8")
    html_path.write_text(render_html(rows, md), encoding="utf-8")

    print(f"Wrote {json_path}")
    print(f"Wrote {md_path}")
    print(f"Wrote {html_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
