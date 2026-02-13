#!/usr/bin/env python3
"""Build full session transcripts with commit file markers interleaved.

Produces one markdown file per session, organized into per-repo directories.
Each session shows the full user/assistant conversation (text only, no tool calls)
with commit markers inserted at the right chronological position showing what
files were committed and when.

Output structure:
    timelapse/transcripts/
        4D-bot/
            2026-02-01_13-16_compiled-napping-rabin.md
            ...
        SICM/
            2026-01-18_22-30_session-slug.md
            ...
        index.md    # table of contents
"""

import json
import re
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPOS = {
    "4D-bot": Path("/home/ath/4D-bot"),
    "SICM": Path("/home/ath/Music/SICM"),
}

CLAUDE_SESSION_DIRS = {
    "4D-bot": Path.home() / ".claude/projects/-home-ath-4D-bot",
    "SICM": Path.home() / ".claude/projects/-home-ath-Music-SICM",
}

OUTPUT_DIR = Path(__file__).parent / "transcripts"


def parse_ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


# ── Commits ──────────────────────────────────────────────────────────────────

def extract_commits(repo_name, repo_path):
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
                "sha": sha, "ts": parse_ts(ts_str), "subject": subject,
                "repo": repo_name, "files": [],
            }
            commits.append(current)
        elif current and line.strip():
            parts = line.split("\t")
            if len(parts) == 3:
                current["files"].append(parts[2])
    return commits


# ── Session parsing ──────────────────────────────────────────────────────────

