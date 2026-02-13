from __future__ import annotations

from collections import Counter, defaultdict
from datetime import timedelta
from statistics import median

from .data_loaders import Commit, Prompt


def nearest_prompt_lags_hours(commits: list[Commit], prompts: list[Prompt]) -> list[float]:
    prompts_by_repo: dict[str, list[Prompt]] = defaultdict(list)
    for prompt in prompts:
        prompts_by_repo[prompt.repo].append(prompt)
    for repo_prompts in prompts_by_repo.values():
        repo_prompts.sort(key=lambda p: p.ts)

    lags: list[float] = []
    for commit in commits:
        repo_prompts = prompts_by_repo.get(commit.repo, [])
        nearest: Prompt | None = None
        for prompt in repo_prompts:
            if prompt.ts > commit.ts:
                break
            nearest = prompt
        if nearest is None:
            continue
        lag = (commit.ts - nearest.ts).total_seconds() / 3600.0
        if 0.0 <= lag <= 12.0:
            lags.append(lag)
    return lags


def rework_ratio(commits: list[Commit], window_days: int = 7) -> float:
    commit_dates: dict[str, list] = defaultdict(list)
    for commit in commits:
        for file_path in commit.files:
            commit_dates[file_path].append(commit.ts)

    touched = 0
    retouched = 0
    window = timedelta(days=window_days)

    for timestamps in commit_dates.values():
        timestamps.sort()
        touched += 1
        if any((b - a) <= window for a, b in zip(timestamps, timestamps[1:])):
            retouched += 1

    if touched == 0:
        return 0.0
    return retouched / touched


def co_change_matrix(commits: list[Commit], max_changeset_size: int = 50) -> Counter[tuple[str, str]]:
    pairs: Counter[tuple[str, str]] = Counter()
    for commit in commits:
        unique_files = sorted(set(commit.files))
        if len(unique_files) < 2 or len(unique_files) > max_changeset_size:
            continue
        for idx, left in enumerate(unique_files):
            for right in unique_files[idx + 1 :]:
                pairs[(left, right)] += 1
    return pairs


def coupling_scores(
    commits: list[Commit],
    target_file: str,
    min_shared_revs: int = 2,
    max_changeset_size: int = 50,
) -> list[dict[str, float | str | int]]:
    commit_count_for_file: Counter[str] = Counter()
    for commit in commits:
        unique_files = set(commit.files)
        if len(unique_files) > max_changeset_size:
            continue
        for file_path in unique_files:
            commit_count_for_file[file_path] += 1

    matrix = co_change_matrix(commits, max_changeset_size=max_changeset_size)
    rows: list[dict[str, float | str | int]] = []

    for (left, right), shared in matrix.items():
        if shared < min_shared_revs:
            continue
        if target_file not in {left, right}:
            continue
        other = right if left == target_file else left
        base = commit_count_for_file[target_file] or 1
        score = shared / base
        rows.append(
            {
                "file": target_file,
                "other_file": other,
                "shared_commits": shared,
                "target_commit_touches": base,
                "coupling": round(score, 4),
            }
        )

    rows.sort(key=lambda r: (r["shared_commits"], r["coupling"]), reverse=True)
    return rows


def churn_velocity(
    commits: list[Commit],
    file_path: str,
    bucket_days: int = 7,
) -> list[dict[str, int | str]]:
    target_commits = [c for c in commits if file_path in c.file_stats]
    if not target_commits:
        return []
    target_commits.sort(key=lambda c: c.ts)

    start = target_commits[0].ts
    buckets: dict[int, dict[str, int | str]] = {}

    for commit in target_commits:
        bucket = int((commit.ts - start).days / bucket_days)
        row = buckets.setdefault(
            bucket,
            {
                "bucket_index": bucket,
                "bucket_start": commit.ts.date().isoformat(),
                "commit_touches": 0,
                "insertions": 0,
                "deletions": 0,
            },
        )
        ins, dels = commit.file_stats.get(file_path, (0, 0))
        row["commit_touches"] = int(row["commit_touches"]) + 1
        row["insertions"] = int(row["insertions"]) + ins
        row["deletions"] = int(row["deletions"]) + dels

    return [buckets[k] for k in sorted(buckets)]


def per_file_retouch_ratio(commits: list[Commit], file_path: str, window_days: int = 7) -> float:
    stamps = sorted(c.ts for c in commits if file_path in c.files)
    if len(stamps) < 2:
        return 0.0
    window = timedelta(days=window_days)
    retouches = sum(1 for a, b in zip(stamps, stamps[1:]) if (b - a) <= window)
    return retouches / (len(stamps) - 1)


def median_or_none(values: list[float]) -> float | None:
    return median(values) if values else None
