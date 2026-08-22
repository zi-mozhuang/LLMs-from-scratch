"""mdlib.cli — 读写辅助（从 fix_structure.py 第 925-931 行原样搬运）。

`_load_lines/_write_back` 改为公开命名 `load_lines/write_back`。
"""

from pathlib import Path


def load_lines(md_path: Path, apply: bool) -> list[str]:
    return md_path.read_text(encoding="utf-8").split("\n")


def write_back(md_path: Path, lines: list[str], apply: bool) -> None:
    if apply:
        md_path.write_text("\n".join(lines), encoding="utf-8")
