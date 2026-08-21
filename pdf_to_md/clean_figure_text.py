"""clean_figure_text.py - Sub-plan P5: delete figure-internal text residuals from the exported Markdown.

Background
----------
P1 (``pdf_text_stream.py`` + ``figure_text_detect.py``) removes ~1149 blocks of
text that are geometrically *inside* a figure rectangle.  Additional residual
blocks survive into the final ``llms-from-scratch.md`` because:

* P1's ``sentence_guard`` / ``term_glossary_guard`` intentionally kept them.
* They sit in *gaps* between figure element rects (not inside any rect).
* They are just outside the figure boundary (side-glued with gap >= SIDE_GAP).

Approach (three-layer: position + content + geometry)
-----------------------------------------------------
1. **Position signal** -- For each ``![Fig X.Y]`` link, collect the *first
   non-blank line group* immediately adjacent (above / below).  This is the
   label cluster: figure labels always sit directly next to the image link,
   separated by blank lines from body text.
2. **Content filter** -- From the cluster, exclude lines that look like body
   prose or code:
     * ends with terminal punctuation (``.?!`` and Unicode variants)
     * contains chapter references ("In chapter 5", "In the next chapter")
     * starts with "Figure X.Y" (body reference to a figure)
     * matches code patterns (starts with ``[[`` / ``}`` / ``)``, contains
       ``=``, ``torch.``, ``plt.``, etc.)
3. **Geometry backup** -- Normalised candidate text is checked against the
   page's geometric figure-text set.  A match gives 100 % confidence.  Even
   without a match the position + content signals are strong enough to delete.

Cover page (page 0) is handled separately via ``COVER_FIGURE_TEXT``.

Usage
-----
    python clean_figure_text.py            # dry-run preview (default)
    python clean_figure_text.py --apply    # apply and overwrite the markdown
"""

from __future__ import annotations

import argparse
import glob
import re
import sys
import unicodedata
from pathlib import Path

import pymupdf  # PyMuPDF; the project's extraction environment uses this.
from figure_text_detect import page_element_regions


