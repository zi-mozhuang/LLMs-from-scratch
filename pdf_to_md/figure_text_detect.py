#!/usr/bin/env python3
"""
Sub-module: detect text that was extracted FROM INSIDE figures (vector
drawings / embedded raster art) so it can be dropped from the Markdown stream.

Background
----------
PyMuPDF extracts the text *inside* a figure's vector drawing (flow-chart
labels, callout box text, axis labels, etc.) as ordinary text blocks. Those
strings are already "baked into" the rendered figure PNG (P0 image channel),
so keeping them in the prose duplicates the figure content and pollutes the
text. We must remove them WITHOUT touching real body text that merely sits
near or below a figure.

Approach (geometry-based, mirrors the P0 image channel's reliability)
---------------------------------------------------------------------
For each page we collect every drawing path rect + raster image placement
rect (reusing P0's `collect_element_rects`). A text block is classified as
figure-internal when ANY of:

  (HIGH confidence) its bbox intersects one of those element rects
      -> true for flow-charts / architecture diagrams / side-by-side art
         where labels are painted on top of drawn shapes.

  (MED  confidence) its bbox center lies inside the UNION bbox of all element
      rects AND it sits in the TOP zone (top 30% of the union height) AND it
      is a short label (<= 2 physical lines)
      -> catches callout-box figures (Fig 1.1 style) whose labels float in the
         gap between thin frame strokes (so they don't intersect a stroke rect)
         yet are clearly inside the figure's top area. Real body paragraphs
         below the figure fall outside this top zone.

Everything else is treated as body text and kept.

Verification (run standalone):
    python figure_text_detect.py <book.pdf>
prints per-page counts of dropped vs kept text blocks and a few samples, so
the deletion can be eyeballed before wiring into the pipeline.
"""

import sys
from pathlib import Path

import fitz  # PyMuPDF

sys.path.insert(0, str(Path(__file__).parent))
from p0_extract_images import collect_element_rects  # reuse P0 geometry

from mdlib import config


# Top-zone fraction of the element-union height used by the MED rule.
TOP_ZONE = 0.30
# Max physical lines for a label to count under the MED rule.
MAX_LABEL_LINES = 2
# Minimum element-union height for the MED rule to apply (tiny art -> skip).
MIN_UNION_H = 30.0
# Max font size for a figure-internal label. Larger text (cover title, author,
# chapter headings) is decorative body text, never figure-internal art.
MAX_FIGTEXT_SIZE = 13.0
# Max character length of a genuine figure-internal label. Text blocks longer
# than this are treated as body content (code, prose sentences, callout
# annotations) and are NEVER dropped as figure art — they merely sit near or
# below a figure. Prevents over-eager union/intersection rules from swallowing
# real body paragraphs (ch3 code, appE formula derivation, appD warmup prose…).
MAX_FIGLABEL_LEN = 40

# ---- Listing 旁注箭头识别（2026-08）---------------------------------------
# Manning 排版：Listing 旁的 HumanistMann 短语由「黑色小三角 + 细肘形连接线」
# 指向对应代码行。这些箭头 drawing 此前被 collect_element_rects 当作图形要素
# 收进图区种子，导致邻近旁注文本被 is_figure_text 误判为图内文字整块删除
# （实测 p48 Listing 2.3 五条短语蒸发三条）。此处给出结构性判据，把箭头从
# 图区种子中剔除；真图形内部的细黑线因不邻近 annot 字体文本而不受影响。
MARKER_BLACK = (0.0, 0.0, 0.0)   # 箭头填充色（2 位小数归一）
MARKER_TRI_MAX = 12.0            # 三角部件最大宽高（pt）
MARKER_LINE_THICK = 2.5          # 肘线厚度阈值（pt）
MARKER_LINE_LEN = 70.0           # 肘线最大长度（pt）
MARKER_ANCHOR_DIST = 60.0        # 距最近 annot 字体 span 的最大距离（pt）
MARKER_CODE_NEAR = 15.0          # 箭头中心距 Courier 代码行的纵向容差（pt）


def _gap_dist(a: fitz.Rect, b: fitz.Rect) -> float:
    """两矩形的间隙距离（不相交时为边到边欧氏距离，相交为 0）。"""
    dx = max(b.x0 - a.x1, a.x0 - b.x1, 0)
    dy = max(b.y0 - a.y1, a.y0 - b.y1, 0)
    return (dx * dx + dy * dy) ** 0.5


