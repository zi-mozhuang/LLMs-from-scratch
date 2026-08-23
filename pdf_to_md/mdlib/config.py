"""mdlib.config — 集中共享常量（从旧脚本原样搬运）。

来源：
- PDF_PATH / MD_PATH / MANIFEST_PATH：指向当前目录的 PDF / llms-from-scratch.md / manifest.json。
- KEEP_HYPHEN_PREFIXES：fix_line_continuity.py 第 47-51 行（以此为准；p2_clean.py 第 32-41 旧词表作废）。
- OPENERS：fix_line_continuity.py 第 55 行起。
- HEADER_FONTS / PAGE_NUMBER_RE：p1_text_stream.py 第 39-43、76 行。
- CALLOUT_FILL / LISTING_FILL：fix_special_blocks.py 第 25-26 行。
- PUA_MAP：p2_clean.py 第 44-47 行。
"""

from pathlib import Path

# 当前目录的 PDF / 最终 MD / 图片 manifest。
PDF_PATH = Path(__file__).resolve().parent.parent / "Build a Large Language Model (From Scratch) (Sebastian Raschka).pdf"
MD_PATH = Path(__file__).resolve().parent.parent / "llms-from-scratch.md"
MANIFEST_PATH = Path(__file__).resolve().parent.parent / "extracted_images" / "manifest.json"

# 断词保留连字符的真复合词前缀（fix_line_continuity.py 第 47-51 行）。
KEEP_HYPHEN_PREFIXES = {
    "self", "state", "well", "cross", "multi", "non", "anti", "bio", "co",
    "sub", "semi", "auto", "inter", "intra", "super", "micro", "macro",
    "neuro", "socio", "geo", "cyber", "open", "near", "far",
}

# ---- 连字符处理词表（golden 行为的搬运，消除旧链矛盾的唯一实现） ----
# 真复合词前缀：保留连字符不合并（p2_clean.py 第 32-37 行）。
# e.g. self-attention, multi-head, cross-attention, state-of-the-art,
# in-progress, pre-training, non-linear ...
COMPOUND_PREFIXES = {
    "self", "state", "well", "cross", "multi", "pre", "post", "sub", "non",
    "anti", "bio", "co", "re", "un", "in", "semi", "auto", "inter", "intra",
    "extra", "super", "micro", "macro", "neuro", "socio", "geo", "cyber",
    "open", "near", "far",
}
# 通常为断词的前缀：跨行合并（p2_clean.py 第 38-41 行）。
# under-stand -> understand, over-view -> overview, up-date -> update,
# down-stream -> downstream, out-put -> output.
JOIN_PREFIXES = {"under", "over", "up", "down", "out"}
# 精确断词修复（fix_line_continuity.py 第 178-184 行）：
# p2 的通用合并因 COMPOUND_PREFIXES 保守保留 pre-/post- 而漏掉的已知断词；
# 其余一律不动（如 "in- progress" 绝不能变成 "inprogress"）。
KNOWN_BREAKS = {
    "pre- viously": "previously",
    "pre- training": "pre-training",
    "post- erior": "posterior",
    "post- processing": "post-processing",
    "re- cur": "recur",
    # Listing 旁注归位时发现的断词损坏（2025-08，listing callout 审计）
    "nexttoken": "next-token",
    "jsonformatted": "json-formatted",
}

# 句间连接词：行尾出现则下一行为续行（fix_line_continuity.py 第 55-65 行）。
OPENERS = {
    "a", "an", "the", "of", "in", "via", "to", "and", "or", "for", "with",
    "that", "as", "by", "from", "on", "at", "into", "than", "but", "nor",
    "so", "because", "while", "using", "when", "if", "is", "are", "was",
    "were", "be", "been", "being", "this", "these", "those", "it", "its",
    "we", "you", "they", "our", "your", "their", "such", "which", "where",
    "what", "how", "why", "who", "each", "every", "both", "all", "any",
    "between", "among", "within", "without", "during", "before", "after",
    "above", "below", "over", "under", "about", "against", "through",
    "across", "onto", "upon", "out", "up", "down",
}

# 页眉/页脚字体信号（p1_text_stream.py 第 39-43 行）。
HEADER_FONTS = {
    "section_title": ["NewBaskerville-BoldItali"],   # "1.4 Introducing..."
    "page_number":   ["NewBaskerville-Bold"],         # "9", "ix", "xii"
    "chapter_label": ["FranklinGothic-Demi"],         # "CHAPTER 1", "CONTENTS"
}

# 页边短数字/罗马数字 = 页码（p1_text_stream.py 第 76 行）。
import re
PAGE_NUMBER_RE = re.compile(r"^[\divxlcmIVXLCDM]+$")

# 概念框 / Listing 标题填充色（fix_special_blocks.py 第 25-26 行）。
CALLOUT_FILL = (0.969, 0.961, 0.91)
LISTING_FILL = (0.438, 0.652, 0.801)

# PUA 私有区字形 → 真实 unicode（p2_clean.py 第 44-47 行）。
PUA_MAP = {
    "\uf061": "\u03b1",  # alpha
    "\uf077": "\u03c9",  # omega
}