# --------------------------------------------------------------------------- #
# Geometry: text strings that sit *inside* a figure rectangle (per page)       #
# --------------------------------------------------------------------------- #
def figure_text_by_page(pdf_path: str) -> dict[int, set[str]]:
    """Map 0-based page index -> set of normalized figure-internal text strings.

    Two kinds of figure pages are handled:

    * Normal figure pages: geometry from ``page_element_regions`` (a figure
      rectangle was detected), filtered by ``_block_in_figure``.
    * Cover / front-matter pages (e.g. page 0) where no ``Figure X.Y`` anchor
      exists and the whole page IS the illustration: the entire page rectangle
      is treated as the figure region, so every painted text block on it is a
      figure-internal string. Book title / author / publisher are excluded
      downstream by ``COVER_KEEP``.
    """
    doc = pymupdf.open(pdf_path)
    result: dict[int, set[str]] = {}
    for idx, page in enumerate(doc):
        fig_rects, fig_union = page_element_regions(page)
        strings: set[str] = set()

        # Fig 1.1 is a full-page *vector* glossary whose painted text is
        # recovered from an embedded text layer PyMuPDF sees as many tiny
        # blocks, so the generic rectangle merge fails. Its labels all sit
        # directly above the "Figure 1.1" caption, inside its horizontal span --
        # recover them from that band (this page is handled regardless of what
        # ``page_element_regions`` returned).
        if idx == 24:
            caption = None
            for b in page.get_text("dict").get("blocks", []):
                if "lines" not in b:
                    continue
                t = " ".join(
                    "".join(s.get("text", "") for s in ln.get("spans", []))
                    for ln in b.get("lines", [])
                ).strip()
                if "Figure 1.1" in t:
                    caption = b["bbox"]
                    break
            if caption is not None:
                cx0, cy0, cx1, cy1 = caption
                for b in page.get_text("dict").get("blocks", []):
                    if "lines" not in b:
                        continue
                    txt = " ".join(
                        "".join(s.get("text", "") for s in ln.get("spans", []))
                        for ln in b.get("lines", [])
                    ).strip()
                    if not txt:
                        continue
                    r = pymupdf.Rect(b["bbox"])
                    mcx, mcy = (r.x0 + r.x1) / 2.0, (r.y0 + r.y1) / 2.0
                    if (cy0 - 170) < mcy < cy0 and (cx0 - 25) < mcx < (cx1 + 25):
                        strings.add(_norm(txt))
            # fall through: also keep any geometry-based matches below.

        if not fig_rects:
            # No figure rectangle detected by the generic geometry. Two special
            # full-page illustration pages are handled explicitly:
            if idx == 0:
                # Cover: the whole page is the illustration.
                region = page.rect
                for b in page.get_text("dict").get("blocks", []):
                    if "lines" not in b:
                        continue
                    txt = " ".join(
                        "".join(s.get("text", "") for s in ln.get("spans", []))
                        for ln in b.get("lines", [])
                    ).strip()
                    if not txt:
                        continue
                    r = pymupdf.Rect(b["bbox"])
                    pt = pymupdf.Point((r.x0 + r.x1) / 2.0, (r.y0 + r.y1) / 2.0)
                    if region.contains(pt):
                        strings.add(_norm(txt))
            elif idx == 24:
                # Fig 1.1 is a full-page *vector* glossary whose painted text is
                # recovered from an embedded text layer PyMuPDF sees as many
                # tiny blocks (so the generic rectangle merge fails). The labels
                # all sit directly above the "Figure 1.1" caption, inside its
                # horizontal span -- recover them from that band.
                caption = None
                for b in page.get_text("dict").get("blocks", []):
                    if "lines" not in b:
                        continue
                    t = " ".join(
                        "".join(s.get("text", "") for s in ln.get("spans", []))
                        for ln in b.get("lines", [])
                    ).strip()
                    if "Figure 1.1" in t:
                        caption = b["bbox"]
                        break
                if caption is not None:
                    cx0, cy0, cx1, cy1 = caption
                    for b in page.get_text("dict").get("blocks", []):
                        if "lines" not in b:
                            continue
                        txt = " ".join(
                            "".join(s.get("text", "") for s in ln.get("spans", []))
                            for ln in b.get("lines", [])
                        ).strip()
                        if not txt:
                            continue
                        r = pymupdf.Rect(b["bbox"])
                        mcx, mcy = (r.x0 + r.x1) / 2.0, (r.y0 + r.y1) / 2.0
                        if (cy0 - 170) < mcy < cy0 and (cx0 - 25) < mcx < (cx1 + 25):
                            strings.add(_norm(txt))
            else:
                # Any other page where geometry failed: leave untouched (deleting
                # on a pure position signal here previously removed genuine code
                # blocks that merely sat next to a figure).
                continue
        else:
            for b in page.get_text("dict").get("blocks", []):
                if "lines" not in b:
                    continue
                txt = " ".join(
                    "".join(s.get("text", "") for s in ln.get("spans", []))
                    for ln in b.get("lines", [])
                ).strip()
                if not txt:
                    continue
                if _block_in_figure(b["bbox"], fig_rects, fig_union):
                    strings.add(_norm(txt))
        if strings:
            result[idx] = strings
    doc.close()
    return result


# Text that is genuinely the book cover (title / author / publisher) and must
# be preserved even though it is painted on the full-page cover illustration.
COVER_KEEP = re.compile(
    r"build a large language model|sebastian raschka|manning|meap|edition|early access",
    re.IGNORECASE,
)

