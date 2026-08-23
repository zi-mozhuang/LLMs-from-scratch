"""pipeline.merge — 块级合并（从 p1_text_stream / fix_line_continuity 原样搬运）。

5 个纯函数，输入输出均为 list[Block]：
1. merge_box_fragments     : 同矩形概念框碎片合并（classify 打 box_key）
2. merge_pending_prose    : 跨块段落合并
3. merge_pending_code     : 代码清单合并（图片/绘图块不打断）
4. merge_two_line_headings: 两行章节标题合并
5. dehyphenate_text       : 连字符断词处理（词表唯一来源 = mdlib.config，
                            等价旧链 p2_clean.dehyphenate + KNOWN_BREAKS）
"""

import re

from mdlib import config
from mdlib.textutil import norm
from pipeline.ir import BOX_GROUP_KINDS

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


def _collapse_para(text: str) -> str:
    """框内碎片 → 单段文本：去 PUA bullet、折叠空白（等价 classify._merge_body_lines）。"""
    return " ".join(text.replace("\uf0a1", " ").split()).strip()


def _absorb_paragraphs(paras: list, new_paras: list) -> None:
    """按句连续性把新段落并入 paras（段间语义与 merge_pending_prose 同源：
    上段未终止 + 续段小写/开放词 → 空格拼接；否则另起一段）。"""
    for p in new_paras:
        if not p:
            continue
        if paras and _should_merge_prose(paras[-1], p):
            paras[-1] = (paras[-1] + " " + p).strip()
        else:
            paras.append(p)


def merge_box_fragments(blocks: list) -> list:
    """同矩形概念框碎片合并（audit_quote_fragmentation 结构性修复）。

    classify 已对落入同一 CALLOUT_FILL 矩形的相邻块打 meta["box_key"]。
    此处把文档流中连续同 key 的块并回单一视觉引用块：
    - 首块为 exercise：其余块的文本作为多段并入其 text（渲染层输出
      `> **Exercise…**` + `>` 分隔的多段引用，匹配印刷版整框练习）；
    - 否则统一转为 concept_box：title 取组内首个非空标题，bodies 按序
      吸收（句连续性拼接）。
    跨页矩形不归组（box_key 含页号）；图注/代码/标题出现即打断归组。
    幂等：合并后单块不再成组。"""
    out: list = []
    i, n = 0, len(blocks)
    while i < n:
        b = blocks[i]
        key = b.meta.get("box_key")
        if not key or b.kind not in BOX_GROUP_KINDS:
            out.append(b)
            i += 1
            continue
        group = [b]
        j = i + 1
        while (j < n and blocks[j].meta.get("box_key") == key
               and blocks[j].kind in BOX_GROUP_KINDS):
            group.append(blocks[j])
            j += 1
        if len(group) == 1:
            out.append(b)
        else:
            _merge_box_group(group)
            out.append(group[0])
        i = j
    return out


def _merge_box_group(group: list) -> None:
    """把同框块组合并为首块（就地修改）。"""
    leader = group[0]
    if leader.kind == "exercise":
        head = _collapse_para(leader.text)
        paras = [head]
        for b in group[1:]:
            bodies = b.meta.get("bodies") or []
            if bodies:
                _absorb_paragraphs(paras, [x.strip() for x in bodies if x.strip()])
                # bodies 之外的残留文本（罕见）也吸收
                continue
            t = _collapse_para(b.text)
            if t:
                _absorb_paragraphs(paras, [t])
        # 标题去重（同 concept_box 分支：后续块文本与标题重复时丢弃）
        body = [p for p in paras[1:] if norm(p) != norm(head)]
        leader.text = "\n".join([head] + body)
        return
    title = leader.meta.get("title", "")
    paras: list = []
    _absorb_paragraphs(paras, [x.strip() for x in (leader.meta.get("bodies") or []) if x.strip()])
    if not paras:
        t = _collapse_para(leader.text)
        if t:
            paras.append(t)
    for b in group[1:]:
        if not title:
            title = b.meta.get("title", "")
        bodies = b.meta.get("bodies") or []
        if bodies:
            _absorb_paragraphs(paras, [x.strip() for x in bodies if x.strip()])
        else:
            _absorb_paragraphs(paras, [_collapse_para(b.text)])
    # 标题去重：leader 无 meta 标题时其文本已作首段，后续块又提供同名
    # 标题（如 ^Exercise N.N 块先命中 concept_box 判定、Demi span 进 meta）
    # → 首段与标题重复，丢弃（norm 精确比较）。
    if title and paras and norm(paras[0]) == norm(title):
        paras.pop(0)
    leader.kind = "concept_box"
    leader.meta["title"] = title
    leader.meta["bodies"] = paras


