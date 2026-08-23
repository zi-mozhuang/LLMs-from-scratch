"""mdlib.textutil — 共享文本工具（从旧脚本原样搬运）。

来源：
- `fence_mask` 搬运自 fix_line_continuity.py 的 code_fence_mask（原第 75 行）。
- `norm` / `norm_keep_case` 搬运自 fix_special_blocks.py 第 28 / 36 行（最完整版本）。
"""

import re
import unicodedata


def fence_mask(lines: list[str]) -> list[bool]:
    """Boolean array: True where the line sits inside a fenced code block.
    兼容 GFM 引用内围栏："> ```" 行同样切换状态（CommonMark 语义）。"""
    mask = [False] * len(lines)
    fence = False
    for k, line in enumerate(lines):
        s = line.lstrip()
        if s.startswith(">"):
            s = s[1:].lstrip()
        if s.startswith("```"):
            fence = not fence
            continue
        mask[k] = fence
    return mask


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", s)
    s = s.replace("\u00AD", "")  # soft hyphen
    # dehyphenate: word- space word -> wordword (but keep self-attention etc where hyphen has no space)
    s = re.sub(r'(\w+)-\s+(\w+)', r'\1\2', s)
    s = re.sub(r'\s+', ' ', s).strip().lower()
    return s


def norm_keep_case(s: str) -> str:
    """NFKC + whitespace collapse but keep case, for display comparison."""
    s = unicodedata.normalize("NFKC", s)
    s = s.replace("\u00AD", "")
    s = re.sub(r'(\w+)-\s+(\w+)', r'\1\2', s)
    s = re.sub(r'\s+', ' ', s).strip()
    return s
