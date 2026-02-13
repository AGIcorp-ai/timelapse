from __future__ import annotations

import ast
import re
from collections import Counter
from dataclasses import dataclass


@dataclass
class DiffHunk:
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    header: str
    added_lines: list[int]
    deleted_lines: list[int]


class _SymbolVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.stack: list[str] = []
        self.symbols: dict[str, tuple[int, int]] = {}

    def _add_symbol(self, node: ast.AST, name: str) -> None:
        start = int(getattr(node, "lineno", 0) or 0)
        end = int(getattr(node, "end_lineno", 0) or start)
        if start <= 0:
            return
        full_name = ".".join(self.stack + [name]) if self.stack else name
        self.symbols[full_name] = (start, end)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._add_symbol(node, node.name)
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._add_symbol(node, node.name)
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._add_symbol(node, node.name)
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()


def extract_symbols(source: str) -> dict[str, tuple[int, int]]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return {}
    visitor = _SymbolVisitor()
    visitor.visit(tree)
    return visitor.symbols


_HUNK_RE = re.compile(r"^@@\s+-(\d+)(?:,(\d+))?\s+\+(\d+)(?:,(\d+))?\s+@@\s*(.*)$")


def parse_diff_hunks(diff_text: str) -> list[DiffHunk]:
    hunks: list[DiffHunk] = []
    current: DiffHunk | None = None
    old_line = 0
    new_line = 0

    for line in diff_text.splitlines():
        m = _HUNK_RE.match(line)
        if m:
            if current is not None:
                hunks.append(current)

            old_start = int(m.group(1))
            old_count = int(m.group(2) or "1")
            new_start = int(m.group(3))
            new_count = int(m.group(4) or "1")
            header = m.group(5).strip()
            current = DiffHunk(
                old_start=old_start,
                old_count=old_count,
                new_start=new_start,
                new_count=new_count,
                header=header,
                added_lines=[],
                deleted_lines=[],
            )
            old_line = old_start
            new_line = new_start
            continue

        if current is None:
            continue
        if line.startswith("+++") or line.startswith("---"):
            continue

        if line.startswith("+"):
            current.added_lines.append(new_line)
            new_line += 1
        elif line.startswith("-"):
            current.deleted_lines.append(old_line)
            old_line += 1
        else:
            old_line += 1
            new_line += 1

    if current is not None:
        hunks.append(current)
    return hunks


def map_hunks_to_symbols(
    hunks: list[DiffHunk],
    symbols: dict[str, tuple[int, int]],
) -> Counter[str]:
    touches: Counter[str] = Counter()
    if not symbols:
        return touches

    for hunk in hunks:
        changed = set(hunk.added_lines) | set(hunk.deleted_lines)
        for symbol, (start, end) in symbols.items():
            if any(start <= ln <= end for ln in changed):
                touches[symbol] += 1
    return touches


def symbols_from_hunk_headers(hunks: list[DiffHunk]) -> Counter[str]:
    counts: Counter[str] = Counter()
    for hunk in hunks:
        header = hunk.header
        if not header:
            continue
        m = re.search(r"(?:def|class|function)\s+([A-Za-z_][A-Za-z0-9_]*)", header)
        if m:
            counts[m.group(1)] += 1
        else:
            counts[header[:80]] += 1
    return counts