# --------------------------------------------------------------------------- #
# Listing 旁注归位                                                             #
# --------------------------------------------------------------------------- #
def merge_listing_callouts(blocks: list) -> list:
    """Listing 旁注 → 代码注释：classify._mark_listing_callouts 标记的块
    （书版排版中指向代码行的箭头注释短语）折叠为单行 `# ...` 追加到所属
    code block 文本尾部，原块删除。两级归属：
    1. 流序：紧跟 code block 之后 ≤6 个块内直接挂（绝大多数情形）；
    2. 几何兜底：间隔超限者按同页 y-overlap 最大的 code block 归属
       （旁注与代码间可能隔着图注/段落块）。
    两级都失败则维持散段落现状（无害）。"""
    def _fold(b):
        return " ".join(b.text.split())

    def _yov(a, c):
        return min(a.bbox[3], c.bbox[3]) - max(a.bbox[1], c.bbox[1])

    out: list = []
    pending_code = None   # 最近一个代码块（流序一级分配）
    gap = 0               # 距该代码块的 intervening 块数
    attached = 0
    for b in blocks:
        if b.kind == "code":
            pending_code = b
            gap = 0
            out.append(b)
            continue
        gap += 1
        txt = _fold(b)
        if b.meta.get("listing_callout") and txt \
                and pending_code is not None and gap <= 6:
            pending_code.text = (pending_code.text.rstrip("\n")
                                 + "\n\n# " + txt)
            attached += 1
            continue  # 块删除
        if b.meta.get("listing_callout"):
            b.meta["callout_deferred"] = True   # 二次几何分配候选
        out.append(b)

    # 二级：deferred 按同页几何归属
    codes = [b for b in blocks if b.kind == "code"]
    for b in out:
        if not b.meta.get("callout_deferred"):
            continue
        cands = [c for c in codes if c.page == b.page and _yov(b, c) > 5]
        if cands:
            best = max(cands, key=lambda c: _yov(b, c))
            best.text = best.text.rstrip("\n") + "\n\n# " + _fold(b)
            attached += 1
            b.meta["callout_consumed"] = True
    out = [b for b in out if not b.meta.pop("callout_consumed", False)]
    for b in out:
        b.meta.pop("callout_deferred", None)
    merge_listing_callouts.last_attached = attached
    return out


# --------------------------------------------------------------------------- #
# 章末 Summary 列表重组                                                        #
# --------------------------------------------------------------------------- #
_SUMMARY_SUBITEM_RE = re.compile(r"^[–•]\s+(.*)$")


def _build_summary_items(parts: list) -> list:
    """Summary 文本片段 → 多级列表项 [(level, text)]。

    结构信号（无硬码）：
    - 含 \\uf0a1 的行 → 一级项起点（Wingdings 圆点）
    - 行首 –/• → 二级项（PDF 原生子子弹）
    - 其余行 → 当前项的折行续行，按 _should_merge_prose 同源语义空格拼接
      （悬挂连字符 "previ- ously" 交由 render 层 BREAK_RE + 词表裁决）
    """
    items: list = []  # [level, text]
    for raw in "\n".join(parts).split("\n"):
        line = raw.strip()
        if not line:
            continue
        if "\uf0a1" in line:
            txt = line.replace("\uf0a1", " ").strip()
            if txt:
                items.append([1, txt])
            continue
        m = _SUMMARY_SUBITEM_RE.match(line)
        if m:
            items.append([2, m.group(1).strip()])
            continue
        if not items:
            items.append([1, line])
            continue
        lvl, txt = items[-1]
        # 断词保持 "xx- yy" 空格形：render 步骤 1.5 的 BREAK_RE + 词表
        # （COMPOUND/JOIN_PREFIXES、KNOWN_BREAKS）统一裁决，与正文同语义。
        items[-1][1] = txt + " " + line
    return items


