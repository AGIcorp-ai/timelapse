#!/usr/bin/env python3
"""Build a timeline of commits paired with the user messages that produced them."""

import json
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path


def parse_ts(s):
    """Parse ISO timestamp, handling trailing Z."""
    return datetime.fromisoformat(s.replace("Z", "+00:00"))

REPOS = {
    "4D-bot": Path("/home/ath/4D-bot"),
    "SICM": Path("/home/ath/Music/SICM"),
}

CLAUDE_SESSION_DIRS = {
    "4D-bot": Path.home() / ".claude/projects/-home-ath-4D-bot",
    "SICM": Path.home() / ".claude/projects/-home-ath-Music-SICM",
}

CODEX_SESSION_DIR = Path.home() / ".codex/sessions"

OUTPUT = Path(__file__).parent / "timeline.md"

WINDOW_BEFORE = timedelta(hours=3)
WINDOW_AFTER = timedelta(minutes=5)


# ── Step 1: Extract commits ─────────────────────────────────────────────────

def extract_commits(repo_name, repo_path):
    """Return list of {sha, ts, subject, stat} dicts from git log."""
    result = subprocess.run(
        ["git", "log", "--format=%H|%aI|%s", "--numstat"],
        cwd=repo_path, capture_output=True, text=True,
    )
    commits = []
    current = None
    for line in result.stdout.splitlines():
        if "|" in line and len(line.split("|", 2)) == 3 and len(line.split("|")[0]) == 40:
            sha, ts_str, subject = line.split("|", 2)
            current = {
                "sha": sha, "ts": parse_ts(ts_str),
                "subject": subject, "repo": repo_name,
                "files": [], "insertions": 0, "deletions": 0,
            }
            commits.append(current)
        elif current and line.strip():
            parts = line.split("\t")
            if len(parts) == 3:
                ins, dels, fname = parts
                current["files"].append(fname)
                current["insertions"] += int(ins) if ins != "-" else 0
                current["deletions"] += int(dels) if dels != "-" else 0
    return commits


# ── Step 2: Extract user messages from Claude sessions ───────────────────────

def extract_claude_messages(repo_name, session_dir):
    """Return list of {ts, text, session_id, repo} dicts."""
    if not session_dir.exists():
        return []
    msgs = []
    for f in sorted(session_dir.glob("*.jsonl")):
        session_id = f.stem
        for line in f.open():
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            if d.get("type") != "user":
                continue
            ts_str = d.get("timestamp")
            if not ts_str:
                continue
            content = d.get("message", {}).get("content", "")
            if isinstance(content, list):
                text = " ".join(
                    c.get("text", "") for c in content if isinstance(c, dict)
                )
            else:
                text = str(content)
            text = re.sub(r"<[^>]+>", "", text).strip()
            if not text or text.startswith("/") and len(text) < 20:
                continue
            msgs.append({
                "ts": parse_ts(ts_str),
                "text": text[:500],
                "session_id": session_id,
                "repo": repo_name,
            })
    return msgs


# ── Step 3: Extract user messages from Codex sessions ────────────────────────

def extract_codex_messages():
    """Return list of {ts, text, session_id, repo} dicts."""
    if not CODEX_SESSION_DIR.exists():
        return []
    msgs = []
    for f in sorted(CODEX_SESSION_DIR.glob("**/*.jsonl")):
        session_id = f.stem
        repo_name = None
        for line in f.open():
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            if d.get("type") == "session_meta":
                cwd = d.get("payload", {}).get("cwd", "")
                for name, path in REPOS.items():
                    if str(path) in cwd:
                        repo_name = name
                        break
                continue
            if not repo_name:
                continue
            if d.get("type") != "response_item":
                continue
            payload = d.get("payload", {})
            if payload.get("role") != "user":
                continue
            ts_str = d.get("timestamp")
            if not ts_str:
                continue
            content = payload.get("content", [])
            if isinstance(content, list):
                text = " ".join(
                    c.get("text", "") for c in content if isinstance(c, dict)
                )
            else:
                text = str(content)
            text = re.sub(r"<[^>]+>", "", text).strip()
            if not text:
                continue
            msgs.append({
                "ts": parse_ts(ts_str),
                "text": text[:500],
                "session_id": session_id,
                "repo": repo_name,
            })
    return msgs


# ── Step 4: Match sessions to commits ────────────────────────────────────────

def match_messages_to_commits(commits, messages):
    """For each commit, find user messages in [commit - 3h, commit + 5min]."""
    messages.sort(key=lambda m: m["ts"])
    for commit in commits:
        ct = commit["ts"]
        window_start = ct - WINDOW_BEFORE
        window_end = ct + WINDOW_AFTER
        matched = [
            m for m in messages
            if m["repo"] == commit["repo"]
            and window_start <= m["ts"] <= window_end
        ]
        # Group by session, pick session with latest message closest to commit
        if not matched:
            commit["messages"] = []
            continue
        sessions = {}
        for m in matched:
            sessions.setdefault(m["session_id"], []).append(m)
        best_session = min(
            sessions,
            key=lambda sid: min(abs((m["ts"] - ct).total_seconds()) for m in sessions[sid]),
        )
        commit["messages"] = sorted(sessions[best_session], key=lambda m: m["ts"])


# ── Step 5: Render markdown ──────────────────────────────────────────────────

def render(commits):
    lines = ["# Timeline\n"]
    for c in commits:
        ts_local = c["ts"].strftime("%Y-%m-%d %H:%M")
        lines.append(f"## {ts_local} — {c['repo']}\n")
        stat = f"+{c['insertions']} -{c['deletions']}"
        files = ", ".join(c["files"][:5])
        if len(c["files"]) > 5:
            files += f" (+{len(c['files']) - 5} more)"
        lines.append(f"### Commit: `{c['sha'][:7]}` — \"{c['subject']}\"")
        lines.append(f"{stat} | {files}\n")
        if c.get("messages"):
            lines.append("**Session (Claude):**")
            for m in c["messages"]:
                for para in m["text"].split("\n"):
                    para = para.strip()
                    if para:
                        lines.append(f"> {para}")
                lines.append(">")
            if lines[-1] == ">":
                lines.pop()
            lines.append("")
        lines.append("---\n")
    return "\n".join(lines)


def main():
    # Gather commits
    all_commits = []
    for name, path in REPOS.items():
        all_commits.extend(extract_commits(name, path))
    all_commits.sort(key=lambda c: c["ts"])
    print(f"Found {len(all_commits)} commits across {len(REPOS)} repos")

    # Gather user messages
    all_messages = []
    for name, sdir in CLAUDE_SESSION_DIRS.items():
        msgs = extract_claude_messages(name, sdir)
        all_messages.extend(msgs)
        print(f"  Claude/{name}: {len(msgs)} user messages")
    codex_msgs = extract_codex_messages()
    all_messages.extend(codex_msgs)
    if codex_msgs:
        print(f"  Codex: {len(codex_msgs)} user messages")

    # Match
    match_messages_to_commits(all_commits, all_messages)
    matched = sum(1 for c in all_commits if c.get("messages"))
    print(f"Matched {matched}/{len(all_commits)} commits to sessions")

    # Render
    md = render(all_commits)
    OUTPUT.write_text(md)
    print(f"Wrote {OUTPUT} ({len(md)} bytes)")


if __name__ == "__main__":
    main()
