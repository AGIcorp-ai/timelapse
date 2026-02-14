#!/usr/bin/env python3
"""RLM harness: corpus assembly + head-engineer orchestration with GPT-5-mini."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from time_machine_review import build_payload


def utc_iso(ts: datetime) -> str:
    return ts.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def run(cmd: list[str], cwd: Path | None = None) -> str:
    result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        return ""
    return result.stdout


def _append_file(out: Path, file_path: Path, header: str) -> None:
    try:
        text = file_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return
    with out.open("a", encoding="utf-8") as fh:
        fh.write(f"\n## {header}\n\n")
        fh.write(text)
        if not text.endswith("\n"):
            fh.write("\n")


def _append_transcripts(out: Path) -> int:
    files = sorted(Path("transcripts").rglob("*.md")) if Path("transcripts").exists() else []
    for path in files:
        _append_file(out, path, f"FILE: {path.as_posix()}")
    return len(files)


def _append_timeline(out: Path) -> bool:
    path = Path("timeline.md")
    if not path.exists():
        return False
    _append_file(out, path, "FILE: timeline.md")
    return True


def _append_repo_files(out: Path) -> int:
    tracked = [line.strip() for line in run(["git", "ls-files"]).splitlines() if line.strip()]
    count = 0
    for rel in sorted(tracked):
        path = Path(rel)
        if not path.exists() or path.is_dir():
            continue
        _append_file(out, path, f"FILE: {rel}")
        count += 1
    return count


def _append_commits(out: Path, include_patches: bool) -> int:
    fmt = "### COMMIT %H%nDate: %ad%nAuthor: %an <%ae>%n%n%s%n%b"
    cmd = ["git", "log", "--date=iso"]
    if include_patches:
        cmd.append("-p")
    else:
        cmd.append("--stat")
    cmd.append(f"--pretty=format:{fmt}")
    log_text = run(cmd)
    with out.open("a", encoding="utf-8") as fh:
        title = "Commits (patches)" if include_patches else "Commits"
        fh.write(f"\n## {title}\n\n")
        fh.write(log_text)
        if log_text and not log_text.endswith("\n"):
            fh.write("\n")
    return log_text.count("### COMMIT ")


def build_corpus_markdown(out: Path, include_patches: bool) -> dict:
    out.parent.mkdir(parents=True, exist_ok=True)
    title = "All Data (Forensic)" if include_patches else "All Data (Compact)"
    out.write_text(f"# {title}\n\nGenerated: {utc_iso(datetime.now(timezone.utc))}\n", encoding="utf-8")
    transcript_files = _append_transcripts(out)
    has_timeline = _append_timeline(out)
    repo_files = _append_repo_files(out)
    commits = _append_commits(out, include_patches)
    stats = compute_text_stats(out)
    stats.update(
        {
            "path": out.as_posix(),
            "mode": "forensic" if include_patches else "compact",
            "transcript_files": transcript_files,
            "timeline_included": has_timeline,
            "repo_files_included": repo_files,
            "commits_included": commits,
        }
    )
    return stats


def compute_text_stats(path: Path) -> dict:
    text = path.read_text(encoding="utf-8", errors="replace")
    byte_count = len(text.encode("utf-8"))
    word_count = len(re.findall(r"\S+", text))
    line_count = text.count("\n") + (0 if text.endswith("\n") else 1)
    token_est = round(len(text) / 4)
    return {
        "bytes": byte_count,
        "words": word_count,
        "lines": line_count,
        "token_estimate_char_div4": token_est,
    }


def read_objective() -> str:
    agents = Path("AGENTS.md")
    if agents.exists():
        first = agents.read_text(encoding="utf-8", errors="replace").splitlines()
        for line in first[:8]:
            clean = line.strip().lstrip("#").strip()
            if clean.lower().startswith("the primary objective of this repo is"):
                return clean
    return (
        "Collate conversations, code, and commits into auditable analysis that identifies "
        "divergence from intended outcomes and converts findings into executable engineering actions."
    )


def call_gpt5mini(model: str, prompt_text: str) -> str:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return "OPENAI_API_KEY not set; skipped GPT-5-mini head-engineer run."

    req_body = {
        "model": model,
        "input": [
            {
                "role": "user",
                "content": [{"type": "input_text", "text": prompt_text}],
            }
        ],
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


def build_prompt(
    objective: str,
    rlm_text: str,
    compact_stats: dict,
    forensic_stats: dict,
    time_machine_payload: dict,
) -> str:
    compact = json.dumps(compact_stats, indent=2)
    forensic = json.dumps(forensic_stats, indent=2)
    payload = json.dumps(time_machine_payload, indent=2)
    return (
        "You are acting as the head engineer for this repository.\n"
        "Primary objective:\n"
        f"{objective}\n\n"
        "Operating contract (RLM.md):\n"
        f"{rlm_text}\n\n"
        "Corpus volume stats:\n"
        f"Compact:\n{compact}\n\n"
        f"Forensic:\n{forensic}\n\n"
        "Time-machine payload:\n"
        f"{payload}\n\n"
        "Requirements:\n"
        "1) Use multi-turn reasoning: do not judge prompt quality from one turn.\n"
        "2) Produce a section 'Primary Objective Scorecard' with 3-7 measurable KPIs.\n"
        "3) Produce a section '14-Day Execution Plan' with concrete commands/scripts in this repo.\n"
        "4) Produce a section 'Divergence Checkpoints' mapping likely drift points to evidence.\n"
        "5) Produce a section 'Responsibility Matrix' with columns: outcome, owner, evidence, next action.\n"
        "6) Produce a section 'Operator Prompt Protocol' with short templates requiring 2+ turn context.\n"
        "7) End with a machine-readable JSON block under heading 'RLM_ACTIONS_JSON'.\n"
        "Keep output concise markdown."
    )


def build_inference_prompt(
    objective: str,
    time_machine_payload: dict,
    compact_stats: dict,
    forensic_stats: dict,
) -> str:
    payload = json.dumps(time_machine_payload, indent=2)
    return (
        "You are an evidence-first principal engineer.\n"
        "Infer the user's original primary goal from repository evidence, not from explicit labels alone.\n\n"
        "Given:\n"
        f"- Stated objective seed: {objective}\n"
        f"- Compact corpus stats: {json.dumps(compact_stats, indent=2)}\n"
        f"- Forensic corpus stats: {json.dumps(forensic_stats, indent=2)}\n"
        f"- Time-machine payload: {payload}\n\n"
        "Return JSON only with this exact schema:\n"
        "{\n"
        '  "inferred_primary_goal": "string (one sentence)",\n'
        '  "confidence": 0.0,\n'
        '  "evidence": ["string", "string"],\n'
        '  "succinct_execution_failures": [\n'
        '    {"failure": "string", "signal": "string", "why_it_hurts": "string", "fix": "string"}\n'
        "  ],\n"
        '  "succinct_execution_strengths": ["string"],\n'
        '  "next_7_day_focus": ["string", "string", "string"]\n'
        "}\n"
        "Rules:\n"
        "- Use multi-turn interpretation. Short prompts can indicate high shared context.\n"
        "- Treat succinctness as precision + minimal ambiguity, not length.\n"
        "- Tie every failure to a concrete signal from the payload.\n"
    )


def parse_json_fallback(raw_text: str) -> dict:
    text = raw_text.strip()
    if not text:
        return {}
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        snippet = text[start : end + 1]
        try:
            return json.loads(snippet)
        except json.JSONDecodeError:
            return {"raw_text": text}
    return {"raw_text": text}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=35)
    parser.add_argument("--model", type=str, default="gpt-5-mini")
    parser.add_argument("--out-dir", type=Path, default=Path("reports") / "rlm")
    args = parser.parse_args()

    data_dir = Path("reports") / "data_volume"
    compact_path = data_dir / "all_data_compact.md"
    forensic_path = data_dir / "all_data_forensic.md"

    compact_stats = build_corpus_markdown(compact_path, include_patches=False)
    forensic_stats = build_corpus_markdown(forensic_path, include_patches=True)

    objective = read_objective()
    rlm_text = Path("RLM.md").read_text(encoding="utf-8", errors="replace") if Path("RLM.md").exists() else ""
    payload = build_payload(args.days)
    prompt_text = build_prompt(objective, rlm_text, compact_stats, forensic_stats, payload)
    gpt_text = call_gpt5mini(args.model, prompt_text)
    inference_prompt = build_inference_prompt(objective, payload, compact_stats, forensic_stats)
    inference_raw = call_gpt5mini(args.model, inference_prompt)
    inference_json = parse_json_fallback(inference_raw)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    (args.out_dir / "data_volume.json").write_text(
        json.dumps(
            {
                "generated_at": utc_iso(datetime.now(timezone.utc)),
                "compact": compact_stats,
                "forensic": forensic_stats,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (args.out_dir / "rlm_head_engineer_prompt.txt").write_text(prompt_text + "\n", encoding="utf-8")
    (args.out_dir / "rlm_head_engineer.md").write_text(gpt_text.strip() + "\n", encoding="utf-8")
    (args.out_dir / "objective_inference_prompt.txt").write_text(inference_prompt + "\n", encoding="utf-8")
    (args.out_dir / "objective_inference.json").write_text(
        json.dumps(inference_json, indent=2) + "\n", encoding="utf-8"
    )
    (args.out_dir / "objective_inference.md").write_text(inference_raw.strip() + "\n", encoding="utf-8")

    print(f"Compact corpus: {compact_stats['path']} ({compact_stats['bytes']} bytes)")
    print(f"Forensic corpus: {forensic_stats['path']} ({forensic_stats['bytes']} bytes)")
    print(f"Wrote {args.out_dir / 'data_volume.json'}")
    print(f"Wrote {args.out_dir / 'rlm_head_engineer_prompt.txt'}")
    print(f"Wrote {args.out_dir / 'rlm_head_engineer.md'}")
    print(f"Wrote {args.out_dir / 'objective_inference_prompt.txt'}")
    print(f"Wrote {args.out_dir / 'objective_inference.json'}")
    print(f"Wrote {args.out_dir / 'objective_inference.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