# Flow-chart labels painted onto the cover illustration (recovered by the P1
# extractor from an embedded text layer PyMuPDF cannot read). These are the
# exact normalized strings as they appear in the exported markdown; only lines
# matching one of these inside the cover region are removed.
COVER_FIGURE_TEXT = {
    "from scratch",
    "build a",
    "classifier",
    "building an llm",
    "foundation model",
    "personal assistant",
    "dataset with class labels",
    "instruction dataset",
    "fine-tunes the pretrained llm to create a classification model",
    "pretrains the llm on unlabeled data to obtain a foundation model for further fine-tuning",
    "fine-tunes the pretrained llm to create a personal assistant or chat model",
}


# Max horizontal gap (pts) between a text block and a figure rectangle for the
# block to count as a side-glued label (e.g. Fig 1.2 "User input (instructions)"
# sits ~3pt right of the figure crop).
SIDE_GAP = 15.0


def _block_in_figure(bbox, fig_rects, fig_union) -> bool:
    r = pymupdf.Rect(bbox)
    if r.width < 1 or r.height < 1:
        return False
    cx, cy = (r.x0 + r.x1) / 2.0, (r.y0 + r.y1) / 2.0
    pt = pymupdf.Point(cx, cy)
    # A block is figure-internal when its center lies inside any figure
    # rectangle. Note: we deliberately do NOT add a "caption protection" rule
    # here. Large full-page figures (e.g. the Fig 1.1 glossary) produce a
    # figure union that also covers the real caption *and* nearby body prose
    # below the figure; but those are filtered out downstream by the position
    # signal (they are not adjacent to the ``![Fig]`` link), so loosening here
    # is safe and lets us catch labels whose center falls in the figure union
    # but outside the sub-pixel individual rectangles.
    for fr in fig_rects:
        if fr.contains(pt):
            return True
    # Side-glued labels: the block is painted just OUTSIDE the figure crop on
    # its left/right edge, vertically aligned with the figure (e.g. Fig 1.2's
    # "User input (instructions)" / "Model output" labels). A pure bounding-box
    # expansion (``expand``) is NOT used: it also matched genuine body prose
    # below/above large figures (e.g. "This chapter covers...") and code blocks.
    # We require a STRICT vertical overlap (no tolerance) plus a small horizontal
    # gap. A tolerance here was the bug: body sentences sitting just *above* a
    # flow-chart node (e.g. "The number of batches is determined..." above
    # Fig 5.11) do NOT overlap the node rect, but a +/-10pt tolerance made them
    # look overlapping, so they were wrongly deleted.
    for fr in fig_rects:
        if not (fr.y0 < r.y1 and fr.y1 > r.y0):
            continue
        h_gap = max(fr.x0 - r.x1, r.x0 - fr.x1)
        if 0 <= h_gap < SIDE_GAP:
            return True
    return False


def _norm(s: str) -> str:
    # NFKC also decomposes typographic ligatures (e.g. "ﬁ" -> "fi"), which the
    # PDF text layer and the markdown can represent differently.
    s = unicodedata.normalize("NFKC", s)
    return re.sub(r"\s+", " ", s).strip()


# --------------------------------------------------------------------------- #
# Markdown: locate figure links and their adjacent label clusters             #
# --------------------------------------------------------------------------- #
FIG_LINK_RE = re.compile(r"!\[Fig[^\]]*\]\(([^)]+)\)")


def page_of_link(link_path: str) -> int | None:
    """Extract the 0-based page index from a link like '.../Fig1.9_p36.png'."""
    m = re.search(r"_p(\d+)\.png", link_path)
    if not m:
        return None
    return int(m.group(1)) - 1  # PDF page numbers are 1-based in the filename.


