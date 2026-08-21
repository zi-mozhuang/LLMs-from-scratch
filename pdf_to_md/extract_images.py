"""Extract all images from the Raschka LLM book PDF.

Two channels:
  A. embedded raster images  -> extracted_images/embedded/  (original bytes)
  B. vector figures          -> extracted_images/figures/   (region render @3x)

Pipeline (each stage independently tested):
  1. find_caption_candidates : line-level 'Figure X.Y' scan (incl. body refs)
  2. validate_candidates     : geometric check rejects refs, keeps captions
  3. build_figure_region     : grow figure region from seeds + label blocks

Usage:
  python3 extract_images.py test-candidates   # stage 1 self-test
  python3 extract_images.py test-validate     # stage 2 self-test
  python3 extract_images.py                   # full run + V1-V3 verification
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

ZOOM = 3
PAD = 6.0
CAPTION_RE = re.compile(r"^Figure\s+(\d+\.\d+)\b")
BARE_RE = re.compile(r"^Figure\s+\d+\.\d+$")

ABOVE_GAP = 80.0    # max vertical distance element-bottom -> caption-top
SIDE_GAP = 30.0     # max horizontal gap for side-by-side layout
GROW_GAP = 10.0     # region growth: absorb elements within this distance
HEADER_Y = 45.0     # header/footer exclusion band
NARROW_FRAC = 0.75  # blocks narrower than this fraction of body width are labels


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


def _body_width(doc):
    """Median width of wide text blocks = full body column width."""
    ws = sorted(r.width for p in range(len(doc))
                for r, _ in _text_blocks(doc[p]) if r.width > 150)
    return ws[len(ws) // 2]


LINK_GAP = 80.0     # max element-to-region gap for chain absorption


def _rect_gap(a, b):
    """Chebyshev-style rect distance: max of x-gap and y-gap."""
    dx = max(0.0, max(a.x0, b.x0) - min(a.x1, b.x1))
    dy = max(0.0, max(a.y0, b.y0) - min(a.y1, b.y1))
    return max(dx, dy)


def build_figure_region(page, cap, y_min, y_max, body_w):
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
    Caption blocks are never absorbed.
    """
    lrect, brect = cap["rect"], cap["block_rect"]
    band = pymupdf.Rect(0, y_min, page.rect.width, y_max)
    rects = [r for r in collect_element_rects(page) if r.intersects(band)]
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

    changed = True
    while changed:
        changed = False
        for r in rects:
            if region.contains(r):
                continue
            if min(_rect_gap(r, a) for a in absorbed) <= LINK_GAP:
                absorbed.append(r)
                region = clamp(region | r)
                changed = True

    # candidate label blocks: not captions, not the caption's own block
    labels = []
    for r, txt in _text_blocks(page):
        if CAPTION_RE.match(txt.strip()):
            continue
        inter = r & brect
        if inter.get_area() > 0.5 * max(r.get_area(), 1e-6):
            continue  # own caption block
        labels.append(r)

    narrow_lim = NARROW_FRAC * body_w
    changed = True
    while changed:
        changed = False
        infl = pymupdf.Rect(region.x0 - GROW_GAP, region.y0 - GROW_GAP,
                            region.x1 + GROW_GAP, region.y1 + GROW_GAP)
        for r in labels:
            if r.intersects(infl) and not region.contains(r):
                below_caption = (r.y0 >= lrect.y1 - 2
                                 and r.x0 < brect.x1 and r.x1 > brect.x0)
                if below_caption and r.width >= narrow_lim:
                    continue  # caption continuation / body prose under caption
                if r.width >= narrow_lim:
                    inter = region & r
                    if inter.get_area() < 0.8 * r.get_area():
                        continue  # wide block only if already mostly inside
                region = clamp(region | r)
                changed = True
    return region


def extract_figures(doc, caps):
    """Channel B: render each validated caption's figure region @3x."""
    FIG_DIR.mkdir(parents=True, exist_ok=True)
    mat = pymupdf.Matrix(ZOOM, ZOOM)
    body_w = _body_width(doc)
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
        region = build_figure_region(page, c, y_min, y_max, body_w)
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
