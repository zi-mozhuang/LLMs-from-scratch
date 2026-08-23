"""pipeline.merge — 块级合并（从 p1_text_stream / fix_line_continuity 原样搬运）。

4 个纯函数，输入输出均为 list[Block]：
1. merge_pending_prose    : 跨块段落合并
2. merge_pending_code     : 代码清单合并（图片/绘图块不打断）
3. merge_two_line_headings: 两行章节标题合并
4. dehyphenate_text       : 连字符断词处理（词表唯一来源 = mdlib.config，
                            等价旧链 p2_clean.dehyphenate + KNOWN_BREAKS）
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
    # 注：不设 len(cur) 下限——PDF 段落末行通常正是短行，
    # 短续行 + 长未完上句 + 小写开头已足够判别（审计 L11302 案例）。
    # 注：不设 len(cur) 下限——PDF 段落末行通常正是短行（审计 L11302 案例）。
    if len(prev) < 40:
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
_URL_TAIL_RE = re.compile(r"https?://\S*$")


def _url_direct_join(prev: str, cur: str):
    """URL 在 PDF 行宽处折断的跨块无缝拼接（不加空格）：
      …arxiv.org/abs/ + 2405.14394 → abs/2405.14394（行尾斜杠，下行纯数字）
      …LLMs-from     + -scratch    → LLMs-from-scratch（排版把连字符留行首）
    仅当 prev 尾部恰为 URL 且 cur 以 '-' 或数字开头才触发；
    字母开头的 cur 可能是新段落，不并。非 URL 断裂返回 None。"""
    if not _URL_TAIL_RE.search(prev):
        return None
    if re.match(r"-\w", cur) or re.match(r"\d", cur):
        return prev + cur
    return None


def merge_pending_prose(blocks: list) -> list:
    """跨块段落合并：相邻、均为文本流（prose 或 callout）、上块不以终止
    标点结尾则合并文本。旧链 p1 的 pending_prose 缓冲对所有文本行一视同仁，
    \uf0a1 引用行与其续行段落在 p1 阶段即已合并为一段（p2 再整体转 `> `），
    故 callout 块在此按同规则吸收后续 prose。

    允许跨页：段落常在物理页边界截断（审计确认 ~35 处断行皆源于此）；
    _should_merge_prose 的终止符/开放词守卫足以防止把下页新段落误接。
    """
    out: list = []
    pending = None  # (block, text)
    for b in blocks:
        if b.kind in ("prose", "callout"):
            txt = b.text.strip()
            if pending is not None:
                prev_block, prev_txt = pending
                _pa = getattr(prev_block, "meta", {}).get("annot")
                _ca = getattr(b, "meta", {}).get("annot")
                if _pa != _ca:
                    # 标签↔正文互不吸收（错位污染治理）；两侧同为标签时
                    # 落入下方常规合并（标签自身折行的续行需并回）
                    out.append(prev_block)
                    pending = (b, txt)
                    continue
                direct = _url_direct_join(prev_txt, txt)
                if direct is not None:
                    prev_block.text = direct
                    pending = (prev_block, direct)
                    continue
                if (prev_block.kind == b.kind == "prose"
                        and _should_merge_prose(prev_txt, txt)):
                    prev_block.text = (prev_txt + " " + txt).strip()
                    pending = (prev_block, prev_block.text.strip())
                    continue
                if (prev_block.kind == "callout"
                        and b.kind == "prose"
                        and _should_merge_prose(prev_txt.replace("\uf0a1", ""), txt)):
                    # 续行并入 callout 块（等价 p1 行级合并）
                    prev_block.text = (prev_txt + " " + txt).strip()
                    pending = (prev_block, prev_block.text.strip())
                    continue
                out.append(prev_block)
                pending = None
            pending = (b, txt)
        else:
            if pending is not None:
                out.append(pending[0])
                pending = None
            out.append(b)
    if pending is not None:
        out.append(pending[0])
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


# 搬运 p2_clean.py 第 52 行：断词正则（连字符 + 空格）。
BREAK_RE = re.compile(r"(\w+)- (\w+)")


def _dehyphen_replace(m: re.Match) -> str:
    """搬运 p2_clean.py 第 55-69 行 _dehyphen_replace。"""
    left, right = m.group(1), m.group(2)
    low = left.lower()
    # 真复合词前缀（self-, multi-, in-, state-, ...）：保留原样不合并。
    if low in config.COMPOUND_PREFIXES:
        return m.group(0)
    # 通常为断词的前缀：合并（under-stand -> understand, over-view -> overview, ...）。
    if low in config.JOIN_PREFIXES:
        return left + right
    # 右侧为大写开头（可能是真复合词/专有名词）：保留原样。
    if right[0].isupper():
        return m.group(0)
    return left + right


def dehyphenate_text(text: str) -> str:
    """连字符断词处理：搬运 p2_clean.dehyphenate（第 72-79 行，迭代至稳定）
    + fix_line_continuity.dehyphenate_line（第 187-191 行，KNOWN_BREAKS 精确修复）。
    词表唯一来源 = mdlib.config（消除旧链两套词表的矛盾）。
    """
    prev = None
    cur = text
    # 迭代直至稳定（处理链式断词）
    while prev != cur:
        prev = cur
        cur = BREAK_RE.sub(_dehyphen_replace, cur)
    for broken, fixed in config.KNOWN_BREAKS.items():
        if broken in cur:
            cur = cur.replace(broken, fixed)
    return cur
