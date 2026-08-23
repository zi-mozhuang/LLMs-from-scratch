"""pipeline.classify — 语义块分类（从各旧脚本原样搬运判定规则）。

对每个 Page 的 Block 判定 kind：
- 页眉/页脚、图内文字：已在 extract 阶段剔除（此处不再判定）。
- code：搬运 p1_text_stream.block_courier_frac + detect_code_lang，阈值 0.75。
- heading：搬运 fix_structure.fix_headings 的 TOC ground-truth 匹配
  （norm 匹配 + LEVEL_TO_PREFIX + SKIP_TITLES）。
- concept_box：搬运 fix_special_blocks.collect_concept_boxes 的颜色+尺寸+重叠+字体判定。
- listing_caption：搬运 fix_special_blocks.collect_listing_headers 的 LISTING_FILL 重叠 + 正则。
- note：搬运 fix_special_blocks.fix_notes 的字体/文本判定（^NOTE 全大写 Demi）。
- exercise：文本 ^Exercise \\d+\\.\\d+ 起头。
- figure_caption：文本 ^Figure \\d+\\.\\d+ 起头。
- bullet：搬运 fix_structure.fix_chapters 的 Wingdings 字体检测。
- 其余 → prose。

禁止新增任何判定规则；未覆盖的块一律 prose。
"""

import re

import fitz

from mdlib import config
from mdlib.textutil import norm, norm_keep_case
from pipeline.ir import BOX_GROUP_KINDS, BOX_OVERLAP, KINDS
from pipeline.merge import _should_merge_prose


def _merge_body_lines(blines: list) -> list:
    """框体正文按块内空格拼接（搬运 collect_concept_boxes 第 83/87 行的
    " ".join(...) 语义：同一 PDF 块内的可视行合并为一段）。"""
    if not blines:
        return []
    return [" ".join(s.strip() for s in blines).strip()]

# ---- 标题判定（搬运自 fix_structure.fix_headings） ----
SKIP_TITLES = {
    "brief contents", "contents", "index",
    "build a large language model (from scratch)",
}
LEVEL_TO_PREFIX = {1: "## ", 2: "### ", 3: "#### "}

# ---- 代码块（搬运自 p1_text_stream） ----
PY_HINT_RE = re.compile(
    r"(^\s*>>>|^\s*\.\.\.|def \w+\(|class \w+\(|import \w|from \w+ import|"
    r"print\(|>>> |\.cuda\(\)|torch\.\w+|np\.\w+|self\.\w+\s*=)"
)
COURIER_THRESHOLD = 0.75

# ---- 概念框 / Listing（搬运自 fix_special_blocks 常量） ----
CALLOUT_FILL = config.CALLOUT_FILL
LISTING_FILL = config.LISTING_FILL

# ---- 图注 / Exercise（方案表） ----
FIGURE_CAPTION_RE = re.compile(r"^\*{0,2}Figure\s+\d+\.\d+\b")
EXERCISE_RE = re.compile(r"^Exercise\s+\d+\.\d+")

# ---- Listing 标题解析（搬运自 collect_listing_headers） ----
LISTING_HEADER_RE = re.compile(r'(Listing\s+[A-Za-z0-9]+\.\d+)\s*(.*)')

# ---- 同矩形概念框碎片归组（audit_quote_fragmentation 审计结论的结构性修复） ----
# 落入同一 CALLOUT_FILL 矩形的相邻文本块属于同一视觉容器；
# PyMuPDF 常把一框切成多 block，逐块渲染会碎成多个分离引用块。
# kind 集合与重叠阈值：BOX_GROUP_KINDS / BOX_OVERLAP 定义于 ir.py。


# --------------------------------------------------------------------------- #
# 各判定辅助函数（原样搬运判定条件）                                          #
# --------------------------------------------------------------------------- #
def _block_courier_frac(block) -> float:
    """搬运 p1_text_stream.block_courier_frac（去掉 header 剔除，extract 已做）。"""
    spans = block.meta.get("spans", [])
    if not spans:
        return 0.0
    tot = len(spans)
    cou = sum(1 for s in spans if "Courier" in s["font"])
    return cou / tot if tot else 0.0


def _detect_code_lang(text: str) -> str:
    if PY_HINT_RE.search(text):
        return "python"
    return "text"


