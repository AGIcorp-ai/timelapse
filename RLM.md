# RLM.md

## Purpose
`RLM.md` is the top-level orchestrator for time-machine analysis in this repo.
It defines how to assemble a single corpus, run multi-turn diagnostics, and detect divergence from intended outcomes.

## Current Data Volume (single markdown corpus)
Generated artifacts:
- `reports/data_volume/all_data_compact.md`
- `reports/data_volume/all_data_forensic.md`
- `reports/rlm/data_volume.json`

Measured sizes:
- Compact (messages + code + commit metadata): `959,003` bytes, `132,956` words, `24,637` lines
- Forensic (messages + code + full commit patches): `1,057,743` bytes, `143,790` words, `27,774` lines

Rule of thumb token estimate:
- Compact: about `238k` tokens (char/4)
- Forensic: about `262k` tokens (char/4)

## Data Inputs
- Messages: `transcripts/**/*.md`, `timeline.md`
- Code: tracked files from `git ls-files`
- History: `git log` (metadata for compact, `-p` for forensic)
- Analyses: `reports/` JSON + markdown outputs

## Golden Analysis Principle
Do not classify prompt quality from one turn in isolation.
Every judgment must include conversational context (minimum 2 turns; target 3+ turns).
Short prompts are neutral by default and only become a concern when context is missing or ambiguity persists across turns.

## Core Workflow
1. Refresh corpus and metrics.
2. Evaluate intended outcomes vs observed repo trajectory.
3. Attribute responsibility (user prompt clarity vs agent inference vs plan drift).
4. Emit an auditable report with concrete divergence points.

## Commands
Canonical orchestrator run (build corpus + run head-engineer loop):
```bash
python rlm_harness.py --days 35
```

Rebuild all-data corpus:
```bash
mkdir -p reports/data_volume
# Compact
bash -lc '
  out=reports/data_volume/all_data_compact.md
  : > "$out"
  echo "# All Data (Compact)" >> "$out"
  find transcripts -type f -name "*.md" | sort | while read -r f; do
    printf "\n## FILE: %s\n\n" "$f" >> "$out"
    cat "$f" >> "$out"
  done
  printf "\n## FILE: timeline.md\n\n" >> "$out"
  cat timeline.md >> "$out"
  git ls-files | sort | while read -r f; do
    printf "\n## FILE: %s\n\n" "$f" >> "$out"
    cat "$f" >> "$out"
  done
  printf "\n## Commits\n\n" >> "$out"
  git log --date=iso --stat --pretty=format:"### COMMIT %H%nDate: %ad%nAuthor: %an <%ae>%n%n%s%n%b" >> "$out"
'

# Forensic
bash -lc '
  out=reports/data_volume/all_data_forensic.md
  : > "$out"
  echo "# All Data (Forensic)" >> "$out"
  find transcripts -type f -name "*.md" | sort | while read -r f; do
    printf "\n## FILE: %s\n\n" "$f" >> "$out"
    cat "$f" >> "$out"
  done
  printf "\n## FILE: timeline.md\n\n" >> "$out"
  cat timeline.md >> "$out"
  git ls-files | sort | while read -r f; do
    printf "\n## FILE: %s\n\n" "$f" >> "$out"
    cat "$f" >> "$out"
  done
  printf "\n## Commits (patches)\n\n" >> "$out"
  git log --date=iso -p --pretty=format:"### COMMIT %H%nDate: %ad%nAuthor: %an <%ae>%n%n%s%n%b" >> "$out"
'
```

Run divergence review:
```bash
python time_machine_review.py
```

## Decision Policy for Prompting Quality
Use these signals only with multi-turn context:
- `short_without_context`
- `no_explicit_target_multi_turn`
- `no_success_criteria_multi_turn`

Do not use prompt length alone as a negative signal.

## Required Outputs
- `reports/time_machine/time_machine_review.json`
- `reports/time_machine/time_machine_review.md`
- `reports/time_machine/gpt5mini_responsibility.md`
- `reports/data_volume/all_data_compact.md`
- `reports/data_volume/all_data_forensic.md`
- `reports/rlm/data_volume.json`
- `reports/rlm/rlm_head_engineer.md`
- `reports/rlm/rlm_head_engineer_prompt.txt`
