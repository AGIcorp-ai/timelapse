from __future__ import annotations

import os
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


def _parse_extra_claude_dirs(raw: str | None) -> dict[str, list[Path]]:
    out: dict[str, list[Path]] = {}
    if not raw:
        return out
    for item in raw.split(";"):
        item = item.strip()
        if not item or "=" not in item:
            continue
        repo, paths_raw = item.split("=", 1)
        repo = repo.strip()
        paths = []
        for p in paths_raw.split(","):
            p = p.strip()
            if p:
                paths.append(Path(p))
        if paths:
            out[repo] = paths
    return out


def _parse_extra_codex_roots(raw: str | None) -> list[Path]:
    if not raw:
        return []
    out: list[Path] = []
    for p in raw.split(":"):
        p = p.strip()
        if p:
            out.append(Path(p))
    return out


def _parse_repo_aliases(raw: str | None) -> dict[str, list[Path]]:
    out: dict[str, list[Path]] = {}
    if not raw:
        return out
    for item in raw.split(";"):
        item = item.strip()
        if not item or "=" not in item:
            continue
        repo, paths_raw = item.split("=", 1)
        repo = repo.strip()
        paths: list[Path] = []
        for p in paths_raw.split(","):
            p = p.strip()
            if p:
                paths.append(Path(p))
        if paths:
            out[repo] = paths
    return out


EXTRA_CLAUDE_SESSION_DIRS = _parse_extra_claude_dirs(os.getenv("TIMELAPSE_EXTRA_CLAUDE_DIRS"))
CODEX_SESSION_DIRS = [CODEX_SESSION_DIR] + _parse_extra_codex_roots(os.getenv("TIMELAPSE_EXTRA_CODEX_ROOTS"))
REPO_ALIASES = _parse_repo_aliases(os.getenv("TIMELAPSE_REPO_ALIASES"))

REPO_PATH_HINTS: dict[str, list[Path]] = {}
for repo, path in REPOS.items():
    REPO_PATH_HINTS[repo] = [path]
for repo, paths in REPO_ALIASES.items():
    REPO_PATH_HINTS.setdefault(repo, [])
    REPO_PATH_HINTS[repo].extend(paths)