def _is_concept_box(block, drawings, skip_rects=None) -> tuple[bool, str, list]:
    """搬运 collect_concept_boxes 的颜色+尺寸+重叠+字体判定。
    返回 (is_box, title, bodies)。

    skip_rects：本页含 "This chapter covers" 的 CALLOUT_FILL drawing 矩形集合
    （搬运 collect_concept_boxes 第 69-72 行的整框跳过逻辑：整框内所有块都不判为概念框，
    交由 bullet/章节逻辑处理）。
    """
    bx = fitz.Rect(block.bbox)
    # 整框跳过：block 落在任一含 "This chapter covers" 的 CALLOUT_FILL drawing 内
    if skip_rects:
        for sr in skip_rects:
            if not (bx & fitz.Rect(sr)).is_empty:
                return False, "", []
    for fill, r in drawings:
        if fill is None:
            continue
        if tuple(round(v, 3) for v in fill) != CALLOUT_FILL:
            continue
        if r.width < 100 or r.height < 40:
            continue
        inter = bx & r
        if inter.is_empty:
            continue
        if bx.get_area() == 0:
            continue
        if inter.get_area() / bx.get_area() < 0.20:
            continue
        # 跳过 This chapter covers 单块（搬运 collect_concept_boxes 原单块判断，冗余保险）
        if "This chapter covers" in block.text:
            return False, "", []
        # 标题判定：首个 span Demi 10.5（搬运 collect_concept_boxes 的 has_title）
        spans = block.meta.get("spans", [])
        has_title = False
        title = ""
        if spans:
            fs = spans[0]
            if "Demi" in fs["font"] and abs(fs["size"] - 10.5) < 0.8:
                has_title = True
                title = spans[0]["text"].strip()
        if has_title:
            # 搬运 collect_concept_boxes：bodies 排除首行（标题行）
            blines = [ln for ln in block.text.split("\n")[1:] if ln.strip()
                      and "\uf0a1" not in ln]
            bodies = _merge_body_lines(blines)
        else:
            blines = [ln for ln in block.text.split("\n") if ln.strip()
                      and "\uf0a1" not in ln]
            bodies = _merge_body_lines(blines)
        return True, title, bodies
    return False, "", []


def _is_listing_caption(block, drawings) -> tuple[bool, str]:
    """搬运 collect_listing_headers 的 LISTING_FILL 重叠 + 正则解析。"""
    bx = fitz.Rect(block.bbox)
    for fill, r in drawings:
        if fill is None:
            continue
        if tuple(round(v, 3) for v in fill) != LISTING_FILL:
            continue
        inter = bx & r
        if inter.is_empty or inter.get_area() < 5:
            continue
        m = LISTING_HEADER_RE.match(block.text.strip())
        if m:
            num = m.group(1).strip()
            tit = m.group(2).strip()
            full = f"{num} {tit}".strip()
        else:
            full = block.text.strip()
        return True, full
    return False, ""


def _is_note(block) -> bool:
    """搬运 fix_notes：^NOTE 全大写 Demi 8.5 字体判定（取首 span 信号）。"""
    if not re.match(r'^NOTE\s', block.text):
        return False
    spans = block.meta.get("spans", [])
    if not spans:
        return False
    fs = spans[0]
    # NOTE 用 FranklinGothic-Demi 8.5；Demi 前缀 + size≈8.5
    return "Demi" in fs["font"] and abs(fs["size"] - 8.5) < 1.0


def _is_bullet(block) -> bool:
    """搬运 fix_structure.fix_chapters 的 Wingdings 字体检测。"""
    return any("Wingdings" in s["font"] for s in block.meta.get("spans", []))


def _in_covers_rect(block, skip_rects) -> bool:
    """block 是否落在任一含 "This chapter covers" 的 CALLOUT_FILL drawing 内
    （与 _is_concept_box 的整框跳过判定同源）。"""
    if not skip_rects:
        return False
    bx = fitz.Rect(block.bbox)
    for sr in skip_rects:
        if not (bx & fitz.Rect(sr)).is_empty:
            return True
    return False


