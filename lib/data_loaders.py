from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import CLAUDE_SESSION_DIRS, CODEX_SESSION_DIR, REPOS

STRIP_TAGS = [
    "system-reminder",
    "environment_context",
    "local-command-caveat",
    "command-name",
    "command-message",
    "command-args",
    "local-command-stdout",
    "claudeMd",
    "fast_mode_info",
    "gitStatus",
    "user-prompt-submit-hook",
]


@dataclass
class Commit:
    repo: str
    sha: str
    ts: datetime
    subject: str
    files: list[str]
    insertions: int
    deletions: int
    file_stats: dict[str, tuple[int, int]] = field(default_factory=dict)
    binary_numstat: bool = False
    merge_commit: bool = False


@dataclass
class Prompt:
    repo: str
    ts: datetime
    source: str
    text: str
    session_id: str | None = None


@dataclass
class SessionEvent:
    repo: str
    session_id: str
    ts: datetime
    role: str
    text: str
    source: str


def parse_ts(ts: str) -> datetime:
    parsed = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def utc_iso(ts: datetime) -> str:
    return ts.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def run_git(cmd: list[str], cwd: Path) -> str:
    result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        return ""
    return result.stdout


def _extract_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") in {"text", "input_text"}:
                parts.append(str(block.get("text", "")))
            elif "text" in block:
                parts.append(str(block.get("text", "")))
        return " ".join(parts)
    return str(content)


def clean_text(text: str) -> str:
    cleaned = text
    for tag in STRIP_TAGS:
        cleaned = re.sub(rf"<{tag}>.*?</{tag}>", "", cleaned, flags=re.DOTALL)
    cleaned = re.sub(r"<[^>]+>", "", cleaned)
    return cleaned.strip()


def load_commits(repo_name: str, repo_path: Path, start: datetime, end: datetime) -> list[Commit]:
    output = run_git(
        [
            "git",
            "log",
            f"--since={start.isoformat()}",
            f"--until={end.isoformat()}",
            "--format=%H|%aI|%s|%P",
            "--numstat",
        ],
        repo_path,
    )

    commits: list[Commit] = []
    current: Commit | None = None

    for line in output.splitlines():
        parts = line.split("|", 3)
        if len(parts) == 4 and len(parts[0]) == 40:
            sha, ts_raw, subject, parents_raw = parts
            parents = [p for p in parents_raw.split() if p]
            current = Commit(
                repo=repo_name,
                sha=sha,
                ts=parse_ts(ts_raw),
                subject=subject,
                files=[],
                insertions=0,
                deletions=0,
                merge_commit=len(parents) > 1,
            )
            commits.append(current)
            continue

        if current is None or not line.strip():
            continue

        ns = line.split("\t")
        if len(ns) != 3:
            continue
        ins_raw, del_raw, file_path = ns

        if file_path not in current.file_stats:
            current.file_stats[file_path] = (0, 0)
            current.files.append(file_path)

        ins = int(ins_raw) if ins_raw.isdigit() else 0
        dels = int(del_raw) if del_raw.isdigit() else 0
        if not ins_raw.isdigit() or not del_raw.isdigit():
            current.binary_numstat = True

        current.insertions += ins
        current.deletions += dels

        prev_ins, prev_dels = current.file_stats[file_path]
        current.file_stats[file_path] = (prev_ins + ins, prev_dels + dels)

    return commits


def load_claude_prompts(repo_name: str, session_dir: Path, start: datetime, end: datetime) -> list[Prompt]:
    prompts: list[Prompt] = []
    if not session_dir.exists():
        return prompts

    for path in sorted(session_dir.glob("*.jsonl")):
        session_id = path.stem
        with path.open() as fh:
            for line in fh:
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue

                if data.get("type") != "user":
                    continue

                ts_raw = data.get("timestamp")
                if not ts_raw:
                    continue
                ts = parse_ts(ts_raw)
                if ts < start or ts > end:
                    continue

                text = clean_text(_extract_text(data.get("message", {}).get("content", "")))
                if not text:
                    continue
                prompts.append(
                    Prompt(
                        repo=repo_name,
                        ts=ts,
                        source="claude",
                        text=text[:300],
                        session_id=session_id,
                    )
                )
    return prompts


