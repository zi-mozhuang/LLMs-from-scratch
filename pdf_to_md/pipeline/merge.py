"""pipeline.merge — 块级合并（从 p1_text_stream / fix_line_continuity 原样搬运）。

7 个纯函数，输入输出均为 list[Block]：
1. merge_box_fragments     : 同矩形概念框碎片合并（classify 打 box_key）
2. merge_summary_items     : 章末 Summary 列表重组（\uf0a1 圆点 → 列表项）
3. merge_numbered_lists    : 连续递增编号 prose 块 → ol_list（verify 第 13 步对账）
   strip_box_continuation_markers: 跨页概念框 "(continued)" 残标剔除
4. merge_pending_prose     : 跨块段落合并
5. merge_pending_code      : 代码清单合并（图片/绘图块不打断）
6. merge_two_line_headings : 两行章节标题合并
7. dehyphenate_text        : 连字符断词处理（词表唯一来源 = mdlib.config，
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


def _should_merge_prose(prev: str, cur: str, force_open: bool = False) -> bool:
    if not prev or not cur:
        return False
    if _FIG_RE.match(prev) or _FIG_RE.match(cur):
        return False
    if prev[-1] in ".!?\"'”’":
        return False
    # force_open：参考文献 URL 签名等场景，越过开放词/长度/大写守卫强制并入
    if force_open:
        return True
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
def _rect_gap_dist(a, c) -> float:
    import fitz as _f
    ra, rc = _f.Rect(a), _f.Rect(c)
    dx = max(rc.x0 - ra.x1, ra.x0 - rc.x1, 0)
    dy = max(rc.y0 - ra.y1, ra.y0 - rc.y1, 0)
    return (dx * dx + dy * dy) ** 0.5


def _target_line_text(page_rows, target_y, target_page):
    """页级行索引中与 target_y（箭头中心）几何最近的 Courier 行文本。
    page_rows: {page: [(cy, text), ...]}。跨页清单的 y 坐标分属各页，
    必须按注块所在页过滤。失败返回 None。"""
    best = None
    for cy, t in page_rows.get(target_page, []):
        d = abs(cy - target_y)
        if best is None or d < best[0]:
            best = (d, t)
    return None if best is None else best[1]


def merge_listing_callouts(blocks: list) -> list:
    """Listing 旁注 → 代码注释：classify._mark_listing_callouts 标记的块
    按 meta['callout_lines']={gid:[(行文本,目标箭头cy),...]} 组装完整短语，
    归入所属代码块后按目标行插入 `# ` 注释（行级定位：目标行取页级行
    索引中与箭头 cy 几何最近者，再在 owner.text 中按行文本定位插入点，
    注释插在目标行上方、缩进随目标行）。一个 PDF 块可向多个组贡献行
    （跨栏混排块），消费时整块删除——其正文语义已全部转移。

    两级归属：
    1. 流序：贡献块之前最近 code block 且间隔 ≤6 块 + 页域/贴近门；
    2. 几何兜底：页域覆盖注块页的 code 中 y-overlap 最大者；无重叠时
       间隙距离 ≤60pt。
    定位失败回退围栏尾部追加；两级归属都失败维持散段落现状（无害）。"""
    # 0) 页级行索引：所有块的行级 Courier 行（不依赖 code 块合并后的
    #    meta 对齐，天然完整）
    page_rows: "dict[int, list]" = {}
    for b in blocks:
        pg = b.page
        for ln in b.meta.get("lines", []):
            sp = [s for s in ln.get("spans", []) if s.get("bbox")
                  and s.get("text", "").strip()]
            if not sp:
                continue
            cou = sum(1 for s in sp if "Courier" in s.get("font", ""))
            if cou * 2 < len(sp):
                continue
            txt = "".join(s["text"] for s in sp).rstrip()
            cy = (min(s["bbox"][1] for s in sp)
                  + max(s["bbox"][3] for s in sp)) / 2
            page_rows.setdefault(pg, []).append((cy, txt))
    # 1) 组装组（流序）：gid -> {"texts":[], "y":cy, "blocks":[]}
    groups: "dict[str, dict]" = {}
    order: list = []
    for b in blocks:
        cl = b.meta.get("callout_lines") if b.meta.get("listing_callout") \
            else None
        if not cl:
            continue
        for gid, items in cl.items():
            if gid not in groups:
                groups[gid] = {"texts": [], "y": None, "blocks": []}
                order.append(gid)
            g = groups[gid]
            g["texts"].extend(t for t, _cy in items)
            if g["y"] is None and items:
                g["y"] = items[0][1]
            if not any(x is b for x in g["blocks"]):
                g["blocks"].append(b)

    # 2) 归属候选链：tier1 流序贴近门给出首选；tier2 几何兜底补全候选，
    #    同页域全部 code 按「纵向重叠降序、间隙升序」入列——插入阶段
    #    逐个尝试直到目标行文本命中（清单被 PDF 拆块时，箭头目标行
    #    可能落在非首选段；单选失败即尾插会造成顶格注释堆叠）
    def _page_ok(code_block, page):
        sp = code_block.meta.get("page_span") or (code_block.page,) * 2
        return sp[0] <= page <= sp[1]

    first_pick: dict = {}
    last_code = None
    since = 0
    for b in blocks:
        if b.kind == "code":
            last_code = b
            since = 0
            continue
        if b.meta.get("listing_callout"):
            cl = b.meta.get("callout_lines") or {}
            if cl and last_code is not None and since <= 6 \
                    and _page_ok(last_code, b.page) \
                    and _rect_gap_dist(b.bbox, last_code.bbox) <= 60.0:
                # 几何贴近门：since 只保证流序近邻，还需注块与 owner
                # 纵向贴邻（重叠或间隙≤60pt），防同页远隔 fence 截胡
                for gid in cl:
                    first_pick.setdefault(gid, last_code)
            continue
        since += 1

    owner_cands: "dict[str, list]" = {}
    for gid in order:
        blks = groups[gid]["blocks"]
        page = blks[0].page
        gb = (min(b.bbox[0] for b in blks), min(b.bbox[1] for b in blks),
              max(b.bbox[2] for b in blks), max(b.bbox[3] for b in blks))
        cands = [c for c in blocks if c.kind == "code" and _page_ok(c, page)]
        scored = []
        for c in cands:
            ov = min(gb[3], c.bbox[3]) - max(gb[1], c.bbox[1])
            gap = _rect_gap_dist(gb, c.bbox)
            scored.append((-min(ov, 10**6), gap, id(c), c))
        scored.sort(key=lambda t: (t[0], t[1]))
        ordered = [c for *_, c in scored]
        fp = first_pick.get(gid)
        if fp is not None:
            if fp in ordered:
                ordered.remove(fp)
            ordered.insert(0, fp)
        owner_cands[gid] = ordered

    # 4) 插入收集与消费标记（候选链逐个尝试，thy 命中即挂）
    ins: "dict[int, list]" = {}
    tails: "dict[int, list]" = {}
    consumed = set()
    attached = 0
    n_unlocatable = 0
    for k, gid in enumerate(order):
        d = groups[gid]
        text = " ".join(t.strip() for t in d["texts"] if t.strip()).strip()
        if not text or gid not in owner_cands:
            continue
        consumed.update(id(f) for f in d["blocks"])
        thy = _target_line_text(page_rows, d["y"], d["blocks"][0].page)
        placed_owner = None
        idx = None
        if thy is not None:
            for cand in owner_cands[gid]:
                tl = cand.text.split("\n")
                for i2, l in enumerate(tl):
                    if l.strip() == thy.strip():
                        idx = i2
                        break
                if idx is not None:
                    placed_owner = cand
                    break
        if placed_owner is None:
            fallback = owner_cands[gid][0]
            tails.setdefault(id(fallback), []).append(text)
            if thy is not None:
                n_unlocatable += 1
        else:
            ins.setdefault(id(placed_owner), []).append((idx, text, k))
        attached += 1

    def apply(owner) -> None:
        items = ins.get(id(owner)) or []
        tl = owner.text.split("\n")
        for idx, text, _k in sorted(items, key=lambda t: (-t[0], -t[2])):
            idx = min(idx, len(tl) - 1)
            # 多行语句的续行修正：若目标行是缩进续行（上一行以 ,/[/（ 结束），
            # 则旁注应归属整条语句，插在首行之前而非续行之间（A.1 tensor2d/3d）
            while idx > 0 and tl[idx].startswith((" ", "\t")) \
                    and tl[idx - 1].rstrip().endswith((",", "(", "[", "{", "\\")):
                idx -= 1
            indent = re.match(r"[ \t]*", tl[idx]).group(0)
            tl.insert(idx, f"{indent}# {text}")
        new_text = "\n".join(tl)
        for text in tails.get(id(owner), []):
            new_text = new_text.rstrip("\n") + "\n\n# " + text
        if items or tails.get(id(owner)):
            owner.text = new_text

    owners = {id(c): c for c in blocks if c.kind == "code"}
    for o in owners.values():
        if id(o) in ins or id(o) in tails:
            apply(o)

    merge_listing_callouts.last_attached = attached
    merge_listing_callouts.last_unlocatable = n_unlocatable
    merge_listing_callouts.last_unowned = [
        (gid, " ".join(groups[gid]["texts"])[:60])
        for gid in order if gid not in owner_cands]
    return [b for b in blocks if id(b) not in consumed]


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


_OL_ITEM_RE = re.compile(r"^(\d{1,2})(?:\s+|\n)([A-Z“\"'(])")


def _ol_item_marker(text: str) -> int | None:
    """prose 块文本的有序列表项编号；非列表项形态返回 None。
    结构信号：1-2 位编号独立起头（PDF 内编号自成一行或紧跟空格），
    后随大写词/引号/括号（排除 "3.5 Decoding" 式小节号——其归 heading 链）。"""
    m = _OL_ITEM_RE.match(text.strip())
    return int(m.group(1)) if m else None


def merge_numbered_lists(blocks: list) -> list:
    """编号列表重组：连续 ≥2 个 prose 块首部带严格递增编号（n, n+1, …）
    → 合为单块 kind="ol_list"（meta["items"]=[(编号, 折行折叠后文本)]），
    render 输出 Markdown 有序列表。须先于 merge_pending_prose 执行，
    否则列表项会被段落合并链吸收成裸段落（点号丢失缺陷的根因）。
    守卫：annot 旁注块、heading/code 等非 prose kind 不参与；
    编号不连续即断组（单块不成列表）。全书仅 Exercise 5.3/5.5 答案一处命中。"""
    out: list = []
    i, n = 0, len(blocks)
    while i < n:
        b = blocks[i]
        num = _ol_item_marker(b.text) if b.kind == "prose" and not b.meta.get("annot") else None
        if num is None:
            out.append(b)
            i += 1
            continue
        group = [(num, b)]
        j = i + 1
        while j < n:
            nb = blocks[j]
            if nb.kind != "prose" or nb.meta.get("annot"):
                break
            nnum = _ol_item_marker(nb.text)
            if nnum is None or nnum != group[-1][0] + 1:
                break
            group.append((nnum, nb))
            j += 1
        if len(group) < 2:
            out.append(b)
            i += 1
            continue
        texts = [(num_, blk.text) for num_, blk in group]
        leader = group[0][1]
        leader.kind = "ol_list"
        leader.text = "\n".join(t for _, t in texts)
        # 项文本 = 去编号后的正文（编号独立行形丢弃编号行；同行形切首个
        # 空白段），块内折行按 prose 同语义折叠为单行。
        items = []
        for num_, txt in texts:
            lines = [ln for ln in txt.split("\n") if ln.strip()]
            if len(lines) > 1:
                body_lines = lines[1:]
            else:
                parts = lines[0].split(" ", 1)
                body_lines = [parts[1]] if len(parts) > 1 else []
            items.append((num_, " ".join(body_lines).strip()))
        leader.meta["items"] = items
        out.append(leader)
        i = j
    return out


def strip_box_continuation_markers(blocks: list) -> list:
    """跨页概念框排版残标清理：无标题 concept_box 且位于页顶（续框碎片）、
    首段恰为印刷版 "(continued)" 续框标记 → 从 text 与 bodies 中剔除该标记。
    结构信号：盒类型 + 无 title + 页顶 y + 精确标记词（排版体系固有符号，
    同 Wingdings \\uf0a1 / Summary 状态机先例）。全书仅 p161 Perplexity 框命中。"""
    for b in blocks:
        if (b.kind != "concept_box" or b.meta.get("title")
                or not b.text.lstrip().startswith("(continued)")):
            continue
        if b.bbox[1] > 100:  # 页顶续框碎片（正文页上边距 ≈65pt）
            continue
        stripped = re.sub(r"^\(continued\)\s*", "", b.text.lstrip())
        if stripped and not stripped.startswith("(continued)"):
            b.text = stripped
            bodies = b.meta.get("bodies")
            if bodies:
                bodies[0] = re.sub(r"^\(continued\)\s*", "", bodies[0].lstrip())
    return blocks


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
            box_key = b.meta.get("box_key")
            while True:
                if j < n and blocks[j].kind == "code" \
                        and blocks[j].meta.get("in_concept_box") \
                        and blocks[j].page == b.page:
                    chain.append(blocks[j])
                    j += 1
                    # 后续同 key 文字段继续入链（box_key 必须一致，防止跨盒误并 A.9 macOS/Exercise）
                    while j < n and blocks[j].kind == "concept_box" \
                            and blocks[j].meta.get("box_key") == box_key:
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
                if _pa != _ca or prev_block.meta.get("listing_callout") \
                        or b.meta.get("listing_callout") \
                        or prev_block.meta.get("showcase") \
                        or b.meta.get("showcase"):
                    # 标签↔正文互不吸收（错位污染治理）；两侧同为标签时
                    # 落入下方常规合并（标签自身折行的续行需并回）。
                    # listing_callout 块一律保持独立：其行已按 callout_lines
                    # 组登记，被并块会丢组元数据（须由 merge_listing_callouts
                    # 在 pending_code 后消费）。
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
                        and _should_merge_prose(prev_txt.replace("\uf0a1", ""),
                                                txt)):
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
    这里处理 code 的连续折叠：相邻 code 块合并，中间若夹非 code 则断开）。
    listing_callout 块夹层时不打断合并（透明占位）——PDF 把一份清单拆成
    多个 code 块、旁注短语恰夹其间；合并同步拼接 raw_lines / lines /
    line_pages（行级定位坐标映射）与 page_span / bbox 并集。"""
    out: list = []
    pending_code = None
    for b in blocks:
        if b.kind == "code":
            if pending_code is not None:
                pending_code.text = (
                    pending_code.text + "\n" + b.text).strip("\n")
                pending_code.meta["raw_lines"] = (
                    (pending_code.meta.get("raw_lines") or [])
                    + (b.meta.get("raw_lines") or []))
                pending_code.meta["lines"] = (
                    (pending_code.meta.get("lines") or [])
                    + (b.meta.get("lines") or []))
                pa, pb_ = pending_code.bbox, b.bbox
                pending_code.bbox = (min(pa[0], pb_[0]), min(pa[1], pb_[1]),
                                     max(pa[2], pb_[2]), max(pa[3], pb_[3]))
                sp = pending_code.meta.get("page_span")
                sp = sp or (pending_code.page, pending_code.page)
                pending_code.meta["page_span"] = (
                    min(sp[0], b.page), max(sp[1], b.page))
            else:
                pending_code = b
                out.append(b)
        elif not b.meta.get("listing_callout"):
            pending_code = None
            out.append(b)
        else:
            out.append(b)   # 旁注块透传（后续由 callouts 消费），不断开合并
    return out


def merge_two_line_headings(blocks: list) -> list:
    """两行章节标题合并：相邻两 prose 块若满足 _is_two_line_heading，
    合并为一块 prose（标题化在 render 阶段按 TOC 重新判定）。
    annot/listing_callout/showcase 块跳过——旁注短语两行相邻会被误判为
    标题对，且并块会丢 callout_lines 组元数据；showcase 块须保持组内独立。"""
    out: list = []
    pending = None

    def _flush():
        nonlocal pending
        if pending is not None:
            out.append(pending)
            pending = None

    for b in blocks:
        skip = bool(b.meta.get("listing_callout") or b.meta.get("annot")
                    or b.meta.get("showcase"))
        if b.kind == "prose" and not skip:
            if pending is not None and pending.page == b.page \
                    and _is_two_line_heading(pending.text.strip(),
                                             b.text.strip()):
                pending.text = (pending.text.strip() + " "
                                + b.text.strip()).strip()
                continue
            _flush()
            pending = b
        else:
            _flush()
            out.append(b)
    _flush()
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
