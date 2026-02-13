from __future__ import annotations

import unittest

from lib.symbol_extractor import extract_symbols, map_hunks_to_symbols, parse_diff_hunks, symbols_from_hunk_headers


class SymbolExtractorTests(unittest.TestCase):
    def test_extract_symbols_python(self) -> None:
        src = """
class A:
    def f(self):
        return 1

def g():
    return 2
"""
        symbols = extract_symbols(src)
        self.assertIn("A", symbols)
        self.assertIn("A.f", symbols)
        self.assertIn("g", symbols)

    def test_parse_diff_hunks_and_map(self) -> None:
        diff = """@@ -1,2 +1,3 @@ def g
-line1
+line1a
+line2
"""
        hunks = parse_diff_hunks(diff)
        self.assertEqual(len(hunks), 1)
        symbols = {"g": (1, 10)}
        touched = map_hunks_to_symbols(hunks, symbols)
        self.assertGreaterEqual(touched["g"], 1)

    def test_fallback_hunk_headers(self) -> None:
        diff = """@@ -10,2 +10,2 @@ function demo
-a
+b
"""
        hunks = parse_diff_hunks(diff)
        touched = symbols_from_hunk_headers(hunks)
        self.assertIn("demo", touched)


if __name__ == "__main__":
    unittest.main()
