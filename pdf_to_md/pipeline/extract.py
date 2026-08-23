"""pipeline.extract — PDF → 页模型（原样搬运 p1_text_stream.py 的页面循环骨架）。

只填充 Page / Block，**不生成 markdown 字符串**。每页收集：
- 文本块：get_text("dict") 的 blocks/lines/spans（保留 font/size/bbox/flags）；
- 绘图：page.get_drawings()（保留 fill 颜色与 rect），挂在 page.drawings 上；
- 全文档一次：doc.get_toc()（存 doc 级，供 classify 用）；
- 图区域：figure_text_detect.page_element_regions。

来源：p1_text_stream.py pdf_to_text_stream（第 293 行起）的页面遍历骨架、
is_header_span / block_courier_frac / is_figure_text 调用约定。
"""

import fitz
import json
from collections import defaultdict
from pathlib import Path

import re

from mdlib import config
from pipeline.ir import Page, Block
from figure_text_detect import page_element_regions, is_figure_text

# 图注行：起头为 "Figure X.Y"。图注即使落在页脚区也不得当页脚剔除，
# 否则底部图注（如 Fig 7.8，p236 用 FranklinGothic-Demi 且 y1≈591）会被误删，
# 导致 MD 缺图、破坏 PDF↔MD 图片一一对应。

def _manifest_clips():
    """p0 manifest 的图像裁剪框（物理页 0 基 -> [Rect]）。
    p0 渲染 PNG 用的正是这些 clip，是"真图边界"的权威源；
    page_element_regions 的 drawings-union 启发式会把箭头/引导线等
    装饰图元一并并入，横吞半页正文（见 diff_triage 审计记录）。"""
    try:
        data = json.loads(config.MANIFEST_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}
    m = defaultdict(list)
    for e in data.get("figures", []):
        pg, clip = e.get("page"), e.get("clip")
        if pg and clip and len(clip) == 4:
            m[pg - 1].append(fitz.Rect(clip))
    return m


_FIGURE_CAPTION_RE = re.compile(r"^\*{0,2}Figure\s+\d+\.\d+\b")


def _is_header_span_like(span: dict, page_height: float) -> bool:
    """内联版 p1_text_stream.is_header_span（仅用于提取阶段剔除页眉页脚行）。"""
    font = span["font"]
    patterns = (
        config.HEADER_FONTS["section_title"]
        + config.HEADER_FONTS["page_number"]
        + config.HEADER_FONTS["chapter_label"]
    )
    if not any(p in font for p in patterns):
        return False
    if span["size"] >= 10.5:
        return False
    y0, y1 = span["bbox"][1], span["bbox"][3]
    at_top = y0 < 35.0
    at_bottom = y1 > page_height - 75.0
    return at_top or at_bottom


def _is_header_line_like(line: dict, page_height: float) -> bool:
    spans = line["spans"]
    if not spans:
        return False
    y0, y1 = line["bbox"][1], line["bbox"][3]
    at_top = y0 < 35.0
    at_bottom = y1 > page_height - 75.0
    if not (at_top or at_bottom):
        return False
    txt = "".join(s["text"] for s in spans).strip()
    if config.PAGE_NUMBER_RE.match(txt) and len(txt) <= 5:
        return True
    return False


def _detect_toc_pages(doc) -> set:
    """搬运 p1_text_stream._detect_toc_pages：返回应跳过的目录页（0 基）。"""
    toc = doc.get_toc()
    toc_keywords = {"brief contents", "contents", "table of contents",
                    " sommaire", "inhoud"}
    skip_titles = []
    for _lvl, title, pg in toc:
        tn = title.strip().lower()
        if tn in toc_keywords:
            skip_titles.append((pg - 1, tn))
    if not skip_titles:
        return set()
    first_toc = min(p for p, _ in skip_titles)
    after_toc = len(doc)
    stop_kw = {"preface", "acknowledgments", "about this book", "about the author",
               "foreword", "introduction", "1 understanding"}
    for _lvl, title, pg in toc:
        if pg - 1 <= first_toc:
            continue
        tn = title.strip().lower()
        if any(tn.startswith(k) or tn == k for k in stop_kw):
            after_toc = pg - 1
            break
    return set(range(first_toc, after_toc))