def annot_span_rects(text_blocks: list) -> list:
    """annot 字体 span（HumanistMann/Arial ≤12pt）的 bbox 集——旁注文本信号。"""
    out = []
    for b in text_blocks:
        for ln in b.get("lines", []):
            for s in ln.get("spans", []):
                if ("HumanistMann" in s.get("font", "")
                        or "Arial" in s.get("font", "")) \
                        and s.get("size", 99) <= 12.0:
                    out.append(fitz.Rect(s["bbox"]))
    return out


def courier_row_ranges(text_blocks: list) -> list:
    """Courier 占比 ≥75% 的物理行 y 范围集——代码行信号。"""
    rows = []
    for b in text_blocks:
        for ln in b.get("lines", []):
            spans = ln.get("spans", [])
            if not spans:
                continue
            cou = sum(1 for s in spans if "Courier" in s.get("font", ""))
            if cou / len(spans) < 0.75:
                continue
            rows.append((min(s["bbox"][1] for s in spans),
                         max(s["bbox"][3] for s in spans)))
    return rows


def callout_marker_rects(drawings: list, annot_rects: list,
                         code_rows: list = None) -> list:
    """判定 Listing 旁注箭头矩形。

    drawings: [(fill_tuple, fitz.Rect)]；annot_rects: annot span bbox 集；
    code_rows: Courier 行 y 范围集——提供时要求箭头中心落在某行 ±15pt 内
    （箭头必指向代码行；纯图形页无此结构则零剔除，防真图内细线回流）。
    判据（缺一不可）：黑色填充 ∧（小三角 ≤12×12 ∨ 细线厚 ≤2.5 长 ≤70）
    ∧ 距最近 annot span ≤60pt。
    """
    if not annot_rects:
        return []
    out = []
    for fill, r in drawings:
        if fill is None:
            continue
        if tuple(round(v, 2) for v in fill) != MARKER_BLACK:
            continue
        tri = r.width <= MARKER_TRI_MAX and r.height <= MARKER_TRI_MAX
        thin = ((r.width <= MARKER_LINE_THICK and r.height <= MARKER_LINE_LEN)
                or (r.height <= MARKER_LINE_THICK and r.width <= MARKER_LINE_LEN))
        if not (tri or thin):
            continue
        if code_rows is not None:
            cy = (r.y0 + r.y1) / 2
            if not any(y0 - MARKER_CODE_NEAR <= cy <= y1 + MARKER_CODE_NEAR
                       for y0, y1 in code_rows):
                continue
        if min(_gap_dist(r, ar) for ar in annot_rects) > MARKER_ANCHOR_DIST:
            continue
        out.append(r)
    return out


def listing_bar_rects(drawings: list) -> list:
    """Listing 标题色条（LISTING_FILL 矩形）——清单装置而非图形，
    不应充当图区种子（其 <30pt 近邻规则曾误杀条上方的旁注短语）。"""
    want = tuple(round(v, 3) for v in config.LISTING_FILL)
    return [r for fill, r in drawings
            if fill is not None
            and tuple(round(v, 3) for v in fill) == want]


def seed_exclusion_rects(drawings: list, annot_rects: list,
                         code_rows: list = None) -> list:
    """图区种子剔除集 = 旁注箭头 + Listing 色条。管道与独立检测共用，
    保证"烘进 PNG 的文字"判定一致。"""
    return callout_marker_rects(drawings, annot_rects, code_rows) \
        + listing_bar_rects(drawings)


def drop_seed_rects(rects: list, exclude: list) -> tuple:
    """从种子矩形中剔除 exclude 集（2 位小数匹配），重算 union。
    返回 (rects, union_or_None)。"""
    if exclude:
        eset = {tuple(round(v, 2) for v in r) for r in exclude}
        rects = [r for r in rects
                 if tuple(round(v, 2) for v in r) not in eset]
    if not rects:
        return rects, None
    ux0 = min(r.x0 for r in rects)
    uy0 = min(r.y0 for r in rects)
    ux1 = max(r.x1 for r in rects)
    uy1 = max(r.y1 for r in rects)
    return rects, fitz.Rect(ux0, uy0, ux1, uy1)


def page_element_regions(page, exclude_rects=None):
    """Return (rects, union_rect_or_None) for a page.

    rects         : list[fitz.Rect] of every drawing/image placement.
    union_rect    : fitz.Rect bounding all rects, or None if no elements.
    exclude_rects : 图区种子剔除集（seed_exclusion_rects 产物：旁注箭头 +
                    Listing 色条），防清单装置误导图内文字判定。
                    None = 不剔除（旧行为；管道调用方显式计算传入）。
    """
    rects = collect_element_rects(page)
    if rects and exclude_rects:
        rects, union = drop_seed_rects(rects, exclude_rects)
        return rects, union
    if not rects:
        return rects, None
    ux0 = min(r.x0 for r in rects)
    uy0 = min(r.y0 for r in rects)
    ux1 = max(r.x1 for r in rects)
    uy1 = max(r.y1 for r in rects)
    return rects, fitz.Rect(ux0, uy0, ux1, uy1)


