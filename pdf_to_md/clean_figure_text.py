"""clean_figure_text.py - Sub-plan P5: delete figure-internal text residuals from the exported Markdown.

Background
----------
P1 (``pdf_text_stream.py`` + ``figure_text_detect.py``) removes ~1149 blocks of
text that are geometrically *inside* a figure rectangle.  Additional residual
blocks survive into the final ``llms-from-scratch.md`` because:

* P1's ``sentence_guard`` / ``term_glossary_guard`` intentionally kept them.
* They sit in *gaps* between figure element rects (not inside any rect).
* They sit in *gaps* between figure element rects (not inside any rect).

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
from figure_text_detect import page_element_regions, is_figure_text


# --------------------------------------------------------------------------- #
# Geometry: text strings that sit *inside* a figure rectangle (per page)       #
# --------------------------------------------------------------------------- #
def figure_text_by_page(pdf_path: str) -> tuple[dict[int, set[str]], set[int]]:
    """Return (fig_by_page, fullpage_art_pages).

    ``fig_by_page[page_idx]`` = set of normalized figure-internal text strings
    on that page (geometry-based, via ``is_figure_text`` / cover handling).

    ``fullpage_art_pages`` = page indices that are *entirely* an illustration
    (the figure union covers most of the page and there is no real body prose).
    On these pages every painted string is decorative, so the markdown-side
    cleaner deletes any line whose normalized text is in that page's set,
    regardless of proximity to a ``**Figure X.Y**`` title (which such pages
    lack -- e.g. cover, back cover, full-page glossary).
    """
    doc = pymupdf.open(pdf_path)
    result: dict[int, set[str]] = {}
    fullpage: set[int] = set()
    for idx, page in enumerate(doc):
        fig_rects, fig_union = page_element_regions(page)
        strings: set[str] = set()
        page_area = float(page.rect.width * page.rect.height)
        union_area = float(fig_union.get_area()) if fig_union is not None else 0.0
        # A page whose figure union covers >= 90% of the page area is treated as
        # a full-page illustration (cover / back cover / full-page glossary).
        # NOTE: a threshold as low as 0.55 wrongly flagged normal content pages
        # (e.g. p60, p80) whose several small figures merge into a large union --
        # those pages still carry body prose and must NOT be glob-delivered.
        is_fullpage = fig_union is not None and union_area / page_area >= 0.90


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
                # Use the same classifier as P1 (is_figure_text) rather than the
                # is_figure_text applies the sentence / term-glossary / font guards
                # so only true figure LABELS (short, non-sentence, non-code) are
                # collected here; a looser geometry-only test would also sweep in
                # genuine code blocks sitting inside a large figure's union.
                # is_figure_text applies the sentence / term-glossary / font guards
                # so only true figure LABELS (short, non-sentence, non-code) are
                # collected here.
                if is_figure_text(b, fig_rects, fig_union, page.rect.height):
                    strings.add(_norm(txt))
        if strings:
            result[idx] = strings
        if is_fullpage:
            fullpage.add(idx)
    doc.close()
    return result, fullpage


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


def _norm(s: str) -> str:
    # NFKC also decomposes typographic ligatures (e.g. "ﬁ" -> "fi"), which the
    # PDF text layer and the markdown can represent differently.
    s = unicodedata.normalize("NFKC", s)
    return re.sub(r"\s+", " ", s).strip()


# --------------------------------------------------------------------------- #
# Markdown: locate figure links and their adjacent label clusters             #
# --------------------------------------------------------------------------- #
FIG_LINK_RE = re.compile(r"!\[Fig[^\]]*\]\(([^)]+)\)")
FIG_TITLE_RE = re.compile(r"^\*\*Figure\s+\d+\.\d+")
# How many lines above/below a **Figure X.Y** title to scan for residual labels.
# Figure-internal labels always sit within a few lines of the caption (either
# directly above the title -- the PDF extraction order -- or just below the
# embedded image link P3 inserts after the title).
WINDOW = 40


def page_of_link(link_path: str) -> int | None:
    """Extract the 0-based page index from a link like '.../Fig1.9_p36.png'."""
    m = re.search(r"_p(\d+)\.png", link_path)
    if not m:
        return None
    return int(m.group(1)) - 1  # PDF page numbers are 1-based in the filename.


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

# Words that mark a line as BODY PROSE (a sentence / caption sentence), not a
# figure-internal label. Figure labels are noun phrases ("Input text",
# "Decoder", "Preprocessing steps"); body descriptions contain verbs
# ("The output is", "This prints", "Every effort moves you").
_VERB_RE = re.compile(
    r"\b(the|this|these|that|a|an|is|are|was|were|prints?|returns?|"
    r"resulting|results|computed?|encodes?|contain(s|ing)?|show(s)?|moves?|"
    r"every|you|i|am|we|they|it|here|below|above|following|given|using|"
    r"obtain(s|ed)?|produce(s|d)?|generate(s|d)?)\b",
    re.IGNORECASE,
)


def _is_label_candidate(stripped: str) -> bool:
    """Return True when *stripped* line is a figure-internal LABEL (safe to delete).

    The classifier is deliberately STRICT: a figure label is a short noun phrase
    painted onto a diagram (e.g. "Input text", "Decoder", "Preprocessing steps",
    "Train", "Labeled dataset").  Anything that resembles body prose or CODE must
    be rejected, because we no longer cross-check against the PDF geometry set
    (that set proved unreliable: on the CJK-filename PDF build, code blocks that
    physically overlap a large figure's union rectangle were mis-classified as
    figure-internal, which would have deleted real code).

    Reject (keep) when the line:
      * is empty
      * ends with terminal punctuation (body prose)
      * mentions a chapter / section reference
      * is a body-text reference to a figure ("Figure 4.12 shows ...")
      * contains CODE syntax: parentheses/brackets/braces, ``=``, ``.method(``,
        code keywords (import/def/class/for/if/return/print/torch/plt/with/open),
        or starts/ends with backtick (inline code)
      * contains a comma (almost always body prose, not a 1-2 word label)
      * ends with a dash (truncated body sentence)
      * is too long (> 6 words) -- labels are short
      * contains digits/punctuation other than a trailing colon or hyphen
        (e.g. "tensor(1.8)", "Z2", "q(2)" are math annotations, handled by the
        full-page cover logic instead, not inline-figure labels)
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
    # Code syntax -- NEVER delete.
    if _CODE_LIKE_RE.search(stripped):
        return False
    if any(ch in stripped for ch in "()[]{}="):
        return False
    if re.search(r"\.\w+\(", stripped):   # method call e.g. model.eval()
        return False
    if stripped.startswith("`") or stripped.endswith("`"):
        return False  # inline code fragment -- keep
    # Comma in a short label is almost always body prose, not a figure label.
    if "," in stripped:
        return False
    # Any verb / sentence word marks this as body prose, not a diagram label.
    if _VERB_RE.search(stripped):
        return False
    # Line ending with dash (en-dash, em-dash, double-hyphen) = truncated body.
    if re.search(r"[—–\-]{1,2}\s*$", stripped):
        return False
    # Length guard: figure labels are short noun phrases.
    if len(stripped.split()) > 6:
        return False
    # Reject lines with digits or stray punctuation (math annotations like
    # "Z2", "tensor(..)", "q(2)" -- not inline labels). Allow a trailing colon
    # (e.g. "Vocabulary:") and internal hyphens / spaces.
    core = stripped.rstrip(":").strip()
    if re.search(r"\d", core):
        return False
    if re.search(r"[^\w\s\-]", core):
        return False
    return True


# --------------------------------------------------------------------------- #
# Cleanup                                                                      #
# --------------------------------------------------------------------------- #
def clean_markdown(
    md_path: Path,
    fig_by_page: dict[int, set[str]],
    fullpage_pages: set[int],
) -> tuple[list[str], list[str]]:
    """Delete figure-internal residual lines.

    Two complementary, geometry-backed strategies:

    1. **Inline figures (have a ``**Figure X.Y**`` title).**  The PDF extracts a
       figure's internal labels *above* the ``**Figure X.Y**`` caption (extraction
       order), while P3 inserts the ``![Fig]`` image *below* the caption.  So the
       labels always sit within ``WINDOW`` lines of the ``**Figure X.Y**`` title.
       We scan that window and delete any line whose normalized text is in the
       geometric figure-text set (``fig_by_page``) and passes the content filter.
       This catches labels above *and* below the title, fixing the old bug where
       the upward scan stopped at the ``**Figure**`` hard-stop.

    2. **Full-page illustration pages** (cover / back cover / full-page glossary)
       identified by ``fullpage_pages`` (figure union covers >= 55% of the page).
       These have no ``**Figure X.Y**`` anchor, so we delete any line anywhere in
       the markdown whose normalized text is in that page's geometric set.  Because
       such pages are pure decoration, global deletion is safe and does not touch
       body prose (which never lives on these pages).

    Iterates until convergence so removal of one label can expose the next.
    """
    raw = md_path.read_text(encoding="utf-8").split("\n")
    all_removed: list[str] = []

    # Union of all geometric figure-internal strings (from the ENGLISH-filename
    # PDF, which is the same source P1 used to build the markdown). This is used
    # as a *confirmation* signal: a line is only deleted when it BOTH looks like
    # a label (_is_label_candidate) AND is confirmed to sit inside a figure
    # rectangle in the PDF. This two-factor check is what makes the cleanup safe
    # even though the geometry or the content heuristic alone would each, on
    # their own, occasionally misfire.
    all_fig: set[str] = set()
    for s in fig_by_page.values():
        all_fig |= {x.lower() for x in s}

    while True:
        delete_idx: set[int] = set()

        # Strategy 1: inline figures. Figure-internal labels sit within WINDOW
        # lines of the **Figure X.Y** caption (extraction order puts them ABOVE
        # the caption; P3 inserts the image BELOW it). A candidate is deleted
        # only when it passes BOTH the content filter and the geometric check.
        for i, line in enumerate(raw):
            if not FIG_TITLE_RE.match(line.strip()):
                continue
            lo = max(0, i - WINDOW)
            hi = min(len(raw), i + WINDOW + 1)
            for j in range(lo, hi):
                if j == i:
                    continue
                norm = _norm(raw[j]).lower()
                if norm in all_fig and _is_label_candidate(raw[j].strip()):
                    delete_idx.add(j)

        # Strategy 2: full-page illustration pages (cover / back cover / glossary)
        # have no **Figure X.Y** anchor. They are pure decoration, so any line
        # whose normalized text is in that page's geometric set is a label and is
        # deleted (author/title/publisher protected by COVER_KEEP).
        if fullpage_pages:
            fp_strings: set[str] = set()
            for p in fullpage_pages:
                fp_strings |= fig_by_page.get(p, set())
            for i, line in enumerate(raw):
                norm = _norm(line).lower()
                if norm in fp_strings and not COVER_KEEP.search(norm):
                    delete_idx.add(i)

        # Cover page exact-match fallback (defence in depth).
        if not all_removed:
            first_fig = next(
                (k for k, ln in enumerate(raw) if FIG_LINK_RE.search(ln)), len(raw)
            )
            for i in range(first_fig):
                norm = _norm(raw[i]).lower()
                if norm in COVER_FIGURE_TEXT and not COVER_KEEP.search(norm):
                    delete_idx.add(i)

        if not delete_idx:
            break  # Converged.

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
    # following line so the ``![Fig X.Y]`` link never sits directly adjacent to
    # the **Figure X.Y** caption.
    spaced: list[str] = []
    for idx, ln in enumerate(raw):
        spaced.append(ln)
        if FIG_LINK_RE.search(ln) and idx + 1 < len(raw) and raw[idx + 1].strip() != "":
            spaced.append("")
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
    if args.pdf:
        pdf_path = args.pdf
    else:
        cands = glob.glob(str(here / "*Sebastian Raschka*.pdf"))
        # Prefer the ENGLISH-filename PDF: that is the exact source P1 used to
        # generate llms-from-scratch.md, so its geometric figure-text set lines
        # up with the markdown. The CJK-filename copy exists in the workspace
        # too but its text layer differs and would mis-align the geometry match.
        eng = [c for c in cands if "从零开始" not in c]
        pdf_path = (eng or cands)[0] if (eng or cands) else None
    if not pdf_path or not Path(pdf_path).exists():
        print("[error] source PDF not found (pass --pdf)", file=sys.stderr)
        return 2

    print(f"[info] PDF : {pdf_path}")
    print(f"[info] MD  : {md_path}")
    fig_by_page, fullpage_pages = figure_text_by_page(pdf_path)
    print(f"[info] pages with figure-internal text: {len(fig_by_page)}")
    print(f"[info] full-page illustration pages: {sorted(p + 1 for p in fullpage_pages)}")

    cleaned, removed = clean_markdown(md_path, fig_by_page, fullpage_pages)
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
