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


def is_figure_text(block: dict, rects, union, page_height: float,
                   page_width: float = None, font_guard: bool = True) -> bool:
    """Decide whether a text block lives inside a figure (-> drop)."""
    if not rects:
        return False
    # Decorative large text (cover title / author / chapter heading) is never
    # figure-internal art, even if it overlaps a drawn frame.
    if _max_size(block) > MAX_FIGTEXT_SIZE:
        return False
    raw = " ".join(s["text"] for l in block.get("lines", []) for s in l["spans"]).strip()
    # Listing header guard: white-on-blue listing bars are not figure art.
    if raw.startswith("Listing"):
        return False
    # Listing code block guard: Courier blocks that look like listing code
    # (contain class/def/import, many lines, below a listing header) are not figure art.
    # This prevents the SimpleTokenizerV1 code at P49 (y178) from being dropped
    # because its background rect makes it intersect a figure rect.
    fonts = [s["font"] for l in block.get("lines", []) for s in l["spans"]]
    if any("Courier" in f for f in fonts):
        if any(kw in raw for kw in ["class ", "def ", "import ", "self.", "return "]):
            # Check if it's a multi-line code block (at least 3 lines) - likely a listing
            if len(block.get("lines", [])) >= 3:
                return False
        # Also, any Courier block with at least 5 lines and size ~8pt is likely a listing, not a figure label
        # Figure interior Courier labels are typically 1-2 lines, small (e.g., "x_2", "Train")
        # Require CODE-LIKE characters to avoid keeping figure interior prose that happens to be Courier.
        # IMPORTANT: do NOT treat '[' ']' ',' or '"' as code markers -- a figure-internal token array such
        # as [ "city", "stood", "the" ], [ "library", ... ] (Fig 2.13) is Courier, multi-line, and contains
        # brackets, but it is baked into the figure PNG. Treating brackets as code kept that block, so P1
        # emitted it as a ```text code block (see llms-from-scratch.md L1148-1155). Only genuine code syntax
        # (assignment, dict/colon, call parens, braces, imports/def/class) marks a real listing.
        if len(block.get("lines", [])) >= 5 and any("Courier" in f for f in fonts):
            if len(raw) > 50 and any(
                c in raw for c in ["=", ":", "(", ")", "{", "}", "import", "class", "def"]
            ):
                return False
    # Callout guard: yellow callout boxes (fill 0.969) use FranklinGothic-Demi 10.5 / Book 9.5.
    # Those are body prose, not figure labels. Keep any block that is dominantly
    # callout-font, even if it geometrically intersects its own fill rect.
    fonts = [s["font"] for l in block.get("lines", []) for s in l["spans"]]
    sizes = [s["size"] for l in block.get("lines", []) for s in l["spans"]]
    bb = fitz.Rect(block["bbox"])
    if fonts and any("FranklinGothic-Book" in f or "FranklinGothic-Demi" in f for f in fonts):
        if any(9.0 <= sz <= 11.0 for sz in sizes):
            if len(raw) > 20:
                return False
            if any("Demi" in f and abs(sz - 10.5) < 0.8 for f, sz in zip(fonts, sizes)):
                return False
    # Table guard: Table 1.1's grid is drawn with the same vector rects as figures,
    # but its text is a real data table (Dataset name / CommonCrawl / WebText2 …)
    # that must be kept as markdown table, not dropped as figure labels.
    if any(kw in raw for kw in ["Dataset name", "Number of tokens", "Proportion in training data", "CommonCrawl", "WebText2", "Books1", "Books2", "Wikipedia"]):
        return False
    # Figure label font guard: HumanistMann / Arial are the book's figure-side
    # annotation fonts (e.g. Fig 1.4 "1. The input text...", Fig 1.3 "Train").
    # They are figure labels even when they are full sentences or short glossary
    # phrases, and even when they sit just outside the figure's union bbox as
    # side annotations (Fig 1.4 left/right labels). Check proximity to any figure rect.
    # This must run BEFORE sentence/glossary guards, which would otherwise keep them.
    if font_guard and fonts and any("HumanistMann" in f or "Arial" in f for f in fonts):
        if any(sz <= 12.0 for sz in sizes):
            # Right-margin side notes (true margin notes) sit beside figures but
            # are complete sentences, NOT figure art. They must be kept, so exempt
            # blocks in the right margin column from the figure-label guard below.
            if page_width and block["bbox"][0] > page_width * 0.62:
                return False
            # Inside union -> definitely figure label
            if union is not None:
                cx = (bb.x0 + bb.x1) / 2
                cy = (bb.y0 + bb.y1) / 2
                if union.contains(fitz.Point(cx, cy)):
                    return True
            # Near any figure rect (side annotation, gap < 30pt) -> figure label
            min_dist = float('inf')
            for fr in rects:
                dx = max(fr.x0 - bb.x1, bb.x0 - fr.x1, 0)
                dy = max(fr.y0 - bb.y1, bb.y0 - fr.y1, 0)
                dist = (dx*dx + dy*dy) ** 0.5
                if dist < min_dist:
                    min_dist = dist
            if min_dist < 30.0:
                return True
    # Sentence guard: a complete sentence (ends with terminal punctuation) is
    # almost always real body prose, never a figure-internal label. Dropping it
    # would lose content, so we keep it even if it geometrically overlaps art.
    stripped = raw.rstrip(')]}"”’\'')
    if stripped and stripped[-1] in ".?!":
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