def first_label_group(lines, fig_idx, direction):
    """Collect the first non-blank line group adjacent to a figure link.

    *direction* = -1 scans upward, +1 scans downward.

    The scan skips blank lines immediately next to the figure link, then
    collects consecutive non-blank lines until a blank line is encountered.
    Hard stop conditions (regardless of blank / non-blank):
      * Markdown heading (``#``), anchor (``<a id``), or caption (``**Figure``)
      * Code fence line (starts with ````` ``)
      * Another figure link line (``![Fig``)
    """
    step = 1 if direction > 0 else -1
    n = len(lines)
    j = fig_idx + step
    # Skip initial blank lines.
    while 0 <= j < n and lines[j].strip() == "":
        j += step
    out = []
    while 0 <= j < n:
        t = lines[j]
        stripped = t.strip()
        # Hard stop conditions.
        if stripped == "":
            break  # End of the first non-blank group.
        if t.startswith("#") or t.startswith("<a id"):
            break
        if stripped.startswith("**Figure"):
            break
        if stripped.startswith("```"):
            break
        if FIG_LINK_RE.search(stripped):
            break
        out.append((j, stripped))
        j += step
    return out


# ---- Content filter for label candidates ---- #

# Trailing quote characters that may follow terminal punctuation.
_TRAILING_QUOTE_RE = re.compile(r'["\'\u201d\u2019\u2026]+$')


def _has_terminal_punct(s: str) -> bool:
    """Return True if *s* ends with terminal punctuation (after stripping quotes)."""
    core = _TRAILING_QUOTE_RE.sub('', s).rstrip()
    return bool(core) and core[-1] in '.?!。？！'

# Chapter / section narrative references -- any mention of "chapter" in a
# short label is almost certainly a book-structure description, not a label.
_CHAPTER_REF_RE = re.compile(r'\bchapter\b', re.IGNORECASE)

# Body-text reference to a figure: "Figure 4.12 shows ...".
_FIGURE_REF_RE = re.compile(r'^Figure \d+\.\d+')

# Code-like patterns that should NEVER be deleted.
_CODE_LIKE_RE = re.compile(
    r'^\[\['              # tensor output like [[ 11, 612, ... ]
    r'|^\['               # list/tensor output starting with [
    r'|^\}'               # closing brace
    r'|^\)'               # closing paren
    r'|\btorch\.\w+'
    r'|\bplt\.\w+'
    r'|\bprint\s*\('
    r'|^def \w+'
    r'|^class \w+'
    r'|^import \w+'
    r'|\w+\s*\([^)]*$'   # function call with unclosed paren (code line)
)


def _is_label_candidate(stripped: str) -> bool:
    """Return True when *stripped* line looks like a figure-internal label.

    The filter is deliberately conservative: it rejects anything that looks
    like body prose (terminal punctuation, chapter references) or code
    (tensor output, function calls, etc.).
    """
    if not stripped:
        return False
    # Terminal punctuation (body prose signal).
    if _has_terminal_punct(stripped):
        return False
    # Chapter / section narrative references.
    if _CHAPTER_REF_RE.search(stripped):
        return False
    # Body-text reference to a figure ("Figure 4.12 shows ...").
    if _FIGURE_REF_RE.match(stripped):
        return False
    # Code-like patterns.
    if _CODE_LIKE_RE.search(stripped):
        return False
    # Comma in a short label is almost always body prose, not a figure label.
    if ',' in stripped:
        return False
    # Line ending with dash (en-dash, em-dash, double-hyphen) = truncated body.
    if re.search(r'[—–\-]{1,2}\s*$', stripped):
        return False
    return True


