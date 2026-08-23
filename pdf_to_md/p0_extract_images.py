"""Extract all images from the Raschka LLM book PDF.

Two channels:
  A. embedded raster images  -> extracted_images/embedded/  (original bytes)
  B. vector figures          -> extracted_images/figures/   (region render @3x)

Pipeline (each stage independently tested):
  1. find_caption_candidates : line-level 'Figure X.Y' scan (incl. body refs)
  2. validate_candidates     : geometric check rejects refs, keeps captions
  3. build_figure_region     : grow figure region from seeds + label blocks

Usage:
  python3 p0_extract_images.py test-candidates   # stage 1 self-test
  python3 p0_extract_images.py test-validate     # stage 2 self-test
  python3 p0_extract_images.py                   # full run + V1-V3 verification
"""
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

import pymupdf
from PIL import Image

BASE = Path(__file__).parent
PDF_PATH = BASE / "Build a Large Language Model (From Scratch) (Sebastian Raschka).pdf"
OUT_DIR = BASE / "extracted_images"
EMB_DIR = OUT_DIR / "embedded"
FIG_DIR = OUT_DIR / "figures"

# 渲染倍率：默认 3（≈216dpi）。可用环境变量 P0_ZOOM 覆盖（如 300dpi → P0_ZOOM=4.17）。
ZOOM = float(__import__("os").environ.get("P0_ZOOM", "3"))
PAD = 6.0
CAPTION_RE = re.compile(r"^Figure\s+(\d+\.\d+)\b")
BARE_RE = re.compile(r"^Figure\s+\d+\.\d+$")

ABOVE_GAP = 80.0    # max vertical distance element-bottom -> caption-top
SIDE_GAP = 30.0     # max horizontal gap for side-by-side layout
LINK_GAP = 80.0     # max element-to-region gap for chain absorption
LABEL_GAP = 30.0    # max gap for absorbing sans-serif label spans
CODE_RELAY_GAP = 40.0  # max gap for extending through code-diagram spans
HEADER_Y = 45.0     # header/footer exclusion band
MAX_LABEL_SIZE = 11.0  # sans spans larger than this are headings, not labels


def _overlaps(r, band):
    """Coordinate-level overlap test.

    Rect.intersects() demands positive-area intersection, which silently
    drops zero-width/height vector strokes (thin rules, bracket lines) that
    anchor figure extents (Fig 1.6 lost its left bracket line this way).
    """
    return (r.x0 < band.x1 and r.x1 > band.x0
            and r.y0 < band.y1 and r.y1 > band.y0)


def _span_class(font, size):
    """Structural class of a text span (no hardcoded strings/pages).

    body : serif reading text -> only absorbable via containment
    code : Courier fragments -> only absorbable via containment
    label: sans-serif figure text (ArialMT, matplotlib, callout heads)
    """
    if font.startswith("NewBaskerville"):
        return "body"
    if "Courier" in font:
        return "code"
    return "label"


def _page_spans(page):
    """(rect, line_text, font, size, line_id) for content spans.

    Headers excluded. line_id lets callers group spans sharing a physical
    line (mixed body+code lines mark inline code inside prose sentences).
    """
    out = []
    for blk in page.get_text("dict")["blocks"]:
        if blk["type"] != 0:
            continue
        for line in blk["lines"]:
            ltxt = "".join(s["text"] for s in line["spans"]).strip()
            for s in line["spans"]:
                txt = s["text"].strip()
                if not txt:
                    continue
                r = pymupdf.Rect(s["bbox"])
                if r.y1 < HEADER_Y or r.y0 > page.rect.height - HEADER_Y:
                    continue
                out.append((r, ltxt, s["font"], float(s["size"]), id(line)))
    return out