def extract_text_from_content(content):
    """Extract human-readable text from message content (string or list)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for c in content:
            if isinstance(c, dict) and c.get("type") == "text":
                parts.append(c.get("text", ""))
            elif isinstance(c, dict) and c.get("type") == "input_text":
                parts.append(c.get("text", ""))
        return "\n".join(parts)
    return ""


STRIP_TAGS = [
    "system-reminder", "environment_context", "local-command-caveat",
    "command-name", "command-message", "command-args", "local-command-stdout",
    "claudeMd", "fast_mode_info", "gitStatus",
]


def clean_text(text):
    """Strip XML tags, system prompts, excessive whitespace."""
    for tag in STRIP_TAGS:
        text = re.sub(rf"<{tag}>.*?</{tag}>", "", text, flags=re.DOTALL)
    # Also strip any remaining paired XML tags that look like system metadata
    text = re.sub(r"<user-prompt-submit-hook>.*?</user-prompt-submit-hook>", "", text, flags=re.DOTALL)
    text = text.strip()
    return text


def parse_session(session_path):
    """Parse a Claude session file into a list of events.

    Returns: (metadata, events) where events are {ts, role, text} dicts.
    """
    metadata = {"session_id": session_path.stem, "cwd": None, "slug": None}
    events = []

    with open(session_path) as f:
        for line in f:
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue

            msg_type = d.get("type")

            if msg_type == "user":
                if not metadata["cwd"]:
                    metadata["cwd"] = d.get("cwd")
                if not metadata["slug"]:
                    metadata["slug"] = d.get("slug")
                ts_str = d.get("timestamp")
                if not ts_str:
                    continue
                text = extract_text_from_content(d.get("message", {}).get("content", ""))
                text = clean_text(text)
                if not text or (text.startswith("/") and len(text) < 20):
                    continue
                events.append({"ts": parse_ts(ts_str), "role": "user", "text": text})

            elif msg_type == "assistant":
                ts_str = d.get("timestamp")
                if not ts_str:
                    continue
                content = d.get("message", {}).get("content", [])
                # Only keep text blocks, skip tool_use/tool_result
                text = extract_text_from_content(content)
                text = clean_text(text)
                if not text:
                    continue
                events.append({"ts": parse_ts(ts_str), "role": "assistant", "text": text})

    return metadata, events


# ── Matching & rendering ─────────────────────────────────────────────────────

def find_session_repo(metadata):
    """Determine which repo a session belongs to based on cwd."""
    cwd = metadata.get("cwd", "") or ""
    for name, path in REPOS.items():
        if str(path) in cwd:
            return name
    return None


def render_session(metadata, events, commits):
    """Render a single session as markdown with commit markers interleaved."""
    # Merge events and commits into one timeline
    timeline = []
    for e in events:
        timeline.append(("msg", e["ts"], e))
    for c in commits:
        timeline.append(("commit", c["ts"], c))
    timeline.sort(key=lambda x: x[1])

    lines = []
    repo = find_session_repo(metadata) or "unknown"
    slug = metadata.get("slug") or metadata["session_id"][:8]
    first_ts = events[0]["ts"] if events else None
    title_ts = first_ts.strftime("%Y-%m-%d %H:%M") if first_ts else "unknown"

    lines.append(f"# {title_ts} — {repo} — {slug}\n")

    for kind, ts, item in timeline:
        ts_str = ts.strftime("%H:%M")

        if kind == "commit":
            lines.append(f"\n---\n")
            lines.append(f"### [{ts_str}] Commit `{item['sha'][:7]}` — \"{item['subject']}\"")
            lines.append(f"Files: {', '.join(item['files'][:10])}")
            if len(item["files"]) > 10:
                lines.append(f"  (+{len(item['files']) - 10} more)")
            lines.append(f"\n---\n")

        elif kind == "msg":
            if item["role"] == "user":
                lines.append(f"\n**[{ts_str}] User:**\n")
                for para in item["text"].split("\n"):
                    lines.append(f"> {para}")
            else:
                # Assistant: keep it but mark it lighter
                text = item["text"]
                if len(text) > 1000:
                    text = text[:1000] + "\n\n[...truncated]"
                lines.append(f"\n**[{ts_str}] Assistant:**\n")
                lines.append(text)

    return "\n".join(lines)


def main():
    # Gather commits per repo
    repo_commits = {}
    for name, path in REPOS.items():
        repo_commits[name] = extract_commits(name, path)
        print(f"  {name}: {len(repo_commits[name])} commits")

    # Parse all sessions and render (clean output dir first)
    if OUTPUT_DIR.exists():
        shutil.rmtree(OUTPUT_DIR)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    index_entries = []
    total_sessions = 0

    for repo_name, session_dir in CLAUDE_SESSION_DIRS.items():
        if not session_dir.exists():
            continue
        repo_dir = OUTPUT_DIR / repo_name
        repo_dir.mkdir(exist_ok=True)

        for session_path in sorted(session_dir.glob("*.jsonl")):
            metadata, events = parse_session(session_path)
            if not events:
                continue

            detected_repo = find_session_repo(metadata) or repo_name

            # Find commits that fall within this session's time range
            first_ts = events[0]["ts"]
            last_ts = events[-1]["ts"]
            window_start = first_ts - timedelta(minutes=5)
            window_end = last_ts + timedelta(hours=1)

            session_commits = [
                c for c in repo_commits.get(detected_repo, [])
                if window_start <= c["ts"] <= window_end
            ]

            md = render_session(metadata, events, session_commits)

            slug = metadata.get("slug") or metadata["session_id"][:12]
            slug = re.sub(r"[^a-zA-Z0-9_-]", "", slug)
            ts_prefix = first_ts.strftime("%Y-%m-%d_%H-%M")
            filename = f"{ts_prefix}_{slug}.md"

            out_path = repo_dir / filename
            out_path.write_text(md)
            total_sessions += 1

            n_commits = len(session_commits)
            n_user = sum(1 for e in events if e["role"] == "user")
            index_entries.append({
                "repo": detected_repo,
                "ts": first_ts,
                "slug": slug,
                "filename": f"{detected_repo}/{filename}",
                "n_user_msgs": n_user,
                "n_commits": n_commits,
            })

    # Write index
    index_entries.sort(key=lambda e: e["ts"])
    index_lines = ["# Session Transcripts\n"]
    current_repo = None
    for entry in index_entries:
        if entry["repo"] != current_repo:
            current_repo = entry["repo"]
            index_lines.append(f"\n## {current_repo}\n")
        ts_str = entry["ts"].strftime("%Y-%m-%d %H:%M")
        commits_note = f", {entry['n_commits']} commits" if entry["n_commits"] else ""
        index_lines.append(
            f"- [{ts_str} — {entry['slug']}]({entry['filename']}) "
            f"({entry['n_user_msgs']} messages{commits_note})"
        )
    (OUTPUT_DIR / "index.md").write_text("\n".join(index_lines))

    print(f"\nWrote {total_sessions} session transcripts to {OUTPUT_DIR}/")
    print(f"Index: {OUTPUT_DIR}/index.md")


if __name__ == "__main__":
    main()