def extract_book(pdf_path) -> list:
    """打开 PDF 一次，逐页填充 Page/Block。返回 list[Page]。

    Page 额外挂载动态属性 .drawings（list of (fill_tuple, fitz.Rect)），
    供 classify 阶段使用（IR 的 Page dataclass 不扩展字段，避免破坏结构）。
    """
    pdf_path = str(pdf_path)
    doc = fitz.open(pdf_path)
    page_heights = {p: doc[p].rect.height for p in range(len(doc))}
    toc = doc.get_toc()  # 全文档一次
    manifest_clips = _manifest_clips()
    toc_skip = _detect_toc_pages(doc)  # 目录页剔除（搬运 p1）

    pages: list = []
    for pno in range(len(doc)):
        if pno in toc_skip:
            continue
        page = doc[pno]
        page_height = page_heights[pno]
        d = page.get_text("dict")
        raw_blocks = d.get("blocks", [])
        # 按 (y, x) 排序，与 p1 一致
        blocks_sorted = sorted(
            raw_blocks, key=lambda b: (round(b["bbox"][1], 1), round(b["bbox"][0], 1))
        )

        # 绘图（fill + rect），供概念框/Listing 分类使用
        drawings = [
            (tuple(dr.get("fill")), dr["rect"])
            for dr in page.get_drawings()
            if dr.get("fill")
        ]

        # 图区域（剔除图内文字）：manifest clips 权威优先，启发式兜底。
        # manifest 模式下关闭 HumanistMann/Arial 字体一票否决守卫——
        # 该守卫会误杀清单旁注释标签与边注（审计实锤三类系统性丢失）。
        if pno in manifest_clips:
            fig_rects = manifest_clips[pno]
            fig_union = None
            for r in fig_rects:
                fig_union = r if fig_union is None else fig_union | r
            fig_font_guard = False
        else:
            fig_rects, fig_union = page_element_regions(page)
            fig_rects = [r for r in fig_rects if not r.is_empty and r.width * r.height >= 16.0]
            fig_union = None
            for r in fig_rects:
                fig_union = r if fig_union is None else fig_union | r
            # 无 manifest 图的页面不存在"烘进 PNG 的文字"，
            # 字体守卫在此纯误杀边注/清单标签（审计三类丢失根源），一并关闭
            fig_font_guard = False

        p = Page(index=pno, height=page_height)
        p.drawings = drawings
        p.toc = toc  # 全文档 TOC 挂在每页（classify 只读 DOC 级，这里仅随页传递）

        for block in blocks_sorted:
            if "lines" not in block:
                # 纯图片/绘图块：不产出 Block（不携带正文）
                continue

            # 图注块整体豁免页眉/页脚剔除（见 _FIGURE_CAPTION_RE 注释）。
            is_figcap = any(
                _FIGURE_CAPTION_RE.match(
                    "".join(s["text"] for s in ln["spans"]).strip())
                for ln in block["lines"]
            )
            if is_figcap:
                non_header_lines = list(block["lines"])
            else:
                non_header_lines = [
                    ln for ln in block["lines"]
                    if any(not _is_header_span_like(s, page_height) for s in ln["spans"])
                    and not _is_header_line_like(ln, page_height)
                ]
            if not non_header_lines:
                continue

            # manifest 模式精判：块内过半行中心落在 clip 内 -> 已烘进 PNG，
            # 整块剔除（块级中心判定会漏掉跨宽标签块，如 'Model input…'）
            if pno in manifest_clips and block.get("lines"):
                _cl = manifest_clips[pno]
                _in = [ln for ln in block["lines"]
                       if any(r.contains(fitz.Point((ln["bbox"][0] + ln["bbox"][2]) / 2,
                                                    (ln["bbox"][1] + ln["bbox"][3]) / 2))
                              for r in _cl)]
                if _in and len(_in) >= max(1, len(block["lines"]) // 2):
                    continue
            # 剔除图内文字（避免与渲染 PNG 重复）
            if is_figure_text(block, fig_rects, fig_union, page_height,
                              page.rect.width, font_guard=fig_font_guard):
                continue

            # 把原始行/span 信息存入 Block 的 meta，供 classify 使用。
            # 同时做最小清洗：拼接非页眉 span 文本。
            # 图注块豁免页眉剔除：保留全部 span，避免 "Figure X.Y" 标签被删。
            span_keep = (lambda s: True) if is_figcap else (
                lambda s: not _is_header_span_like(s, page_height))
            block_text_lines = []
            for ln in non_header_lines:
                txt = "".join(
                    s["text"] for s in ln["spans"] if span_keep(s)
                ).strip()
                if txt:
                    block_text_lines.append(txt)
            text = "\n".join(block_text_lines)
            if not text.strip():
                continue

            b = Block(
                kind="prose",  # 暂定 prose，classify 阶段重新判定
                text=text,
                page=pno,
                bbox=tuple(block["bbox"]),
            )
            # 原始结构信号（font/size/flags）供 classify 判定代码/概念框/NOTE 等
            spans_info = []
            for ln in non_header_lines:
                for s in ln["spans"]:
                    if not span_keep(s):
                        continue
                    spans_info.append({
                        "text": s["text"],
                        "font": s["font"],
                        "size": s["size"],
                        "flags": s["flags"],
                        "bbox": tuple(s["bbox"]),
                        "color": s.get("color"),
                    })
            b.meta["spans"] = spans_info
            # 搬运 p1_text_stream 第 521-523 行：代码行的原始文本仅 rstrip
            # （保留前导缩进），供 classify 判为 code 块时还原。
            b.meta["raw_lines"] = [
                "".join(s["text"] for s in ln["spans"]
                        if span_keep(s)).rstrip()
                for ln in non_header_lines
            ]
            b.meta["lines"] = [
                {"spans": [sp for sp in ln["spans"] if span_keep(sp)]}
                for ln in non_header_lines
            ]
            p.blocks.append(b)

        pages.append(p)

    doc.close()
    return pages