def _detect_repo_from_cwd(cwd: str) -> str | None:
    for name, repo_path in REPOS.items():
        if str(repo_path) in cwd:
            return name
    return None


def load_codex_prompts(start: datetime, end: datetime) -> list[Prompt]:
    prompts: list[Prompt] = []
    if not CODEX_SESSION_DIR.exists():
        return prompts

    for path in sorted(CODEX_SESSION_DIR.glob("**/*.jsonl")):
        session_id = path.stem
        repo_name: str | None = None

        with path.open() as fh:
            for line in fh:
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue

                msg_type = data.get("type")
                if msg_type == "session_meta":
                    cwd = data.get("payload", {}).get("cwd", "")
                    repo_name = _detect_repo_from_cwd(str(cwd))
                    continue

                if msg_type != "response_item":
                    continue

                payload = data.get("payload", {})
                if payload.get("role") != "user":
                    continue

                ts_raw = data.get("timestamp")
                if not ts_raw or repo_name is None:
                    continue
                ts = parse_ts(ts_raw)
                if ts < start or ts > end:
                    continue

                text = clean_text(_extract_text(payload.get("content", [])))
                if not text:
                    continue
                prompts.append(
                    Prompt(
                        repo=repo_name,
                        ts=ts,
                        source="codex",
                        text=text[:300],
                        session_id=session_id,
                    )
                )

    return prompts


def _load_claude_session_events(repo_name: str, session_id: str) -> list[SessionEvent]:
    events: list[SessionEvent] = []
    session_dir = CLAUDE_SESSION_DIRS.get(repo_name)
    if session_dir is None:
        return events
    path = session_dir / f"{session_id}.jsonl"
    if not path.exists():
        return events

    with path.open() as fh:
        for line in fh:
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue

            msg_type = data.get("type")
            if msg_type not in {"user", "assistant"}:
                continue
            ts_raw = data.get("timestamp")
            if not ts_raw:
                continue
            ts = parse_ts(ts_raw)

            if msg_type == "user":
                content = data.get("message", {}).get("content", "")
                text = clean_text(_extract_text(content))
                role = "user"
            else:
                content = data.get("message", {}).get("content", [])
                text = clean_text(_extract_text(content))
                role = "assistant"

            if not text:
                continue
            events.append(
                SessionEvent(
                    repo=repo_name,
                    session_id=session_id,
                    ts=ts,
                    role=role,
                    text=text,
                    source="claude",
                )
            )
    return events


def _find_codex_session_file(session_id: str) -> Path | None:
    if not CODEX_SESSION_DIR.exists():
        return None
    for path in CODEX_SESSION_DIR.glob(f"**/{session_id}.jsonl"):
        return path
    return None


def _load_codex_session_events(repo_name: str, session_id: str) -> list[SessionEvent]:
    events: list[SessionEvent] = []
    path = _find_codex_session_file(session_id)
    if path is None:
        return events

    detected_repo: str | None = None
    with path.open() as fh:
        for line in fh:
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue

            msg_type = data.get("type")
            if msg_type == "session_meta":
                cwd = str(data.get("payload", {}).get("cwd", ""))
                detected_repo = _detect_repo_from_cwd(cwd)
                continue

            if msg_type != "response_item":
                continue

            payload = data.get("payload", {})
            role = payload.get("role")
            if role not in {"user", "assistant"}:
                continue

            ts_raw = data.get("timestamp")
            if not ts_raw:
                continue
            ts = parse_ts(ts_raw)

            text = clean_text(_extract_text(payload.get("content", [])))
            if not text:
                continue

            event_repo = detected_repo or repo_name
            if event_repo != repo_name:
                continue

            events.append(
                SessionEvent(
                    repo=repo_name,
                    session_id=session_id,
                    ts=ts,
                    role=role,
                    text=text,
                    source="codex",
                )
            )
    return events


def load_session_events(repo_name: str, session_id: str) -> list[SessionEvent]:
    events = _load_claude_session_events(repo_name, session_id)
    events.extend(_load_codex_session_events(repo_name, session_id))
    events.sort(key=lambda e: e.ts)
    return events