# ---------------------------------------------------------------- stage 1
def find_caption_candidates(doc):
    """Line-level scan for lines starting with 'Figure X.Y'.

    Returns candidate dicts with line + parent-block bbox. Deliberately
    includes body references ("Figure 3.7 shows ..."); stage 2 rejects them.
    """
    cands = []
    for pno in range(len(doc)):
        for blk in doc[pno].get_text("dict")["blocks"]:
            if blk["type"] != 0:
                continue
            for line in blk["lines"]:
                txt = "".join(s["text"] for s in line["spans"]).strip()
                m = CAPTION_RE.match(txt)
                if m:
                    cands.append({
                        "fig": m.group(1),
                        "page": pno + 1,
                        "rect": pymupdf.Rect(line["bbox"]),
                        "block_rect": pymupdf.Rect(blk["bbox"]),
                        "text": txt,
                        "bare": bool(BARE_RE.match(txt)),
                    })
    return cands


def test_candidates():
    """Stage 1 test: totals match ground truth from full-text line scan."""
    doc = pymupdf.open(PDF_PATH)
    cands = find_caption_candidates(doc)
    cnt = Counter(c["fig"] for c in cands)
    dups = {k: v for k, v in cnt.items() if v > 1}
    print(f"candidates={len(cands)} unique={len(cnt)} dup_figs={len(dups)}")
    print("dups:", dict(sorted(dups.items())))
    assert len(cands) == 143, f"expected 143 line matches, got {len(cands)}"
    assert len(cnt) == 128, f"expected 128 unique figs, got {len(cnt)}"
    print("STAGE 1 PASS")
    return cands


# ---------------------------------------------------------------- stage 2
def collect_element_rects(page):
    """All drawing paths + raster image placement rects on the page."""
    rects = []
    pw, ph = page.rect.width, page.rect.height
    for d in page.get_drawings():
        r = d["rect"]
        if r.width > 0.5 or r.height > 0.5:
            # skip page frames / full-page background rects
            if r.width > 0.95 * pw and r.height > 0.95 * ph:
                continue
            rects.append(pymupdf.Rect(r))
    for im in page.get_images(full=True):
        for r in page.get_image_rects(im[0]):
            rects.append(pymupdf.Rect(r))
    return rects


def _element_gap(r, line_rect, block_rect):
    """Distance between element rect and caption, or None if unrelated.

    Two relations qualify:
      above : element bottom just above caption first-line top (normal
              layout: graphic stacked over caption)
      side  : element vertically level with the full caption BLOCK and
              horizontally adjacent with a small gap (side-by-side layout,
              e.g. Fig 1.7 p34: graphic left of caption). Horizontal overlap
              does NOT qualify here — that is body text flowing around.
    """
    if r.y1 <= line_rect.y0 + 5 and 0 <= line_rect.y0 - r.y1 < ABOVE_GAP:
        return line_rect.y0 - r.y1
    v_overlap = r.y0 < block_rect.y1 + 10 and r.y1 > block_rect.y0 - 10
    if v_overlap:
        h_gap = max(block_rect.x0 - r.x1, r.x0 - block_rect.x1)
        if 0 <= h_gap < SIDE_GAP:
            return h_gap
    return None


def validate_candidates(doc, cands):
    """Keep only candidates with graphics directly adjacent.

    A body reference has no drawing/image next to it -> rejected.
    One caption survives per fig number: bare lines win over inline ones,
    then smaller gap wins.
    """
    by_page = {}
    for c in cands:
        by_page.setdefault(c["page"], []).append(c)
    validated = []
    for pno, cs in sorted(by_page.items()):
        rects = collect_element_rects(doc[pno - 1])
        for c in cs:
            gaps = [g for g in (_element_gap(r, c["rect"], c["block_rect"])
                                for r in rects) if g is not None]
            if gaps:
                validated.append(dict(c, gap=min(gaps)))
    best = {}
    for c in validated:
        rank = (0 if c["bare"] else 1, c["gap"])
        cur = best.get(c["fig"])
        if cur is None or rank < cur[0]:
            best[c["fig"]] = (rank, c)

    def fig_key(item):
        return [int(x) for x in item[0].split(".")]

    return [c for _, (_, c) in sorted(best.items(), key=fig_key)]


