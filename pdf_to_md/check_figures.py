"""Audit rendered figure clips against full-page evidence.

For every figure PNG listed in manifest.json, compare the clip rect with all
page evidence (vector drawing rects + text spans). Flags:

  cut-x / cut-y  : an element is sliced by the clip boundary (always error)
  near-side      : a non-caption span sits fully outside the clip but within
                   NEAR_SIDE_GAP horizontally with >=50% vertical overlap
                   (missing side label, e.g. Fig 1.6 ZERO-SHOT/FEW-SHOT)
  near-edge      : a drawing rect fully outside but within NEAR_DRAW_GAP
                   with >=50% vertical overlap (missing graphic fragment)
  prose-in-clip  : wide serif reading text mostly inside the clip — likely
                   over-inclusion of body prose (e.g. Fig 6.5 intro sentence)

Excluded from evidence: caption lines AND their whole parent blocks (caption
prose wraps beside figures in side-by-side layouts), header/footer bands,
margin-note zone, spans inside other clips on the same page.

Usage:
  python3 audit_figures.py              # audit current manifest.json
  python3 audit_figures.py --json OUT   # machine-readable report
"""
import json
import sys
from collections import defaultdict
from pathlib import Path

import pymupdf

from p0_extract_images import (
    CAPTION_RE, HEADER_Y, PDF_PATH, OUT_DIR, _overlaps, _body_column,
    collect_element_rects, find_caption_candidates, validate_candidates)

NEAR_SIDE_GAP = 30.0   # max horizontal gap for missing-label detection
NEAR_DRAW_GAP = 15.0   # max gap for stray drawing fragments
CUT_EPS = 0.5          # tolerance for cut-through detection
PROSE_MIN_W = 200.0    # wide serif span considered reading text


def _v_overlap_frac(r, c):
    """Fraction of r's height covered by c's y-range."""
    h = r.y1 - r.y0
    if h <= 0:
        return 1.0 if c.y0 <= r.y0 and c.y1 >= r.y1 else 0.0
    inter = min(r.y1, c.y1) - max(r.y0, c.y0)
    return max(0.0, inter) / h


def page_evidence(page, col_x1, cap_blocks):
    """(drawings, spans) relevant as figure-content evidence."""
    drawings = collect_element_rects(page)
    spans = []
    for blk in page.get_text("dict")["blocks"]:
        if blk["type"] != 0:
            continue
        block_rect = pymupdf.Rect(blk["bbox"])
        is_caption_block = any(
            (block_rect & cb).get_area() > 0.5 * max(block_rect.get_area(), 1e-6)
            for cb in cap_blocks) or any(
            CAPTION_RE.match("".join(s["text"] for s in ln["spans"]).strip())
            for ln in blk["lines"])
        for line in blk["lines"]:
            for s in line["spans"]:
                txt = s["text"].strip()
                if not txt:
                    continue
                r = pymupdf.Rect(s["bbox"])
                if r.y1 < HEADER_Y or r.y0 > page.rect.height - HEADER_Y:
                    continue  # running header/footer
                if "Mann" in s["font"] and r.x0 > col_x1:
                    continue  # margin note
                if is_caption_block:
                    continue  # caption prose incl. wrapped continuation lines
                spans.append((r, txt, s["font"]))
    return drawings, spans


