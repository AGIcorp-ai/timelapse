from __future__ import annotations

import unittest
from datetime import datetime, timezone

from analyze_repo import build_repo_json
from lib.data_loaders import Commit, Prompt


class AnalyzeRepoTests(unittest.TestCase):
    def test_build_repo_json_shape(self) -> None:
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        end = datetime(2026, 1, 31, tzinfo=timezone.utc)
        commits = [
            Commit(
                repo="4D-bot",
                sha="a" * 40,
                ts=datetime(2026, 1, 2, tzinfo=timezone.utc),
                subject="init",
                files=["a.py"],
                insertions=10,
                deletions=2,
                file_stats={"a.py": (10, 2)},
            )
        ]
        prompts = [
            Prompt(
                repo="4D-bot",
                ts=datetime(2026, 1, 2, 0, 0, tzinfo=timezone.utc),
                source="codex",
                text="do x",
            )
        ]

        payload = build_repo_json(commits, prompts, start, end)
        self.assertIn("schema_version", payload)
        self.assertIn("throughput", payload)
        self.assertIn("commits", payload)
        self.assertEqual(payload["throughput"]["commits"], 1)


if __name__ == "__main__":
    unittest.main()
