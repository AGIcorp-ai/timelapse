#!/usr/bin/env python3
"""Merge remote snapshot session data into a single staged dataset.

Outputs:
- reports/remote_merged/<run>/claude/projects/<host>/<project-dir>/*.jsonl
- reports/remote_merged/<run>/codex/sessions/<host>/**/*.jsonl
- reports/remote_merged/<run>/session_env.sh
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def latest_snapshot_dir(root: Path, host: str) -> Path:
    candidates = sorted(root.glob(f"{host}_*"))
    if not candidates:
        raise FileNotFoundError(f"no snapshot directory for host={host}")
    return candidates[-1]


def host_from_snapshot_name(name: str) -> str:
    m = re.match(r"^(.+)_\d{8}T\d{6}Z$", name)
    return m.group(1) if m else name


def _copy_tree(src: Path, dst: Path) -> int:
    count = 0
    if not src.exists():
        return count
    for p in src.rglob("*.jsonl"):
        rel = p.relative_to(src)
        target = dst / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            shutil.copy2(p, target)
            count += 1
    return count


def _copy_claude_projects(snapshot_root: Path, out_root: Path, host: str) -> tuple[dict[str, list[Path]], int]:
    claude_root = snapshot_root / "snapshot" / "home" / "ath" / ".claude" / "projects"
    out_projects = out_root / "claude" / "projects" / host
    out_projects.mkdir(parents=True, exist_ok=True)
    mapping: dict[str, list[Path]] = {"4D-bot": [], "SICM": []}
    copied = 0

    if not claude_root.exists():
        return mapping, copied

    for project_dir in sorted(claude_root.iterdir()):
        if not project_dir.is_dir():
            continue
        name = project_dir.name
        low = name.lower()
        repo: str | None = None
        if "4d-bot" in low:
            repo = "4D-bot"
        elif "sicm" in low:
            repo = "SICM"
        if repo is None:
            continue
        dst = out_projects / name
        copied += _copy_tree(project_dir, dst)
        mapping[repo].append(dst)
    return mapping, copied


def _copy_codex(snapshot_root: Path, out_root: Path, host: str) -> tuple[Path, int]:
    codex_src = snapshot_root / "snapshot" / "home" / "ath" / ".codex" / "sessions"
    codex_dst = out_root / "codex" / "sessions" / host
    codex_dst.mkdir(parents=True, exist_ok=True)
    copied = _copy_tree(codex_src, codex_dst)
    return codex_dst, copied


def write_env_file(out_root: Path, claude_map: dict[str, list[Path]], codex_roots: list[Path]) -> Path:
    def fmt_paths(paths: list[Path]) -> str:
        return ",".join(str(p.resolve()) for p in sorted(paths))

    parts = []
    for repo in ("4D-bot", "SICM"):
        paths = claude_map.get(repo, [])
        if paths:
            parts.append(f"{repo}={fmt_paths(paths)}")
    claude_env = ";".join(parts)
    codex_env = ":".join(str(p.resolve()) for p in sorted(codex_roots))

    env_path = out_root / "session_env.sh"
    env_path.write_text(
        "#!/usr/bin/env bash\n"
        f"export TIMELAPSE_EXTRA_CLAUDE_DIRS={json.dumps(claude_env)}\n"
        f"export TIMELAPSE_EXTRA_CODEX_ROOTS={json.dumps(codex_env)}\n",
        encoding="utf-8",
    )
    env_path.chmod(0o755)
    return env_path


def collect_repo_aliases(source_snapshots: list[Path]) -> dict[str, set[str]]:
    aliases: dict[str, set[str]] = {"4D-bot": set(), "SICM": set()}
    for snap in source_snapshots:
        manifest = snap / "manifest.json"
        if not manifest.exists():
            continue
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        for row in data.get("discovered", []):
            if row.get("type") != "repo":
                continue
            label = str(row.get("label", "")).strip()
            path = str(row.get("path", "")).strip()
            if label in aliases and path:
                aliases[label].add(path)
    return aliases


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--snapshot-root",
        type=Path,
        default=Path("reports") / "remote_sync",
        help="directory containing per-host snapshot runs",
    )
    parser.add_argument(
        "--hosts",
        nargs="+",
        default=["ath-ms-7a73", "ath-e6420"],
        help="hostnames to merge (latest run per host)",
    )
    parser.add_argument(
        "--out-root",
        type=Path,
        default=Path("reports") / "remote_merged",
        help="base merged output directory",
    )
    args = parser.parse_args()

    run_dir = args.out_root / utc_stamp()
    run_dir.mkdir(parents=True, exist_ok=True)

    merged_claude: dict[str, list[Path]] = {"4D-bot": [], "SICM": []}
    merged_codex: list[Path] = []
    source_snapshots: list[str] = []
    source_snapshot_paths: list[Path] = []
    copied_files = 0

    for host in args.hosts:
        snap = latest_snapshot_dir(args.snapshot_root, host)
        source_snapshots.append(str(snap))
        source_snapshot_paths.append(snap)
        host_name = host_from_snapshot_name(snap.name)
        claude_map, c = _copy_claude_projects(snap, run_dir, host_name)
        copied_files += c
        for repo, dirs in claude_map.items():
            merged_claude[repo].extend(dirs)
        codex_root, c2 = _copy_codex(snap, run_dir, host_name)
        merged_codex.append(codex_root)
        copied_files += c2

    env_path = write_env_file(run_dir, merged_claude, merged_codex)
    repo_aliases = collect_repo_aliases(source_snapshot_paths)
    alias_str = ";".join(
        f"{repo}={','.join(sorted(paths))}" for repo, paths in repo_aliases.items() if paths
    )
    with env_path.open("a", encoding="utf-8") as fh:
        fh.write(f"export TIMELAPSE_REPO_ALIASES={json.dumps(alias_str)}\n")
    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "source_snapshots": source_snapshots,
        "hosts": args.hosts,
        "copied_jsonl_files": copied_files,
        "merged_claude_dirs": {k: [str(p) for p in v] for k, v in merged_claude.items()},
        "merged_codex_roots": [str(p) for p in merged_codex],
        "repo_aliases": {k: sorted(v) for k, v in repo_aliases.items()},
        "env_file": str(env_path),
    }
    manifest_path = run_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    print(f"Merged run: {run_dir}")
    print(f"Copied jsonl files: {copied_files}")
    print(f"Env file: {env_path}")
    print(f"Manifest: {manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