def merge_summary_items(blocks: list) -> list:
    """章末 Summary 列表重组：summary_list 首块（\\uf0a1 圆点）与其后的
    折行 prose 续块合并为单个列表块（meta["items"]），render 据此输出
    `- `/`  - ` 列表。须先于 merge_pending_prose 执行——否则续块会被
    prose 合并链吸收成独立段落（现状孤行/断段缺陷的根因）。"""
    out: list = []
    i, n = 0, len(blocks)
    while i < n:
        b = blocks[i]
        if b.kind != "summary_list":
            out.append(b)
            i += 1
            continue
        parts = [b.text]
        j = i + 1
        while j < n and blocks[j].kind == "prose" \
                and not getattr(blocks[j], "meta", {}).get("annot"):
            parts.append(blocks[j].text)
            j += 1
        b.meta["items"] = _build_summary_items(parts)
        out.append(b)
        i = j
    return out


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


# --------------------------------------------------------------------------- #
# 概念框内代码 → 复合引用框                                                    #
# --------------------------------------------------------------------------- #
def merge_box_code(blocks: list) -> list:
    """框内代码合成复合 concept_box：书版概念框内部嵌代码示例时，
    code 判定优先于框归属被剥出（classify 规则序），渲染成
    "引用-裸围栏-引用"三明治断裂。此处把 [concept_box(key), code(in_box),
    concept_box(key), …] 交替链合并为单个复合框，bodies 升级为 typed 元素：
      {"t": "text", "v": str} / {"t": "code", "v": str, "lang": str}
    render 据此输出 GFM 引用内围栏（"> ```"），空行以 ">" 延续保持盒子连续。
    须在 merge_box_fragments 之后执行。"""
    out: list = []
    i, n = 0, len(blocks)
    while i < n:
        b = blocks[i]
        if b.kind == "concept_box" and b.meta.get("box_key") and i + 1 < n \
                and blocks[i + 1].kind == "code" \
                and blocks[i + 1].meta.get("in_concept_box"):
            chain = [b]
            j = i + 1
            while True:
                if j < n and blocks[j].kind == "code" \
                        and blocks[j].meta.get("in_concept_box") \
                        and blocks[j].page == b.page:
                    chain.append(blocks[j])
                    j += 1
                    # 后续同 key 文字段继续入链
                    while j < n and blocks[j].kind == "concept_box" \
                            and blocks[j].meta.get("box_key"):
                        chain.append(blocks[j])
                        j += 1
                    continue
                break
            if len(chain) > 1:
                _merge_box_code_group(chain)
                out.append(chain[0])
                i = j
                continue
        out.append(b)
        i += 1
    return out


def _merge_box_code_group(chain: list) -> None:
    """链首块吸收为复合框：bodies 全量转为 typed 结构。"""
    head = chain[0]
    bodies = head.meta.get("bodies") or [head.text]
    typed = [{"t": "text", "v": x} if isinstance(x, str) else x for x in bodies]
    for b in chain[1:]:
        if b.kind == "code":
            typed.append({"t": "code", "v": b.text.rstrip("\n"),
                          "lang": b.lang or "python"})
            merge_box_code.last_codes += 1
        else:  # 同 key 文字段
            for x in (b.meta.get("bodies") or [b.text]):
                if isinstance(x, dict):
                    typed.append(x)
                elif x.strip():
                    typed.append({"t": "text", "v": x})
    head.meta["bodies"] = typed


merge_box_code.last_codes = 0


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
    # 悬挂连字符守卫：英文排版的并列悬挂式 "X- and Y-" / "X- or Y-"
    # （如 "time- and resource-intensive"、"GPT- and BERT-like"）是合法原样，
    # 不是断词——连词开头的小写右侧一律保留。
    if right.lower() in ("and", "or", "nor", "to"):
        return m.group(0)
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
