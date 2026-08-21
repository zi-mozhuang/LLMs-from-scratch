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
from extract_images import collect_element_rects  # reuse P0 geometry


# Top-zone fraction of the element-union height used by the MED rule.
TOP_ZONE = 0.30
# Max physical lines for a label to count under the MED rule.
MAX_LABEL_LINES = 2
# Minimum element-union height for the MED rule to apply (tiny art -> skip).
MIN_UNION_H = 30.0
# Max font size for a figure-internal label. Larger text (cover title, author,
# chapter headings) is decorative body text, never figure-internal art.
MAX_FIGTEXT_SIZE = 13.0


def page_element_regions(page):
    """Return (rects, union_rect_or_None) for a page.

    rects      : list[fitz.Rect] of every drawing/image placement.
    union_rect : fitz.Rect bounding all rects, or None if no elements.
    """
    rects = collect_element_rects(page)
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


def is_figure_text(block: dict, rects, union, page_height: float) -> bool:
    """Decide whether a text block lives inside a figure (-> drop)."""
    if not rects:
        return False
    # Decorative large text (cover title / author / chapter heading) is never
    # figure-internal art, even if it overlaps a drawn frame.
    if _max_size(block) > MAX_FIGTEXT_SIZE:
        return False
    # Sentence guard: a complete sentence (ends with terminal punctuation) is
    # almost always real body prose, never a figure-internal label. Dropping it
    # would lose content, so we keep it even if it geometrically overlaps art.
    # This protects e.g. the glossary paragraphs that sit beside callout-box
    # figures (Fig 1.1) while still dropping label-only figure text.
    raw = " ".join(s["text"] for l in block.get("lines", []) for s in l["spans"]).strip()
    if raw and raw[-1] in ".?!":
        return False
    # Term-glossary guard: a short pure-alphabetic phrase (<=4 words, no digits,
    # no punctuation) is a body glossary term, not a figure label. Protects the
    # Fig 1.1 glossary (Artificial intelligence / Machine learning / ...) which
    # geometrically sits inside the callout-box frame. Trade-off: a few pure-word
    # figure labels (e.g. "Building an LLM") may be retained as harmless
    # redundancy rather than risk deleting real glossary prose.
    words = raw.replace("-", " ").split()
    if words and len(words) <= 4 and not any(ch.isdigit() for ch in raw) \
            and all(w.isalpha() for w in words):
        return False
    bb = fitz.Rect(block["bbox"])

    # (HIGH) direct intersection with any drawn/placed element.
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
    rects, union = page_element_regions(page)
    dropped, kept = [], []
    for b in page.get_text("dict").get("blocks", []):
        if "lines" not in b:
            continue  # drawing / image only
        if is_figure_text(b, rects, union, page_height):
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
