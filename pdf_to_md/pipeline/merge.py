"""pipeline.merge — 块级合并（从 p1_text_stream / fix_line_continuity 原样搬运）。

4 个纯函数，输入输出均为 list[Block]：
1. merge_pending_prose    : 跨块段落合并
2. merge_pending_code     : 代码清单合并（图片/绘图块不打断）
3. merge_two_line_headings: 两行章节标题合并
4. dehyphenate_text       : 连字符断词合并（唯一词表 = mdlib.config.KEEP_HYPHEN_PREFIXES）
"""

import re

from mdlib import config
from mdlib.textutil import norm

# ---- 搬运自 p1_text_stream 的常量（pdf_to_text_stream 内部闭包） ----
_FIG_RE = re.compile(r"^\*{0,2}Figure\s+\d+\.\d+")
_CHAPTER_LABEL_RE = re.compile(r"^(chapter|appendix)\s+[A-Z0-9]", re.I)
_PROSE_OPENERS = {
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
_CLOSE = {")": ")", "]": "]", "}": "}", '"': '"', "'": "'"}
LEADIN_RE = re.compile(
    r"(covers|include|includes|chapter|derived|follows|discussed|"
    r"introduced|covering|explores|examines|presents|describes)\s*$",
    re.I,
)


# --------------------------------------------------------------------------- #
# 搬运自 p1_text_stream 的判定闭包（_should_merge_prose / _is_two_line_heading
# / _is_chapter_label_then_title），仅操作两个字符串。
# --------------------------------------------------------------------------- #
def _is_chapter_label_then_title(prev: str, cur: str) -> bool:
    if not prev or not cur:
        return False
    if not _CHAPTER_LABEL_RE.match(prev.strip()):
        return False
    cur = cur.strip()
    return bool(cur) and cur[0].isupper() and len(cur) < 80


def _should_merge_prose(prev: str, cur: str) -> bool:
    if not prev or not cur:
        return False
    if _FIG_RE.match(prev) or _FIG_RE.match(cur):
        return False
    if prev[-1] in ".!?\"'”’":
        return False
    # (2) inline-code / unclosed-delimiter continuation
    if cur.startswith("`"):
        return True
    if prev[-1] == "`":
        return True
    for o in ("(", "[", "{", '"', "'"):
        if prev.count(o) > prev.count(_CLOSE.get(o, o)):
            return True
    # (3) open-word continuation
    last_word = prev.split()[-1].strip("\"'()[]{}").lower()
    if last_word in _PROSE_OPENERS:
        return True
    # (4) hyphenated word split across blocks
    if prev.rstrip().endswith("-"):
        return True
    # (1) ordinary prose continuation
    if len(prev) < 40 or len(cur) < 40:
        return False
    if not cur[0].islower():
        return False
    return True


def _is_two_line_heading(prev: str, cur: str) -> bool:
    if not prev or not cur:
        return False
    if len(prev) > 50 or len(cur) > 50:
        return False
    if prev[-1] in ".!?\"'”’":
        return False
    if prev[-1] == ")":
        return False
    if not cur[0].islower():
        return False
    if LEADIN_RE.search(prev):
        return False
    return True


# --------------------------------------------------------------------------- #
# 块级合并纯函数                                                              #
# --------------------------------------------------------------------------- #
def merge_pending_prose(blocks: list) -> list:
    """跨块段落合并：相邻、同页、均为 prose、上块不以终止标点结尾则合并文本。"""
    out: list = []
    pending = None  # (block, text)
    for b in blocks:
        if b.kind == "prose":
            txt = b.text.strip()
            if pending is not None:
                prev_txt, prev_block = pending
                if (prev_block.page == b.page
                        and _should_merge_prose(prev_txt, txt)):
                    prev_block.text = (prev_txt + " " + txt).strip()
                    pending = (prev_block.text, prev_block)
                    continue
                else:
                    out.append(prev_block)
                    pending = None
            pending = (txt, b)
        else:
            if pending is not None:
                out.append(pending[1])
                pending = None
            out.append(b)
    if pending is not None:
        out.append(pending[1])
    return out


def merge_pending_code(blocks: list) -> list:
    """代码清单合并：相邻 code 块合并为一块（图片/绘图块不打断需外部保证，
    这里处理 code 的连续折叠：相邻 code 块合并，中间若夹非 code 则断开）。"""
    out: list = []
    pending_code = None
    for b in blocks:
        if b.kind == "code":
            if pending_code is not None:
                # 连续 code 块：合并文本，保留首个 lang
                pending_code.text = (pending_code.text + "\n" + b.text).strip("\n")
            else:
                pending_code = b
                out.append(b)
        else:
            pending_code = None
            out.append(b)
    return out


def merge_two_line_headings(blocks: list) -> list:
    """两行章节标题合并：相邻两 prose 块若满足 _is_two_line_heading，
    合并为一块 prose（标题化在 render 阶段按 TOC 重新判定）。"""
    out: list = []
    pending = None
    for b in blocks:
        if b.kind == "prose" and pending is not None:
            prev_txt = pending.text.strip()
            cur_txt = b.text.strip()
            if (pending.page == b.page
                    and _is_two_line_heading(prev_txt, cur_txt)):
                pending.text = (prev_txt + " " + cur_txt).strip()
                continue
            else:
                out.append(pending)
                pending = b
        elif b.kind == "prose":
            pending = b
        else:
            if pending is not None:
                out.append(pending)
                pending = None
            out.append(b)
    if pending is not None:
        out.append(pending)
    return out


def dehyphenate_text(text: str) -> str:
    """连字符断词合并：word- 空格 word -> wordword，但保留真复合词前缀连字符。
    唯一词表 = mdlib.config.KEEP_HYPHEN_PREFIXES（消除旧矛盾）。
    （搬运自 fix_line_continuity.dehyphenate_line 思路，词表以 config 为准。）
    """
    def repl(m: re.Match) -> str:
        prefix = m.group(1)
        if prefix in config.KEEP_HYPHEN_PREFIXES:
            return f"{prefix}-{m.group(2)}"
        return prefix + m.group(2)
    return re.sub(r"\b([a-zA-Z]+)-\s+([a-zA-Z]+)", repl, text)