def test_validate():
    """Stage 2 test: 128 unique true captions, known pages, no chapter gaps."""
    doc = pymupdf.open(PDF_PATH)
    caps = validate_candidates(doc, find_caption_candidates(doc))
    figs = [c["fig"] for c in caps]
    print(f"validated={len(caps)} unique={len(set(figs))}")
    assert len(caps) == 128, f"expected 128 true captions, got {len(caps)}"
    assert len(set(figs)) == 128, "duplicate fig numbers survived"
    expect_pages = {"1.1": 25, "1.7": 34, "3.7": 78, "5.4": 155,
                    "6.16": 220, "7.2": 228, "7.21": 270}
    by_fig = {c["fig"]: c for c in caps}
    for fig, pg in expect_pages.items():
        got = by_fig[fig]["page"]
        assert got == pg, f"Fig {fig}: expected p{pg}, got p{got}"
    per_ch = defaultdict(list)
    for f in figs:
        ch, n = f.split(".")
        per_ch[int(ch)].append(int(n))
    for ch, nums in sorted(per_ch.items()):
        nums.sort()
        assert nums == list(range(1, len(nums) + 1)), f"ch{ch} gaps: {nums}"
        print(f"  ch{ch}: {len(nums)} figures contiguous")
    print("STAGE 2 PASS")
    return caps


# ---------------------------------------------------------------- stage 3
def _text_blocks(page):
    """(rect, text) of body-area text blocks, headers/footers excluded."""
    out = []
    for b in page.get_text("blocks"):
        if b[6] != 0:
            continue
        r = pymupdf.Rect(b[:4])
        if r.y1 < HEADER_Y or r.y0 > page.rect.height - HEADER_Y:
            continue
        out.append((r, b[4]))
    return out


