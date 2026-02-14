#!/usr/bin/env python3
"""Adaptive remote pull for SICM/4D-bot repos and session data.

This script discovers likely project/session paths on a remote host and rsyncs
them into a timestamped local snapshot directory.
"""

from __future__ import annotations

import argparse
import json
import shlex
import subprocess
from datetime import datetime, timezone
from pathlib import Path


def run(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, text=True, capture_output=True, check=check)


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def sanitize_remote_path(path: str) -> str:
    clean = path.strip().lstrip("/")
    return clean.replace("..", "__").replace("\\", "/")


def build_ssh_target(host: str, user: str | None) -> str:
    return f"{user}@{host}" if user else host


def discover_remote_paths(ssh_target: str, max_depth: int) -> list[dict]:
    remote_script = f"""
set -euo pipefail
HOME_DIR="${{HOME}}"
MAX_DEPTH="{max_depth}"

emit() {{
  # type, label, path
  printf "%s\\t%s\\t%s\\n" "$1" "$2" "$3"
}}

# Fast explicit candidates first.
for p in \\
  "$HOME_DIR/4D-bot" \\
  "$HOME_DIR/Music/SICM" \\
  "$HOME_DIR/SICM" \\
  "$HOME_DIR/projects/4D-bot" \\
  "$HOME_DIR/projects/SICM" \\
  "$HOME_DIR/dev/4D-bot" \\
  "$HOME_DIR/dev/SICM"
do
  if [ -d "$p" ] && [ -d "$p/.git" ]; then
    emit "repo" "$(basename "$p")" "$p"
  fi
done

# Adaptive discovery of repos by folder name + .git.
for name in "4D-bot" "SICM"; do
  while IFS= read -r p; do
    [ -z "$p" ] && continue
    if [ -d "$p/.git" ]; then
      emit "repo" "$name" "$p"
    fi
  done < <(find "$HOME_DIR" -maxdepth "$MAX_DEPTH" -type d -name "$name" 2>/dev/null | sort -u)
done

# Claude session roots tied to project names.
CLAUDE_ROOT="$HOME_DIR/.claude/projects"
if [ -d "$CLAUDE_ROOT" ]; then
  emit "sessions_root" "claude_projects" "$CLAUDE_ROOT"
  while IFS= read -r p; do
    [ -z "$p" ] && continue
    emit "sessions" "claude_project" "$p"
  done < <(find "$CLAUDE_ROOT" -maxdepth 1 -mindepth 1 -type d | grep -Ei '4D-bot|sicm' || true)
fi

# Codex sessions (full root; filtering is done downstream by analyzers).
CODEX_ROOT="$HOME_DIR/.codex/sessions"
if [ -d "$CODEX_ROOT" ]; then
  emit "sessions_root" "codex_sessions" "$CODEX_ROOT"
fi
"""
    cmd = ["ssh", ssh_target, "bash", "-lc", remote_script]
    cp = run(cmd, check=False)
    if cp.returncode != 0:
        raise RuntimeError(f"remote discovery failed: {cp.stderr.strip()}")

    rows: list[dict] = []
    seen: set[tuple[str, str, str]] = set()
    for line in cp.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        t, label, path = parts
        key = (t.strip(), label.strip(), path.strip())
        if key in seen:
            continue
        seen.add(key)
        rows.append({"type": key[0], "label": key[1], "path": key[2]})
    return rows


def rsync_pull(ssh_target: str, remote_path: str, local_path: Path, dry_run: bool) -> dict:
    local_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "rsync",
        "-az",
        "--info=stats2",
        "--human-readable",
        f"{ssh_target}:{remote_path.rstrip('/')}/",
        str(local_path),
    ]
    if dry_run:
        return {"ok": True, "dry_run": True, "cmd": cmd}
    cp = run(cmd, check=False)
    return {
        "ok": cp.returncode == 0,
        "returncode": cp.returncode,
        "cmd": cmd,
        "stdout_tail": "\n".join(cp.stdout.splitlines()[-20:]),
        "stderr_tail": "\n".join(cp.stderr.splitlines()[-20:]),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", required=True, help="remote host, e.g. ath-ms-7a73")
    parser.add_argument("--user", default=None, help="optional ssh user")
    parser.add_argument("--max-depth", type=int, default=7, help="remote find depth")
    parser.add_argument("--out-root", type=Path, default=Path("reports") / "remote_sync")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    ssh_target = build_ssh_target(args.host, args.user)
    run_id = f"{args.host}_{utc_stamp()}"
    out_dir = args.out_root / run_id
    out_dir.mkdir(parents=True, exist_ok=True)

    discovered = discover_remote_paths(ssh_target, args.max_depth)
    pulls: list[dict] = []
    for row in discovered:
        remote_path = row["path"]
        local_rel = sanitize_remote_path(remote_path)
        local_path = out_dir / "snapshot" / local_rel
        result = rsync_pull(ssh_target, remote_path, local_path, args.dry_run)
        pulls.append(
            {
                "type": row["type"],
                "label": row["label"],
                "remote_path": remote_path,
                "local_path": str(local_path),
                "result": result,
            }
        )

    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "host": args.host,
        "ssh_target": ssh_target,
        "dry_run": args.dry_run,
        "discovered_count": len(discovered),
        "discovered": discovered,
        "pulls": pulls,
    }
    manifest_path = out_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    ok = sum(1 for p in pulls if p["result"].get("ok"))
    failed = len(pulls) - ok
    print(f"Host: {args.host}")
    print(f"Discovered paths: {len(discovered)}")
    print(f"Pulls ok: {ok}")
    print(f"Pulls failed: {failed}")
    print(f"Manifest: {manifest_path}")
    if failed > 0:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
