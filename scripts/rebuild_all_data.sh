#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

python - <<'PY'
from pathlib import Path
from rlm_harness import build_corpus_markdown

data_dir = Path("reports") / "data_volume"
compact = build_corpus_markdown(data_dir / "all_data_compact.md", include_patches=False)
forensic = build_corpus_markdown(data_dir / "all_data_forensic.md", include_patches=True)

print(
    f"Compact rebuilt: {compact['path']} "
    f"({compact['bytes']} bytes, {compact['words']} words, {compact['lines']} lines)"
)
print(
    f"Forensic rebuilt: {forensic['path']} "
    f"({forensic['bytes']} bytes, {forensic['words']} words, {forensic['lines']} lines)"
)
PY