def _max_size(block: dict) -> float:
    return max((s["size"] for l in block.get("lines", []) for s in l["spans"]),
               default=0.0)


def is_figure_text(block: dict, rects, union, page_height: float,
                   page_width: float = None, font_guard: bool = True,
                   burned_rects=None) -> bool:
    """Decide whether a text block lives inside a figure (-> drop).

    burned_rects：附录 E 图的烘焙区域（由图注锚点生长，与渲染 PNG 同一
    区域）。中心落在其中的块无条件判图内文字——它们必然已烘进 PNG；
    不受 glossary/sentence 守卫保护（FigE.1 事故：Outputs/Pretrained/
    Weight update 等 ≤4 纯字母词标签被 Term-glossary 守卫放行）。
    """
    if not rects and not burned_rects:
        return False
    raw = " ".join(s["text"] for l in block.get("lines", []) for s in l["spans"]).strip()
    fonts = [s["font"] for l in block.get("lines", []) for s in l["spans"]]
    sizes = [s["size"] for l in block.get("lines", []) for s in l["spans"]]
    bb = fitz.Rect(block["bbox"])

    # --- 长文本体内容保护（安全网）---
    # 任何超过图注标签长度的块都视为正文（代码 / 完整句子 / 旁注注解），
    # 绝不按图内文字丢弃——它们只是邻近或位于图下方。覆盖：ch3 代码块、
    # ch5 日志句、appD 代码模板句 / 梯度裁剪句、appE 公式推导段等。
    # 真正的图内标签都是短短语（<= MAX_FIGLABEL_LEN），不受影响。
    # Decorative large text (cover title / author / chapter heading) is never
    # figure-internal art, even if it overlaps a drawn frame.
    if _max_size(block) > MAX_FIGTEXT_SIZE:
        return False
    # Courier 代码块守卫：含代码语法（赋值/def/class/import/with/for/if/print…）
    # 即视为真实清单代码，不被图区相交误吞（修复 ch3 x_2=inputs[1]、
    # ch7 json.dump 行等）。图内 Courier 标签多为轴标/纯词数组，无此类语法。
    if any("Courier" in f for f in fonts):
        if any(kw in raw for kw in ["class ", "def ", "import ", "self.", "return ",
                                    "=", "print(", "torch.", "np.", "with ",
                                    "for ", "if ", "while ", "assert ", "#"]):
            return False
    # 长 HumanistMann / Arial 注解（> MAX_FIGLABEL_LEN）是清单旁注 / 边注，
    # 不是烘进 PNG 的图标签——保留，交由 classify/merge 归位。
    if any("HumanistMann" in f or "Arial" in f for f in fonts):
        if len(raw) > MAX_FIGLABEL_LEN:
            return False
    # 烘焙区判定：仅丢弃短图标签（<= MAX_FIGLABEL_LEN）；正文段落必然更长，
    # 保留（修复 appE Fig E.1 公式推导段被烘焙区误吞）。
    if burned_rects:
        cx0, cy0 = (bb.x0 + bb.x1) / 2, (bb.y0 + bb.y1) / 2
        if len(raw) <= MAX_FIGLABEL_LEN and any(
                r.contains(fitz.Point(cx0, cy0)) for r in burned_rects):
            return True
        # 近邻分支：轴标签落"区域下缘 ↔ 图注"间隙（E.2 Inputs 实测）。仅短块。
        if len(raw) <= 30:
            for r in burned_rects:
                dx = max(r.x0 - bb.x1, bb.x0 - r.x1, 0)
                dy = max(r.y0 - bb.y1, bb.y0 - r.y1, 0)
                if (dx * dx + dy * dy) ** 0.5 < 30.0:
                    return True
    # Listing header guard: white-on-blue listing bars are not figure art.
    if raw.startswith("Listing"):
        return False
    # Callout guard: yellow callout boxes (fill 0.969) use FranklinGothic-Demi 10.5 / Book 9.5.
    # Those are body prose, not figure labels. Keep any block that is dominantly
    # callout-font, even if it geometrically intersects its own fill rect.
    if fonts and any("FranklinGothic-Book" in f or "FranklinGothic-Demi" in f for f in fonts):
        if any(9.0 <= sz <= 11.0 for sz in sizes):
            if len(raw) > 20:
                return False
            if any("Demi" in f and abs(sz - 10.5) < 0.8 for f, sz in zip(fonts, sizes)):
                return False
    # Table guard: Table 1.1's grid is drawn with the same vector rects as figures,
    # but its text is a real data table that must be kept as markdown table.
    if any(kw in raw for kw in ["Dataset name", "Number of tokens", "Proportion in training data", "CommonCrawl", "WebText2", "Books1", "Books2", "Wikipedia"]):
        return False
    # Sentence guard: a complete sentence (ends with terminal punctuation) is
    # almost always real body prose, never a figure-internal label.
    stripped = raw.rstrip(')]}"”’\'')
    if stripped and stripped[-1] in ".?!":
        return False
    # Term-glossary guard: a short pure-alphabetic phrase (<=4 words, no digits,
    # no punctuation) is a body glossary term, not a figure label.
    words = raw.replace("-", " ").split()
    if words and len(words) <= 4 and not any(ch.isdigit() for ch in raw) \
            and all(w.isalpha() for w in words):
        return False
    # 长文本体安全网（放在相交 / MED 规则之前）：> MAX_FIGLABEL_LEN 的块一律保留。
    if len(raw) > MAX_FIGLABEL_LEN:
        return False
    # 短 HumanistMann / Arial 图标签：位于图 union 内或邻近图元（<30pt）才判图内；
    # 右栏边注（完整句）已由上句守卫保留。修复清单旁注被字体守卫关闭后误吞。
    if any("HumanistMann" in f or "Arial" in f for f in fonts):
        if union is not None:
            cx = (bb.x0 + bb.x1) / 2
            cy = (bb.y0 + bb.y1) / 2
            if union.contains(fitz.Point(cx, cy)):
                return True
        min_dist = float('inf')
        for fr in rects:
            dx = max(fr.x0 - bb.x1, bb.x0 - fr.x1, 0)
            dy = max(fr.y0 - bb.y1, bb.y0 - fr.y1, 0)
            dist = (dx * dx + dy * dy) ** 0.5
            if dist < min_dist:
                min_dist = dist
        if min_dist < 30.0:
            return True
    # (HIGH) direct intersection with any drawn/placed element (only for short labels).
    if any(bb.intersects(r) for r in rects):
        return True
    # (MED) inside union, in top zone, short label.
    if union is not None and union.height >= MIN_UNION_H:
        cx = (bb.x0 + bb.x1) / 2
        cy = (bb.y0 + bb.y1) / 2
        in_union = (union.x0 <= cx <= union.x1) and (union.y0 <= cy <= union.y1)
        if in_union:
            top_limit = union.y0 + TOP_ZONE * union.height
            n_lines = len(block.get("lines", []))
            if bb.y0 <= top_limit and n_lines <= MAX_LABEL_LINES:
                return True
    return False