def audit_figure(drawings, spans, clip):
    problems = []
    for r in drawings:
        vfrac = _v_overlap_frac(r, clip)
        if r.x0 < clip.x0 - CUT_EPS and r.x1 > clip.x0 + CUT_EPS and vfrac > 0.5:
            problems.append(("cut-x-draw", r))
        elif r.x0 < clip.x1 - CUT_EPS and r.x1 > clip.x1 + CUT_EPS and vfrac > 0.5:
            problems.append(("cut-x-draw", r))
        elif r.y0 < clip.y0 - CUT_EPS and r.y1 > clip.y0 + CUT_EPS \
                and (min(r.x1, clip.x1) - max(r.x0, clip.x0)) > 0.5 * max(r.width, 1e-6):
            problems.append(("cut-y-draw", r))
        elif not (r & clip).get_area() and vfrac > 0.5:
            gap = clip.x0 - r.x1 if r.x1 <= clip.x0 else r.x0 - clip.x1
            if 0 <= gap < NEAR_DRAW_GAP:
                problems.append(("near-edge", r))
    for r, txt, font in spans:
        vfrac = _v_overlap_frac(r, clip)
        inside = (r & clip).get_area() / max(r.get_area(), 1e-6)
        if inside > 0.6 and font.startswith("NewBaskerville") \
                and r.width >= PROSE_MIN_W:
            problems.append(("prose-in-clip", r, txt))  # warning class
            continue
        if r.x0 < clip.x0 - CUT_EPS and r.x1 > clip.x0 + CUT_EPS and vfrac > 0.5:
            problems.append(("cut-x-span", r, txt))
        elif r.x0 < clip.x1 - CUT_EPS and r.x1 > clip.x1 + CUT_EPS and vfrac > 0.5:
            problems.append(("cut-x-span", r, txt))
        elif r.y0 < clip.y0 - CUT_EPS and r.y1 > clip.y0 + CUT_EPS \
                and (min(r.x1, clip.x1) - max(r.x0, clip.x0)) > 0.5 * max(r.width, 1e-6):
            problems.append(("cut-y-span", r, txt))
        elif not (r & clip).get_area() and vfrac > 0.5:
            gap = clip.x0 - r.x1 if r.x1 <= clip.x0 else r.x0 - clip.x1
            if 0 <= gap < NEAR_SIDE_GAP:
                problems.append(("near-side", r, txt))
    return problems


def main():
    doc = pymupdf.open(PDF_PATH)
    manifest = json.loads((OUT_DIR / "manifest.json").read_text())
    figs = manifest["figures"]
    caps = validate_candidates(doc, find_caption_candidates(doc))

    clips_by_page = defaultdict(list)
    for f in figs:
        clips_by_page[f["page"]].append((f["fig"], pymupdf.Rect(f["clip"])))
    cap_blocks_by_page = defaultdict(list)
    for c in caps:
        cap_blocks_by_page[c["page"]].append(c["block_rect"])

    col_x0, col_x1 = _body_column(doc)
    report = []
    n_bad = 0
    for pno in range(1, len(doc) + 1):
        if pno not in clips_by_page:
            continue
        page = doc[pno - 1]
        drawings, spans = page_evidence(page, col_x1, cap_blocks_by_page[pno])
        own = clips_by_page[pno]
        for fig, clip in own:
            others = [c for f2, c in own if f2 != fig]
            my_spans = [(r, t, f) for r, t, f in spans
                        if not any((r & c).get_area() > 0.9 * max(r.get_area(), 1e-6)
                                   for c in others)]
            problems = audit_figure(drawings, my_spans, clip)
            if problems:
                n_bad += 1
                print(f"Fig {fig} p{pno}: {len(problems)} problem(s)")
                for p in problems[:12]:
                    kind, r = p[0], p[1]
                    extra = f"  {p[2][:40]!r}" if len(p) > 2 else ""
                    print(f"   {kind:<13} ({r.x0:.0f},{r.y0:.0f},{r.x1:.0f},{r.y1:.0f}){extra}")
                report.append({"fig": fig, "page": pno,
                               "problems": [{"kind": p[0],
                                             "rect": list(p[1]),
                                             **({"text": p[2]} if len(p) > 2 else {})}
                                            for p in problems]})
    print(f"\naudited={len(figs)} flagged={n_bad}")
    if "--json" in sys.argv:
        out = sys.argv[sys.argv.index("--json") + 1]
        Path(out).write_text(json.dumps(report, indent=2, ensure_ascii=False))
        print("report ->", out)
    return 1 if n_bad else 0


if __name__ == "__main__":
    sys.exit(main())
