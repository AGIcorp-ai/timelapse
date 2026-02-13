from __future__ import annotations

from pathlib import Path

REPOS = {
    "4D-bot": Path("/home/ath/4D-bot"),
    "SICM": Path("/home/ath/Music/SICM"),
}

CLAUDE_SESSION_DIRS = {
    "4D-bot": Path.home() / ".claude/projects/-home-ath-4D-bot",
    "SICM": Path.home() / ".claude/projects/-home-ath-Music-SICM",
}

CODEX_SESSION_DIR = Path.home() / ".codex/sessions"

REPORTS_DIR = Path(__file__).resolve().parents[1] / "reports"

SCHEMA_VERSION = "v0.1"
COLLECTOR_VERSION = "timelapse-analyzers/0.1"
