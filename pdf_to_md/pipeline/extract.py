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
from pathlib import Path

from mdlib import config
from pipeline.ir import Page, Block
from figure_text_detect import page_element_regions, is_figure_text


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

        # 图区域（剔除图内文字）
        fig_rects, fig_union = page_element_regions(page)

        p = Page(index=pno, height=page_height)
        p.drawings = drawings
        p.toc = toc  # 全文档 TOC 挂在每页（classify 只读 DOC 级，这里仅随页传递）

        for block in blocks_sorted:
            if "lines" not in block:
                # 纯图片/绘图块：不产出 Block（不携带正文）
                continue

            # 剔除整块页眉/页脚
            non_header_lines = [
                ln for ln in block["lines"]
                if any(not _is_header_span_like(s, page_height) for s in ln["spans"])
                and not _is_header_line_like(ln, page_height)
            ]
            if not non_header_lines:
                continue

            # 剔除图内文字（避免与渲染 PNG 重复）
            if is_figure_text(block, fig_rects, fig_union, page_height):
                continue

            # 把原始行/span 信息存入 Block 的 meta，供 classify 使用。
            # 同时做最小清洗：拼接非页眉 span 文本。
            block_text_lines = []
            for ln in non_header_lines:
                txt = "".join(
                    s["text"] for s in ln["spans"]
                    if not _is_header_span_like(s, page_height)
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
                    if _is_header_span_like(s, page_height):
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
            b.meta["lines"] = [
                {"spans": [sp for sp in ln["spans"]
                           if not _is_header_span_like(sp, page_height)]}
                for ln in non_header_lines
            ]
            p.blocks.append(b)

        pages.append(p)

    doc.close()
    return pages
