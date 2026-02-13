from __future__ import annotations

import unittest
from datetime import datetime, timezone

from lib.data_loaders import Commit, Prompt
from lib.metrics import co_change_matrix, coupling_scores, nearest_prompt_lags_hours, rework_ratio


class MetricsTests(unittest.TestCase):
    def _commit(self, sha: str, ts_hour: int, files: list[str]) -> Commit:
        ts = datetime(2026, 2, 1, ts_hour, 0, tzinfo=timezone.utc)
        stats = {f: (1, 0) for f in files}
        return Commit(
            repo="4D-bot",
            sha=sha,
            ts=ts,
            subject="s",
            files=files,
            insertions=len(files),
            deletions=0,
            file_stats=stats,
        )

    def test_co_change_matrix_counts_pairs(self) -> None:
        commits = [
            self._commit("a" * 40, 1, ["a.py", "b.py"]),
            self._commit("b" * 40, 2, ["a.py", "b.py", "c.py"]),
        ]
        pairs = co_change_matrix(commits)
        self.assertEqual(pairs[("a.py", "b.py")], 2)
        self.assertEqual(pairs[("a.py", "c.py")], 1)

    def test_coupling_scores_filters_target(self) -> None:
        commits = [
            self._commit("a" * 40, 1, ["target.py", "x.py"]),
            self._commit("b" * 40, 2, ["target.py", "x.py"]),
            self._commit("c" * 40, 3, ["target.py", "y.py"]),
        ]
        rows = coupling_scores(commits, "target.py", min_shared_revs=2)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["other_file"], "x.py")

    def test_rework_ratio(self) -> None:
        commits = [
            self._commit("a" * 40, 1, ["a.py"]),
            self._commit("b" * 40, 2, ["a.py"]),
        ]
        self.assertGreater(rework_ratio(commits, 7), 0.0)

    def test_nearest_prompt_lags(self) -> None:
        commit = self._commit("a" * 40, 6, ["a.py"])
        prompts = [
            Prompt(repo="4D-bot", ts=datetime(2026, 2, 1, 5, 30, tzinfo=timezone.utc), source="codex", text="x")
        ]
        lags = nearest_prompt_lags_hours([commit], prompts)
        self.assertEqual(len(lags), 1)
        self.assertAlmostEqual(lags[0], 0.5)


if __name__ == "__main__":
    unittest.main()