def detect_page_figure_text(page, page_height: float):
    """Return (dropped_blocks, kept_blocks) text-block dicts for one page."""
    tblocks = page.get_text("dict").get("blocks", [])
    draws = [(tuple(dr.get("fill")), dr["rect"])
             for dr in page.get_drawings() if dr.get("fill")]
    exclude = seed_exclusion_rects(drawings, annot_span_rects(tblocks),
                                   courier_row_ranges(tblocks))
    rects, union = page_element_regions(page, exclude_rects=exclude)
    dropped, kept = [], []
    for b in tblocks:
        if "lines" not in b:
            continue  # drawing / image only
        if is_figure_text(b, rects, union, page_height, page.rect.width):
            dropped.append(b)
        else:
            kept.append(b)
    return dropped, kept


# ---------------------------------------------------------------------------
# Standalone verification
# ---------------------------------------------------------------------------
def _norm(t: str) -> str:
    return " ".join(t.split()).strip().lower()


if __name__ == "__main__":
    import json

    if len(sys.argv) < 2:
        raise SystemExit("usage: figure_text_detect.py <book.pdf>")
    doc = fitz.open(sys.argv[1])
    total_dropped = 0
    samples = []
    for pno in range(len(doc)):
        page = doc[pno]
        ph = page.rect.height
        dropped, kept = detect_page_figure_text(page, ph)
        total_dropped += len(dropped)
        for b in dropped:
            txt = " ".join(s["text"] for l in b["lines"] for s in l["spans"]).strip()
            if txt and len(samples) < 15:
                samples.append((pno + 1, txt[:60]))
    print(f"pages: {len(doc)}  figure-internal text blocks dropped: {total_dropped}")
    print("--- samples of dropped text (page, text) ---")
    for p, t in samples:
        print(f"  p{p}: {t!r}")
