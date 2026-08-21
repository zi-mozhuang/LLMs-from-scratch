"""Extract all images from the Raschka LLM book PDF.

Two channels:
  A. embedded raster images  -> extracted_images/embedded/  (original bytes)
  B. vector figures          -> extracted_images/figures/   (region render @3x)

Run:  python3 extract_images.py
"""
import json
import re
import sys
from pathlib import Path

import pymupdf
from PIL import Image
import io

BASE = Path(__file__).parent
PDF_PATH = BASE / "Build a Large Language Model (From Scratch) (Sebastian Raschka).pdf"
OUT_DIR = BASE / "extracted_images"
EMB_DIR = OUT_DIR / "embedded"
FIG_DIR = OUT_DIR / "figures"

ZOOM = 3
CLUSTER_GAP = 15.0
PAD = 6.0
CAPTION_RE = re.compile(r"^\s*Figure\s+(\d+\.\d+)\b")


def extract_embedded(doc):
    """Channel A: unique-xref raster images, original bytes."""
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


def page_caption_blocks(page):
    """Return [(fig_num, rect)] for Figure X.Y caption blocks on page."""
    out = []
    for b in page.get_text("blocks"):
        x0, y0, x1, y1, txt = b[0], b[1], b[2], b[3], b[4]
        m = CAPTION_RE.match(txt)
        if m:
            out.append((m.group(1), pymupdf.Rect(x0, y0, x1, y1)))
    out.sort(key=lambda t: t[1].y0)
    return out


def collect_element_rects(page):
    rects = []
    for d in page.get_drawings():
        r = d["rect"]
        if r.width > 0.5 or r.height > 0.5:
            rects.append(pymupdf.Rect(r))
    for im in page.get_images(full=True):
        for r in page.get_image_rects(im[0]):
            rects.append(pymupdf.Rect(r))
    return rects


def cluster_rects(rects):
    """Union-find clustering with vertical/horizontal gap tolerance."""
    n = len(rects)
    parent = list(range(n))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i, j):
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[ri] = rj

    for i in range(n):
        for j in range(i + 1, n):
            a, b = rects[i], rects[j]
            gap_x = max(0.0, max(a.x0, b.x0) - min(a.x1, b.x1))
            gap_y = max(0.0, max(a.y0, b.y0) - min(a.y1, b.y1))
            if gap_x <= CLUSTER_GAP and gap_y <= CLUSTER_GAP:
                union(i, j)
    clusters = {}
    for i, r in enumerate(rects):
        root = find(i)
        if root not in clusters:
            clusters[root] = pymupdf.Rect(r)
        else:
            clusters[root] |= r
    return list(clusters.values())


def extract_figures(doc):
    """Channel B: render region above each Figure caption."""
    FIG_DIR.mkdir(parents=True, exist_ok=True)
    mat = pymupdf.Matrix(ZOOM, ZOOM)
    results, failures = [], []
    for pno in range(len(doc)):
        page = doc[pno]
        caps = page_caption_blocks(page)
        if not caps:
            continue
        rects = collect_element_rects(page)
        prev_bottom = 0.0
        for fig_num, cap_rect in caps:
            band_top = prev_bottom
            band = [r for r in rects
                    if r.y1 <= cap_rect.y0 + 2.0 and r.y0 >= band_top - 2.0]
            prev_bottom = cap_rect.y1
            if not band:
                failures.append({"fig": fig_num, "page": pno + 1,
                                 "reason": "no vector elements above caption"})
                continue
            clusters = cluster_rects(band)
            # figure = cluster(s) overlapping the band's lower part (just above caption)
            cap_top = cap_rect.y0
            main = [c for c in clusters if c.y1 >= cap_top - 40]
            if not main:
                main = [max(clusters, key=lambda c: c.y1)]
            region = main[0]
            for c in main[1:]:
                region |= c
            clip = pymupdf.Rect(
                max(0, region.x0 - PAD),
                max(0, region.y0 - PAD),
                min(page.rect.width, region.x1 + PAD),
                min(page.rect.height, cap_rect.y0 - 1),
            )
            if clip.width < 20 or clip.height < 20:
                failures.append({"fig": fig_num, "page": pno + 1,
                                 "reason": f"degenerate clip {clip}"})
                continue
            name = f"Fig{fig_num}_p{pno + 1:03d}.png"
            path = FIG_DIR / name
            page.get_pixmap(clip=clip, matrix=mat).save(path)
            results.append({
                "fig": fig_num,
                "page": pno + 1,
                "file": str(path.relative_to(OUT_DIR)),
                "clip": [round(v, 1) for v in clip],
            })
    return results, failures


def verify(embedded, figures, failures):
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
                v1_problems.append(f"{e['file']}: size {img.size} != {(e['width'], e['height'])}")
        except Exception as exc:
            v1_problems.append(f"{e['file']}: {exc}")
    report["V1_embedded"] = {
        "pass": len(embedded) == 33 and not v1_problems,
        "count": len(embedded),
        "problems": v1_problems,
    }
    ok &= report["V1_embedded"]["pass"]

    # V2: coverage — every expected Fig X.Y has exactly one file
    doc = pymupdf.open(PDF_PATH)
    expected = set()
    for pno in range(len(doc)):
        for fig_num, _ in page_caption_blocks(doc[pno]):
            expected.add(fig_num)
    got = {f["fig"]: f for f in figures}
    missing = sorted(expected - set(got), key=lambda s: [int(x) for x in s.split(".")])
    dupes = [f for f in figures if sum(1 for g in figures if g["fig"] == f["fig"]) > 1]
    report["V2_coverage"] = {
        "pass": not missing and not dupes,
        "expected": len(expected),
        "rendered": len(figures),
        "missing": missing,
        "duplicates": [d["fig"] for d in dupes],
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
        px = list(img.resize((min(w, 200), min(h, 200))).getdata())
        if statistics.pstdev(px) < 5:
            v3_problems.append(f"{f['file']}: near-blank (std={statistics.pstdev(px):.1f})")
    report["V3_content"] = {"pass": not v3_problems, "problems": v3_problems}
    ok &= report["V3_content"]["pass"]

    manifest = {
        "pdf": PDF_PATH.name,
        "pages": len(doc),
        "embedded": embedded,
        "figures": figures,
        "verify": report,
    }
    (OUT_DIR / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False))

    print(f"embedded : {len(embedded)} files  V1={'PASS' if report['V1_embedded']['pass'] else 'FAIL'}")
    print(f"figures  : {len(figures)}/{report['V2_coverage']['expected']}  "
          f"V2={'PASS' if report['V2_coverage']['pass'] else 'FAIL'}")
    print(f"content  : V3={'PASS' if report['V3_content']['pass'] else 'FAIL'}")
    for pr in v1_problems[:10]:
        print("  V1:", pr)
    for m in missing[:20]:
        print("  V2 missing:", m)
    for pr in v3_problems[:10]:
        print("  V3:", pr)
    for fl in failures[:10]:
        print("  step-fail:", fl)
    print("manifest ->", OUT_DIR / "manifest.json")
    return ok


def main():
    if OUT_DIR.exists():
        import shutil
        shutil.rmtree(OUT_DIR)
    doc = pymupdf.open(PDF_PATH)
    embedded = extract_embedded(doc)
    figures, failures = extract_figures(doc)
    ok = verify(embedded, figures, failures)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
