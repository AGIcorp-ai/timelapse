# The primary objective of this repo is to collate information from ~/.claude/projects/, ~/.codex/sessions/, ~/4D-bot, ~/4D-bot/4D-ascii-graphics-engine, and ~/Music/SICM/. We want to qualitatively extract information that gets us concrete solutions to the implied objectives of the conversation, code and commit data in the aforementioned directories.

# Repository Guidelines

## Project Structure & Module Organization
- Root analyzers:
  - `analyze_repo.py` (repo-wide metrics)
  - `analyze_file.py` (file churn/coupling)
  - `analyze_symbols.py` (symbol-level attribution)
  - `analyze_session.py` (session prompt-to-commit attribution)
  - `analyze_last_month.py` (backward-compatible wrapper)
- Timeline and transcript pipelines:
  - `build_timeline.py` (cross-repo commit-to-prompt timeline markdown)
  - `build_transcript.py` (per-session transcript markdown with interleaved commit markers)
  - `objective_timeline.py` (objective-level synthesis outputs for docs/pages)
  - `meta_analysis.py` (GPT-5.2 meta-analysis: cross-report synthesis + verdict)
- Shared library code lives in `lib/` (`config.py`, `data_loaders.py`, `metrics.py`, `symbol_extractor.py`).
- Tests live in `tests/` and mirror library behavior (`test_metrics.py`, `test_symbol_extractor.py`, etc.).
- Static showcase site for GitHub Pages is under `docs/`.
- Generated outputs are written to `reports/` and are treated as build artifacts.
- Additional generated artifacts include `timeline.md`, `transcripts/`, and `docs/objective_timeline.{md,json}`.

## Build, Test, and Development Commands
- `python analyze_repo.py --days 30`: generate repo markdown/JSON/CSV in `reports/repo/`.
- `python analyze_file.py --repo 4D-bot --file arena_v0/cli.py --days 30`: deep dive for one file.
- `python analyze_symbols.py --repo 4D-bot --file arena_v0/cli.py --days 30`: symbol churn report.
- `python analyze_session.py --repo 4D-bot --session-id <SESSION_ID>`: session attribution outputs.
- `python build_timeline.py`: generate `timeline.md` by matching user prompts to commits across configured repos.
- `python build_transcript.py`: generate `transcripts/` session narratives with commit markers.
- `python objective_timeline.py`: refresh objective timeline markdown/JSON consumed by `docs/`.
- `python meta_analysis.py`: GPT-5.2 meta-analysis of all derived reports (synthesis + verdict).
- `python -m unittest discover -s tests -v`: run all tests.

## Coding Style & Naming Conventions
- Python only; use 4-space indentation and type hints for new/changed functions.
- Keep modules focused and composable; shared logic belongs in `lib/`, not analyzer entrypoints.
- Use `snake_case` for files/functions/variables, `PascalCase` for dataclasses.
- Prefer deterministic outputs (stable sort/order, explicit timestamps in UTC).

## Testing Guidelines
- Framework: built-in `unittest`.
- Add tests for each new metric/parser edge case (empty input, malformed JSONL, binary numstat, merge commits).
- Name tests `test_<unit>.py` and test methods `test_<behavior>()`.
- Validate both structure and semantics (e.g., required JSON keys + expected metric values).

## Commit & Pull Request Guidelines
- Commit messages should be short, imperative, and specific (e.g., `Add symbol hunk fallback parser`).
- Keep commits scoped: one logical change per commit where possible.
- PRs should include:
  - what changed and why,
  - commands run (tests + sample analyzer command),
  - note of any schema/output contract changes,
  - screenshots only when updating `docs/` visual behavior.

## Security & Configuration Tips
- Session logs are loaded from local home directories (Claude/Codex); avoid committing private logs.
- Repo and session discovery is configured in `lib/config.py`; default tracked repos include `4D-bot`, `SICM`, and `ascii-engine`.
- Optional environment overrides:
  - `TIMELAPSE_EXTRA_CLAUDE_DIRS`
  - `TIMELAPSE_EXTRA_CODEX_ROOTS`
  - `TIMELAPSE_REPO_ALIASES`
- Do not commit generated `reports/` artifacts unless explicitly needed for a reproducible demo.