def _body_column(doc):
    """Median left/right edge of wide body blocks = body column bounds.

    Margin notes (HumanistMann side columns) live beyond col_x1 and must
    never be absorbed into figure regions.
    """
    xs0, xs1 = [], []
    for pno in range(len(doc)):
        for r, _ in _text_blocks(doc[pno]):
            if r.width > 300:
                xs0.append(r.x0)
                xs1.append(r.x1)
    xs0.sort(), xs1.sort()
    return xs0[len(xs0) // 2], xs1[len(xs1) // 2]


def _rect_gap(a, b):
    """Chebyshev-style rect distance: max of x-gap and y-gap."""
    dx = max(0.0, max(a.x0, b.x0) - min(a.x1, b.x1))
    dy = max(0.0, max(a.y0, b.y0) - min(a.y1, b.y1))
    return max(dx, dy)


def _body_in_corridor(cand, region, body_rects):
    """True if a serif body span sits between cand and the region.

    Relay extension through figure text must not cross reading text: on
    p220 a code listing sits one prose line above the plot; the prose line
    in the corridor is what keeps the listing out of the figure. Text-only
    diagrams (Fig 7.7, two stacked examples 37 pt apart) have empty
    corridors and relay freely.
    """
    if cand.y1 <= region.y0:
        lo, hi = cand.y1, region.y0
    elif cand.y0 >= region.y1:
        lo, hi = region.y1, cand.y0
    else:
        return False
    hx0 = min(cand.x0, region.x0)
    hx1 = max(cand.x1, region.x1)
    for b in body_rects:
        if b.y0 < hi - 1 and b.y1 > lo + 1 \
                and min(b.x1, hx1) - max(b.x0, hx0) > 0.5 * b.width:
            return True
    return False


LINK_FILL = (0.438, 0.652, 0.801)     # Listing header bar fill (§12 signal)
CALLOUT_FILL = (0.969, 0.961, 0.910)  # concept/exercise box fill (§1 signal)


def _fill_close(fill, ref):
    return all(abs(f - c) < 0.03 for f, c in zip(fill, ref))


def _listing_bars(page):
    """Rects of Listing header bars and concept-callout boxes.

    Both are hard growth barriers: figures stacked onto a Listing must not
    swallow its bar/code (Fig 4.3), and figures next to a callout box must
    not absorb it or the prose around it (Fig 2.11, Fig 3.17). Structural
    color/size signals only, no hardcoded pages.
    """
    bars = []
    for d in page.get_drawings():
        r = d["rect"]
        fill = d.get("fill")
        if fill is None:
            continue
        if _fill_close(fill, LINK_FILL) and r.width >= 200 \
                and 10 <= r.height <= 40:
            bars.append(pymupdf.Rect(r))
        elif _fill_close(fill, CALLOUT_FILL) and r.width >= 100 \
                and r.height >= 40:
            bars.append(pymupdf.Rect(r))
    return bars


def _crosses_bar(lo_rect, hi_rect, bars):
    """True if a barrier rect separates two rects vertically."""
    a, b = sorted((lo_rect, hi_rect), key=lambda r: r.y1)
    for bar in bars:
        if bar.y0 > a.y1 + 1 and bar.y1 < b.y0 - 1 \
                and min(bar.x1, b.x1) - max(bar.x0, a.x0) > 0.5 * bar.width:
            return True
    return False


def build_figure_region(page, cap, y_min, y_max, col_x1):
    """Build figure region for one validated caption.

    Seeds = elements directly adjacent to the caption (above within
    ABOVE_GAP, or side-by-side). Then chain-absorb every element whose gap
    to the region is <= LINK_GAP, clamped to the caption barrier band.

    Why both bounds exist:
      - band (y_min/y_max) stops stacked figures bleeding into each other
      - LINK_GAP stops foreign content above the figure being swallowed
        (e.g. p220: a code listing sits ~117 pt above the plot; its
        callout arrows must not join the figure, while Fig 7.2's internal
        ~48 pt row spacing must be crossed)

    Text absorption is span-level and font-classified (block-level checks
    break when PyMuPDF merges a figure label with unrelated prose into one
    block — Fig 1.6 'TEXT COMPLETION', Fig 2.10 top labels):
      - sans-serif label spans: absorbed within LABEL_GAP of the region
      - serif body / Courier code spans: only when >=90% already contained,
        so reading text and listings are never pulled into the figure
        (Fig 6.5 previously swallowed its own intro sentence)
    Caption lines/blocks are never absorbed.
    """
    lrect, brect = cap["rect"], cap["block_rect"]
    band = pymupdf.Rect(0, y_min, page.rect.width, y_max)
    bars = _listing_bars(page)
    rects = [r for r in collect_element_rects(page) if _overlaps(r, band)
             and not any(r == b for b in bars)]
    seeds = [r for r in rects if _element_gap(r, lrect, brect) is not None]
    if not seeds:
        return None

    def clamp(rr):
        rr.intersect(band)
        return rr

    absorbed = list(seeds)
    region = clamp(pymupdf.Rect(seeds[0]))
    for r in seeds[1:]:
        region |= r
    region = clamp(region)

    # Terminate on "no new absorptions", not on region growth: elements
    # straddling the band get clamped back to the same region every pass,
    # which previously made a growth-based loop spin forever.
    while True:
        added = False
        for r in rects:
            if any(r == a for a in absorbed):
                continue
            if min(_rect_gap(r, a) for a in absorbed) <= LINK_GAP \
                    and not _crosses_bar(r, region, bars):
                absorbed.append(r)
                region = clamp(region | r)
                added = True
        if not added:
            break

    spans = [(r, ltxt, font, size, lid)
             for r, ltxt, font, size, lid in _page_spans(page)
             if not CAPTION_RE.match(ltxt)
             and (r & brect).get_area() <= 0.5 * max(r.get_area(), 1e-6)]
    body_rects = [r for r, _, f, s, _ in spans if _span_class(f, s) == "body"]
    # lines mixing serif prose with Courier fragments = inline code inside
    # sentences; their code spans must never act as relay stepping stones
    line_classes = defaultdict(set)
    for _, _, f, s, lid in spans:
        line_classes[lid].add(_span_class(f, s))
    mixed_lines = {lid for lid, cls in line_classes.items()
                   if "body" in cls and len(cls) > 1}
    taken = [False] * len(spans)
    while True:
        added = False
        infl = pymupdf.Rect(region.x0 - LABEL_GAP, region.y0 - LABEL_GAP,
                            region.x1 + LABEL_GAP, region.y1 + LABEL_GAP)
        for i, (r, _, font, size, lid) in enumerate(spans):
            if taken[i] or _crosses_bar(r, region, bars):
                continue
            cls = _span_class(font, size)
            if cls == "label":
                if size > MAX_LABEL_SIZE:
                    continue  # section/callout headings are not labels
                if r.x0 > col_x1 + 6:
                    continue  # margin-note zone
                if r.intersects(infl) \
                        and not _body_in_corridor(r, region, body_rects):
                    pass
                else:
                    continue
            elif cls == "code":
                # code joins when almost enclosed, or via relay across an
                # empty corridor (text-only diagrams like Fig 7.7); a body
                # span in the corridor marks exterior listing/prose content.
                inter = region & r
                if inter.get_area() < 0.9 * max(r.get_area(), 1e-6):
                    if lid in mixed_lines \
                            or _rect_gap(r, region) > CODE_RELAY_GAP \
                            or _body_in_corridor(r, region, body_rects):
                        continue
            else:
                # body prose: absorb only if almost fully enclosed
                inter = region & r
                if inter.get_area() < 0.9 * max(r.get_area(), 1e-6):
                    continue
            taken[i] = True
            region = clamp(region | r)
            added = True
        if not added:
            break
    return region


def extract_figures(doc, caps):
    """Channel B: render each validated caption's figure region @3x."""
    FIG_DIR.mkdir(parents=True, exist_ok=True)
    mat = pymupdf.Matrix(ZOOM, ZOOM)
    _, col_x1 = _body_column(doc)
    by_page = {}
    for c in caps:
        by_page.setdefault(c["page"], []).append(c)
    results, failures = [], []
    for c in caps:
        page = doc[c["page"] - 1]
        others = [o for o in by_page[c["page"]] if o is not c]
        above = [o["block_rect"].y1 for o in others
                 if o["block_rect"].y1 <= c["rect"].y0]
        below = [o["block_rect"].y0 for o in others
                 if o["block_rect"].y0 >= c["rect"].y1]
        y_min = max(above + [0.0])
        y_max = min(below + [page.rect.height])
        region = build_figure_region(page, c, y_min, y_max, col_x1)
        if region is None or region.is_empty or region.width < 20 or region.height < 20:
            failures.append({"fig": c["fig"], "page": c["page"],
                             "reason": f"bad region {region}"})
            continue
        clip = pymupdf.Rect(
            max(0, region.x0 - PAD),
            max(y_min + 2, region.y0 - PAD),
            min(page.rect.width, region.x1 + PAD),
            min(y_max - 2, region.y1 + PAD),
        )
        name = f"Fig{c['fig']}_p{c['page']:03d}.png"
        path = FIG_DIR / name
        page.get_pixmap(clip=clip, matrix=mat).save(path)
        results.append({
            "fig": c["fig"],
            "page": c["page"],
            "file": str(path.relative_to(OUT_DIR)),
            "clip": [round(v, 1) for v in clip],
        })
    return results, failures


# ---------------------------------------------------------------- channel A
def extract_embedded(doc):
    """Unique-xref raster images, original bytes (smask/CMYK handled)."""
    EMB_DIR.mkdir(parents=True, exist_ok=True)
    seen = {}
    order = []
    for pno in range(len(doc)):
        for im in doc.get_page_images(pno, full=True):
            xref, smask = im[0], im[1]
            if xref not in seen:
                seen[xref] = smask
                order.append((xref, pno))
    saved = []
    for xref, first_page in order:
        smask = seen[xref]
        info = doc.extract_image(xref)
        ext = info["ext"]
        name = f"p{first_page + 1:03d}_xref{xref}.{ext}"
        path = EMB_DIR / name
        if smask:
            pix = pymupdf.Pixmap(doc, xref)
            mask = pymupdf.Pixmap(doc, smask)
            pix = pymupdf.Pixmap(pix, mask)
            if pix.colorspace and pix.colorspace.n > 3:
                pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
            pix.save(path)
        else:
            path.write_bytes(info["image"])
        saved.append({
            "file": str(path.relative_to(OUT_DIR)),
            "page": first_page + 1,
            "xref": xref,
            "width": info["width"],
            "height": info["height"],
            "format": ext,
        })
    return saved


# ---------------------------------------------------------------- verify
def verify(embedded, figures, failures, expected_figs):
    report = {"V1_embedded": None, "V2_coverage": None,
              "V3_content": None, "failures": failures}
    ok = True

    # V1: embedded complete + PIL openable + dims match
    v1_problems = []
    for e in embedded:
        p = OUT_DIR / e["file"]
        try:
            img = Image.open(p)
            img.verify()
            img = Image.open(p)
            if img.size != (e["width"], e["height"]):
                v1_problems.append(
                    f"{e['file']}: size {img.size} != {(e['width'], e['height'])}")
        except Exception as exc:
            v1_problems.append(f"{e['file']}: {exc}")
    report["V1_embedded"] = {
        "pass": len(embedded) == 33 and not v1_problems,
        "count": len(embedded),
        "problems": v1_problems,
    }
    ok &= report["V1_embedded"]["pass"]

    # V2: coverage — every expected fig number rendered exactly once
    got = Counter(f["fig"] for f in figures)
    missing = sorted(set(expected_figs) - set(got),
                     key=lambda s: [int(x) for x in s.split(".")])
    dupes = sorted([f for f, n in got.items() if n > 1],
                   key=lambda s: [int(x) for x in s.split(".")])
    report["V2_coverage"] = {
        "pass": not missing and not dupes and len(figures) == len(expected_figs),
        "expected": len(expected_figs),
        "rendered": len(figures),
        "missing": missing,
        "duplicates": dupes,
    }
    ok &= report["V2_coverage"]["pass"]

    # V3: content validity — non-blank, reasonable size
    import statistics
    v3_problems = []
    for f in figures:
        p = OUT_DIR / f["file"]
        img = Image.open(p).convert("L")
        w, h = img.size
        if min(w, h) < 100:
            v3_problems.append(f"{f['file']}: too small {w}x{h}")
            continue
        small = img.resize((min(w, 200), min(h, 200)))
        std = statistics.pstdev(small.tobytes())
        if std < 5:
            v3_problems.append(f"{f['file']}: near-blank (std={std:.1f})")
    report["V3_content"] = {"pass": not v3_problems, "problems": v3_problems}
    ok &= report["V3_content"]["pass"]

    manifest = {
        "pdf": PDF_PATH.name,
        "embedded": embedded,
        "figures": figures,
        "verify": report,
    }
    (OUT_DIR / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False))

    print(f"embedded : {len(embedded)} files  "
          f"V1={'PASS' if report['V1_embedded']['pass'] else 'FAIL'}")
    print(f"figures  : {len(figures)}/{report['V2_coverage']['expected']}  "
          f"V2={'PASS' if report['V2_coverage']['pass'] else 'FAIL'}")
    print(f"content  : V3={'PASS' if report['V3_content']['pass'] else 'FAIL'}")
    for pr in v1_problems[:10]:
        print("  V1:", pr)
    for m in missing[:20]:
        print("  V2 missing:", m)
    for d in dupes[:20]:
        print("  V2 dup:", d)
    for pr in v3_problems[:15]:
        print("  V3:", pr)
    for fl in failures[:10]:
        print("  step-fail:", fl)
    print("manifest ->", OUT_DIR / "manifest.json")
    return ok


def main():
    argv = sys.argv[1:]
    if argv and argv[0] == "test-candidates":
        test_candidates()
        return 0
    if argv and argv[0] == "test-validate":
        test_validate()
        return 0

    import shutil
    if OUT_DIR.exists():
        shutil.rmtree(OUT_DIR)
    doc = pymupdf.open(PDF_PATH)
    embedded = extract_embedded(doc)
    caps = validate_candidates(doc, find_caption_candidates(doc))
    figures, failures = extract_figures(doc, caps)
    expected = {c["fig"] for c in caps}
    ok = verify(embedded, figures, failures, expected)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