def _mark_annot_blocks(page_blocks) -> None:
    """边注/清单注释标签标记：HumanistMann/Arial 小字号块为书旁注本体，
    禁止被 merge_pending_prose 吸收进无关段落（错位污染治理 P1）。
    须先于 _mark_listing_callouts 执行（后者依赖 annot 前置条件）。"""
    for b in page_blocks:
        sps = (b.meta.get("spans") or [])
        if b.kind == "prose" and sps:
            ann = sum(1 for x in sps
                      if ("HumanistMann" in x["font"] or "Arial" in x["font"])
                      and x["size"] <= 12.0)
            if ann >= max(1, len(sps) // 2) and len(b.text) <= 160:
                b.meta["annot"] = True


def _mark_listing_callouts(page_blocks, drawings) -> None:
    """Listing 旁注标注（Manning 排版：代码旁的 HumanistMann 短语 +
    指向代码行的箭头图形）。判定三条件（缺一不可，防边注误收）：
    1. 已是 annot 块（HumanistMann/Arial 小字号短文本）；
    2. bbox 与同页某 code block 纵向重叠 >5pt；
    3. 页面存在小尺寸黑色 drawing（箭头三角/连线，宽高 ≤12pt）且其
       y 中心距块 ≤40pt——组级几何锚定，边注（无指示符）天然排除。
    打 meta["listing_callout"]=True；由 merge.merge_listing_callouts
    归位进代码围栏尾部作 `# ` 注释。判定失败者维持散段落现状（无害）。"""
    codes = [b for b in page_blocks if b.kind == "code"]
    if not codes:
        return
    marker_ys = []
    for fill, r in drawings or []:
        if fill is None:
            continue
        if tuple(round(v, 2) for v in fill) != (0.0, 0.0, 0.0):
            continue
        if r.width <= 12 and r.height <= 12:
            marker_ys.append((r.y0 + r.y1) / 2)
    if not marker_ys:
        return
    for b in page_blocks:
        if b.kind != "prose" or not b.meta.get("annot"):
            continue
        bx = fitz.Rect(b.bbox)
        if not any(min(bx.y1, c.bbox[3]) - max(bx.y0, c.bbox[1]) > 5
                   for c in codes):
            continue
        d = min(min(abs(m - bx.y0), abs(m - bx.y1)) for m in marker_ys)
        if d <= 40:
            b.meta["listing_callout"] = True


def _tag_box_membership(page_blocks, page_index, drawings, skip_rects) -> None:
    """为本页块标记所属概念框矩形（meta["box_key"]）。

    矩形判定与 _is_concept_box 同源（CALLOUT_FILL + 宽高阈值 + covers 跳过），
    重叠 ≥ BOX_OVERLAP 即视为框内。仅标记 BOX_GROUP_KINDS 中的 kind；
    heading/code/figure_caption/listing_caption 不入组（出现即打断归组，
    保证图链、代码围栏、标题不被并进引用块）。"""
    rects = []
    for fill, r in drawings or []:
        if fill is None:
            continue
        if tuple(round(v, 3) for v in fill) != CALLOUT_FILL:
            continue
        rr = fitz.Rect(r)
        if rr.width < 100 or rr.height < 40:
            continue
        # covers 整框跳过同源排除
        if any((rr & fitz.Rect(sr)).get_area() > 0.9 * min(rr.get_area(), fitz.Rect(sr).get_area())
               for sr in (skip_rects or [])):
            continue
        rects.append(rr)
    if not rects:
        return
    for b in page_blocks:
        if b.kind not in BOX_GROUP_KINDS:
            # 概念框内代码标记（如 p79 Understanding dot products 框内嵌示例）：
            # 不入碎片归组链（打断语义保留），仅携带归属信号供
            # merge.merge_box_code 合成复合引用框（GFM 引用内围栏）
            if b.kind == "code":
                bx = fitz.Rect(b.bbox)
                if bx.get_area() == 0:
                    continue
                for rr in rects:
                    inter = bx & rr
                    if inter.is_empty:
                        continue
                    if inter.get_area() / bx.get_area() >= BOX_OVERLAP:
                        b.meta["in_concept_box"] = True
                        break
            continue
        bx = fitz.Rect(b.bbox)
        if bx.get_area() == 0:
            continue
        for rr in rects:
            inter = bx & rr
            if inter.is_empty:
                continue
            if inter.get_area() / bx.get_area() >= BOX_OVERLAP:
                b.meta["box_key"] = f"p{page_index}:{rr.x0:.0f},{rr.y0:.0f},{rr.x1:.0f},{rr.y1:.0f}"
                break


def _apply_inline_code(block) -> None:
    """搬运 p1_text_stream 的内联代码标记：对 prose 块中 Courier 字体的连续
    span 包反引号（内联代码）。纯 Courier 代码块已由 classify 判为 code 块，
    不经此函数（不影响围栏内文本）。
    """
    out_lines = []
    for ln in block.meta.get("lines", []):
        spans = ln.get("spans", [])
        if not spans:
            continue
        parts = []
        buf_courier = None
        for s in spans:
            if "Courier" in s["font"]:
                buf_courier = (buf_courier or "") + s["text"]
            else:
                if buf_courier is not None:
                    parts.append(f"`{buf_courier}`")
                    buf_courier = None
                parts.append(s["text"])
        if buf_courier is not None:
            parts.append(f"`{buf_courier}`")
        line = "".join(parts).strip()
        if line:
            out_lines.append(line)
    if out_lines:
        block.text = "\n".join(out_lines)


def build_toc_map(doc_toc) -> list:
    """搬运 fix_headings 的 TOC 去重 + SKIP 过滤，返回 [(level, title)]。
    另搬运 find_all_heading_candidates 的长度护栏（norm 长度 ≤2 的条目不作为
    标题真相源，如 PDF TOC 中的垃圾条目 'T'）。"""
    seen = set()
    entries = []
    for level, title, page in doc_toc:
        tn = norm(title.strip())
        if tn in SKIP_TITLES:
            continue
        if len(tn) <= 2:
            continue
        if tn in seen:
            continue
        seen.add(tn)
        entries.append((level, title.strip()))
    return entries


def _match_heading(tn: str, toc_norm: dict):
    """精确匹配 TOC 标题；失败则前缀回退（PDF 提取的标题副标题被截断，
    如 'appendix D\\nAdding bells and whistles' 是 TOC 'appendix D—Adding bells
    and whistles to the training loop' 的前缀）。返回 (lvl, orig_title) 或 None。
    """
    if tn in toc_norm:
        return toc_norm[tn]
    if len(tn) < 6:
        return None
    best = None
    for k, v in toc_norm.items():
        if k.startswith(tn):
            if best is None or len(k) > len(best[0]):
                best = (k, v)
    return best[1] if best else None


def classify_pages(pages: list) -> list:
    """对每个 Block 判定 kind。直接修改并就地返回 blocks 列表（扁平化）。"""
    # 收集 TOC（取第一页挂的 toc；全部页共享同一 doc.toc）
    toc = getattr(pages[0], "toc", []) if pages else []
    toc_entries = build_toc_map(toc)
    # 建索引：(1) TOC 原文 norm；(2) 章节标题去掉前导 "N " 后的 norm，
    # 用于匹配 PDF 提取的无前缀章节文本（旧链由 fix_chapters 补前缀）。
    toc_norm = {}
    for lvl, t in toc_entries:
        tn = norm(t.replace("\u2014", " "))
        toc_norm[tn] = (lvl, t)
        m = re.match(r"^\d+\s+(.*)$", t)
        if m:
            toc_norm[norm(m.group(1).replace("\u2014", " "))] = (lvl, t)

    blocks_out: list = []
    # 附录 B/C 页界（搬运 fix_appendix_chapter_dups 的区域语义：附录 B 内的
    # Chapter N 为文献分组（###），附录 C 内为习题解答主节（##））
    appendix_ranges = {}
    for lvl, title, pg in toc:
        m = re.match(r"appendix ([A-E])", title.strip(), re.I)
        if m and lvl == 1:
            appendix_ranges[m.group(1)] = pg - 1  # 0 基
    b_start = appendix_ranges.get("B")
    c_start = appendix_ranges.get("C")
    d_start = appendix_ranges.get("D")
    # 章末 Summary 区域状态机：heading 文本恰为 "Summary" 进入，
    # 任一下一级 heading 退出。仅该区域内的 \uf0a1 圆点块判为 summary_list
    # （列表渲染）；其余区域的 \uf0a1（参考文献、正文 bullet、特殊 token 等）
    # 维持旧链 callout 语义（引用块），行为不变。
    in_summary = False
    for page in pages:
        drawings = getattr(page, "drawings", [])        # 预计算本页含 "This chapter covers" 的 CALLOUT_FILL drawing 矩形集合
        # （搬运 collect_concept_boxes 第 54-72 行的整框跳过逻辑）
        skip_rects = []
        page_block_texts = [b.text for b in page.blocks]
        page_has_chapter = any("This chapter covers" in bt for bt in page_block_texts)
        if page_has_chapter:
            for fill, r in drawings:
                if fill is None:
                    continue
                if tuple(round(v, 3) for v in fill) != CALLOUT_FILL:
                    continue
                if r.width < 100 or r.height < 40:
                    continue
                # 该 drawing 覆盖区域内任一 block 含 "This chapter covers" 即整框跳过
                for b in page.blocks:
                    bx = fitz.Rect(b.bbox)
                    inter = bx & fitz.Rect(r)
                    if inter.is_empty:
                        continue
                    if bx.get_area() == 0:
                        continue
                    if inter.get_area() / bx.get_area() < 0.20:
                        continue
                    if "This chapter covers" in b.text:
                        skip_rects.append(r)
                        break
        start_idx = len(blocks_out)
        for block in page.blocks:
            text = block.text.strip()
            # 1) 代码块（最高优先级之一）
            if _block_courier_frac(block) >= COURIER_THRESHOLD:
                block.kind = "code"
                block.lang = _detect_code_lang(text)
                # 搬运 p1_text_stream 第 520-523/337 行：代码文本保留前导缩进
                block.text = "\n".join(
                    block.meta.get("raw_lines", block.text.split("\n"))
                ).rstrip()
                blocks_out.append(block)
                continue
            # 内联代码反引号（搬运 p1_text_stream.inline_code_wrap_line：旧链 p1
            # 对除纯 Courier 代码块外的所有文本块统一包反引号）
            _apply_inline_code(block)
            # 2) 概念框
            is_box, title, bodies = _is_concept_box(block, drawings, skip_rects)
            if is_box:
                block.kind = "concept_box"
                block.meta["title"] = title
                block.meta["bodies"] = bodies
                blocks_out.append(block)
                continue
            # 3) Listing 标题
            is_list, full = _is_listing_caption(block, drawings)
            if is_list:
                block.kind = "listing_caption"
                block.meta["full"] = full
                blocks_out.append(block)
                continue
            # 4) NOTE
            if _is_note(block):
                block.kind = "note"
                blocks_out.append(block)
                continue
            # 5) Exercise
            if EXERCISE_RE.match(text):
                block.kind = "exercise"
                blocks_out.append(block)
                continue
            # 6) 图注
            if FIGURE_CAPTION_RE.match(text):
                block.kind = "figure_caption"
                blocks_out.append(block)
                continue
            # 7) bullet / callout（搬运旧链语义）：
            #    旧链 p2 concept_boxes_to_blockquote 把一切含 \uf0a1 的行转 blockquote；
            #    仅章首 "This chapter covers" 区域被 fix_chapter_covers 重写为 `- ` 列表。
            #    故：covers 区域内 → bullet；区域外的 PUA bullet 块 → callout。
            if _is_bullet(block):
                if _in_covers_rect(block, skip_rects):
                    block.kind = "bullet"
                elif "\uf0a1" in block.text:
                    block.kind = "summary_list" if in_summary else "callout"
                else:
                    block.kind = "bullet"
                blocks_out.append(block)
                continue
            # 8) 标题（TOC ground truth）
            # 多行标题（如 "appendix A\nIntroduction to PyTorch"）需折叠换行后再归一化，
            # 且把 TOC 条目的 em dash（—）也归一化为空格，才能匹配 TOC 合并条目
            # （搬运 fix_headings 的 TOC 标题注入逻辑）。
            tn = norm(text.replace("\n", " ").replace("\u2014", " "))
            mh = _match_heading(tn, toc_norm)
            if mh is not None:
                block.kind = "heading"
                block.level, orig_title = mh  # 1/2/3 + TOC 原文（正确大小写）
                # 用 TOC ground-truth 标题覆盖 PDF 提取文本（修复大小写/空格）
                block.text = orig_title
                # 搬运 fix_appendix_chapter_dups 区域语义：按所在附录调整 Chapter N 级别
                if re.match(r"^Chapter \d+$", block.text.strip()):
                    if b_start is not None and c_start is not None \
                            and b_start <= block.page < c_start:
                        block.level = 2  # 附录 B：文献分组 → ###
                    elif c_start is not None and (d_start is None or block.page < d_start) \
                            and (c_start <= block.page):
                        block.level = 1  # 附录 C：习题解答主节 → ##
                blocks_out.append(block)
                # Summary 状态机：进入/退出判定放在 heading 落定之后
                in_summary = " ".join(block.text.split()).lower() == "summary"
                continue
            # 其余
            block.kind = "prose"
            blocks_out.append(block)
        # 同矩形概念框碎片归组标记（供 merge.merge_box_fragments 合并）
        _tag_box_membership(blocks_out[start_idx:], page.index, drawings, skip_rects)
        # Listing 旁注标记（供 merge.merge_listing_callouts 归位进代码围栏）
        _mark_annot_blocks(blocks_out[start_idx:])
        _mark_listing_callouts(blocks_out[start_idx:], drawings)

    # 标题后缀碎片剔除（搬运 fix_structure.fix_chapter_titles Case 2 的删除语义：
    # 章节标题在 PDF 中拆为多块时，首块经 TOC 匹配还原完整标题，其余碎片块删除）
    # （边注/清单注释标签标记已移入页循环内 _mark_annot_blocks，
    #   供同页的 _mark_listing_callouts 依赖 annot 前置条件）

    _drop_heading_suffix_fragments(blocks_out)
    # 章节标题补全：TOC level-1 章节条目若 PDF 未提取到文本（大字/图标题被剔除），
    # 以 TOC ground truth 在最接近章节首页处插入 heading 块（搬运 fix_chapters 的重建意图）。
    _ensure_chapter_headings(blocks_out, toc, pages)
    return blocks_out


def _drop_heading_suffix_fragments(blocks_out: list) -> None:
    """紧跟标题块之后、同页的 prose 块若其文本是标题文本的后缀（norm 比较），
    视为被拆出的标题碎片，就地删除（置空文本并标记 skip）。"""
    from pipeline.ir import Block  # noqa: F401  (类型提示用)
    i = 0
    n = len(blocks_out)
    while i < n:
        b = blocks_out[i]
        if b.kind == "heading":
            title_n = norm(b.text.replace("\n", " ").replace("\u2014", " "))
            j = i + 1
            while j < n and blocks_out[j].page == b.page:
                nxt = blocks_out[j]
                if nxt.kind != "prose":
                    break
                nxt_n = norm(nxt.text.replace("\n", " "))
                if not nxt_n or not title_n.endswith(nxt_n):
                    break
                nxt.meta["drop"] = True
                j += 1
            i = max(j, i + 1)
        else:
            i += 1
    blocks_out[:] = [b for b in blocks_out if not b.meta.get("drop")]


def _ensure_chapter_headings(blocks_out: list, toc, pages: list) -> None:
    """对 TOC 中 level==1 且以数字开头的章节条目，确保存在对应 heading。"""
    from pipeline.ir import Block
    # 已识别的章节标题（norm，去前缀）
    have = set()
    for b in blocks_out:
        if b.kind == "heading" and b.level == 1:
            have.add(norm(b.text))
    # 章节条目：((首数字章节号, TOC 原文), 首页号)
    chap_entries = []
    for lvl, title, page in toc:
        if lvl != 1:
            continue
        tn = norm(title.strip())
        if tn in SKIP_TITLES:
            continue
        m = re.match(r"^(\d+)\s+(.*)$", title.strip())
        if not m:
            continue
        chap_entries.append((title.strip(), page - 1))  # 0 基首页

    # 构建 page -> 该页首个 prose 块索引 的插入点
    # 简化：按首页号找到 blocks_out 中 page 最接近且 ≥ 首页的 prose 位置之前插入
    inserts = []
    for title, pg0 in chap_entries:
        key = norm(title)
        # 去前缀版本也算已存在
        m = re.match(r"^\d+\s+(.*)$", title)
        key_no_num = norm(m.group(1)) if m else key
        if key in have or key_no_num in have:
            continue
        # 找插入位置：blocks_out 中第一个 block.page >= pg0 的索引
        idx = len(blocks_out)
        for i, b in enumerate(blocks_out):
            if b.page >= pg0:
                idx = i
                break
        inserts.append((idx, Block("heading", title, pg0, level=1)))
    # 从后往前插入，避免索引偏移
    for idx, blk in sorted(inserts, reverse=True):
        blocks_out.insert(idx, blk)