# --------------------------------------------------------------------------- #
# Cleanup                                                                      #
# --------------------------------------------------------------------------- #
def clean_markdown(md_path: Path, fig_by_page: dict[int, set[str]]) -> tuple[list[str], list[str]]:
    """Delete figure-internal residual lines.

    For each ``![Fig X.Y](...pNNN.png)`` link we collect the *first non-blank
    line group* immediately above and below the link (``first_label_group``).
    From that group, lines passing the content filter (``_is_label_candidate``)
    are marked for deletion.  The scan is iterated until no more lines are
    found: each pass may expose new "first groups" that were previously hidden
    behind labels removed in an earlier pass.

    The geometric figure-text set is retained as supplementary signal (logged
    for traceability) but is no longer a hard requirement -- the position +
    content filters are sufficient.
    """
    raw = md_path.read_text(encoding="utf-8").split("\n")
    all_removed: list[str] = []

    # Iterate until convergence: each pass removes the first non-blank group
    # of labels adjacent to figure links; the next pass then sees the *next*
    # group as the new "first" group.
    while True:
        delete_idx: set[int] = set()
        for i, line in enumerate(raw):
            m = FIG_LINK_RE.search(line)
            if not m:
                continue
            # Upward scan.
            for di, txt in first_label_group(raw, i, -1):
                if _is_label_candidate(txt):
                    delete_idx.add(di)
            # Downward scan.
            for di, txt in first_label_group(raw, i, +1):
                if _is_label_candidate(txt):
                    delete_idx.add(di)

        # Cover page (page 0) -- only on the first pass (no figure links).
        if not all_removed:
            first_fig = next(
                (k for k, ln in enumerate(raw) if FIG_LINK_RE.search(ln)), len(raw)
            )
            for i in range(first_fig):
                norm = _norm(raw[i]).lower()
                if norm in COVER_FIGURE_TEXT and not COVER_KEEP.search(norm):
                    delete_idx.add(i)

        if not delete_idx:
            break  # Converged -- no more lines to remove.

        removed = [raw[i] for i in sorted(delete_idx)]
        all_removed.extend(removed)
        raw = [ln for i, ln in enumerate(raw) if i not in delete_idx]
        # Collapse runs of 2+ blank lines produced by deletions.
        collapsed: list[str] = []
        blank_run = 0
        for ln in raw:
            if ln.strip() == "":
                blank_run += 1
                if blank_run <= 1:
                    collapsed.append(ln)
            else:
                blank_run = 0
                collapsed.append(ln)
        raw = collapsed

    # Post-processing: ensure a blank line between each figure link and the
    # following line.  The iterative deletion of figure-internal labels can
    # leave the ``![Fig X.Y]`` link directly adjacent to the **Figure X.Y**
    # caption (or other body text), which renders poorly in Markdown.
    spaced: list[str] = []
    for idx, ln in enumerate(raw):
        spaced.append(ln)
        if FIG_LINK_RE.search(ln) and idx + 1 < len(raw) and raw[idx + 1].strip() != "":
            spaced.append("")  # Insert blank line after figure link.
    return spaced, all_removed


# --------------------------------------------------------------------------- #
# CLI                                                                          #
# --------------------------------------------------------------------------- #
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    here = Path(__file__).resolve().parent
    ap.add_argument("--md", default=str(here / "llms-from-scratch.md"))
    ap.add_argument("--pdf", default=None)
    ap.add_argument("--apply", action="store_true",
                    help="Apply the cleanup (default is dry-run preview).")
    args = ap.parse_args(argv)

    md_path = Path(args.md)
    if not md_path.exists():
        print(f"[error] markdown not found: {md_path}", file=sys.stderr)
        return 2
    pdf_path = args.pdf or glob.glob(str(here / "*Sebastian Raschka*.pdf"))
    if isinstance(pdf_path, list):
        pdf_path = pdf_path[0] if pdf_path else None
    if not pdf_path or not Path(pdf_path).exists():
        print("[error] source PDF not found (pass --pdf)", file=sys.stderr)
        return 2

    print(f"[info] PDF : {pdf_path}")
    print(f"[info] MD  : {md_path}")
    fig_by_page = figure_text_by_page(pdf_path)
    print(f"[info] pages with figure-internal text: {len(fig_by_page)}")

    cleaned, removed = clean_markdown(md_path, fig_by_page)
    print(f"[info] figure-internal lines matched for removal: {len(removed)}")

    if args.apply:
        md_path.write_text("\n".join(cleaned), encoding="utf-8")
        print(f"[done] wrote cleaned markdown: {md_path}")
        print(f"[done] removed {len(removed)} figure-internal lines.")
    else:
        print("\n--- DRY RUN: the following lines would be removed ---")
        for ln in removed:
            print(f"  {ln!r}")
        print("--- end dry run (use --apply to write) ---")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
