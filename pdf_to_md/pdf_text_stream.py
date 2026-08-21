#!/usr/bin/env python3
"""
P1: PDF text-stream direct conversion (PyMuPDF), replacing pymupdf4llm.

Strategy (pdf-to-md-plan.md §1, §3.5):
- Extract text spans directly via PyMuPDF (get_text("dict")), block -> line -> span.
- Header/footer removal is done at the SPAN level: a span is a running header if
  its font is one of the book's known header fonts (HEADER_FONTS below) AND it
  sits at the top/bottom page margin AND its size is below the header threshold.
  This is robust against PyMuPDF merging a header span into the first body line
  of the same block (which broke block-level exact matching).
- Code blocks: a block whose spans are >=75% Courier -> fenced ``` code.
  Inline Courier fragments inside prose -> backtick `code`.
- Section headings: regex on the cleaned line text -> ### (final heading-level
  normalization deferred to P3).
- Pipeline order is fixed: header spans removed FIRST, then paragraph merge,
  then everything else (math/table/figure synthesis happens in P2/P3).

This script is the P1 text pipeline only. It does NOT do image synthesis (P2),
math/table cleanup (P2), or TOC anchors (P3).
"""

import re
import sys
from dataclasses import dataclass
from pathlib import Path

import fitz  # PyMuPDF

# Reuse the figure-internal text geometry detector (sub-module, mirrors P0).
from figure_text_detect import page_element_regions, is_figure_text  # noqa: E402


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
# Running-header font signals validated in P0 (extract_images). Pages carry
# small running headers/footers set in these fonts at the top/bottom margins.
HEADER_FONTS = {
    "section_title": ["NewBaskerville-BoldItali"],   # "1.4 Introducing..."
    "page_number":   ["NewBaskerville-Bold"],         # "9", "ix", "xii"
    "chapter_label": ["FranklinGothic-Demi"],         # "CHAPTER 1", "CONTENTS"
}


def font_matches(fonts: list[str], patterns: list[str]) -> bool:
    return any(p in f for f in fonts for p in patterns)


@dataclass
class StreamConfig:
    # A block is a code block if >= this fraction of its spans are Courier.
    courier_code_threshold: float = 0.75
    # Header/footer detection thresholds.
    top_margin_pt: float = 35.0
    bottom_margin_pt: float = 75.0
    max_line_len: int = 80
    header_font_size_max: float = 10.5
    # Inline-code backtick wrapping for Courier fragments inside prose blocks.
    inline_code: bool = True
    # Paragraph merge: join physical lines within a prose block into one
    # paragraph (separated by spaces), separated by blank lines between blocks.
    merge_paragraphs: bool = True
    # Drop text blocks that were extracted from INSIDE figures (vector labels,
    # callout-box text, axis labels). Uses geometry from figure_text_detect.
    drop_figure_text: bool = True


HEADER_FONT_PATTERNS = (
    HEADER_FONTS["section_title"]
    + HEADER_FONTS["page_number"]
    + HEADER_FONTS["chapter_label"]
)

# A short pure number / roman numeral sitting at the page margin is a page number.
PAGE_NUMBER_RE = re.compile(r"^[\divxlcmIVXLCDM]+$")


def is_header_line(line: dict, cfg: StreamConfig, page_height: float) -> bool:
    """Whole-line header/footer test (used for page-number prefix lines that
    escape the font-based span test, e.g. NewBaskerville-Bold page numbers)."""
    spans = line["spans"]
    if not spans:
        return False
    y0, y1 = line["bbox"][1], line["bbox"][3]
    at_top = y0 < cfg.top_margin_pt
    at_bottom = y1 > page_height - cfg.bottom_margin_pt
    if not (at_top or at_bottom):
        return False
    txt = "".join(s["text"] for s in spans).strip()
    if PAGE_NUMBER_RE.match(txt) and len(txt) <= 5:
        return True
    return False


def is_header_span(span: dict, page_height: float, cfg: StreamConfig) -> bool:
    """A span is a running header/footer if it uses a known header font, sits at
    the page margin, and is small. Mirrors P0 structural detection."""
    font = span["font"]
    if not font_matches([font], HEADER_FONT_PATTERNS):
        return False
    if span["size"] >= cfg.header_font_size_max:
        return False
    y0, y1 = span["bbox"][1], span["bbox"][3]
    at_top = y0 < cfg.top_margin_pt
    at_bottom = y1 > page_height - cfg.bottom_margin_pt
    return at_top or at_bottom


# ---------------------------------------------------------------------------
# Code-block language heuristics
# ---------------------------------------------------------------------------
PY_HINT_RE = re.compile(
    r"(^\s*>>>|^\s*\.\.\.|def \w+\(|class \w+\(|import \w|from \w+ import|"
    r"print\(|>>> |\.cuda\(\)|torch\.\w+|np\.\w+|self\.\w+\s*=)",
)


def detect_code_lang(block_text: str) -> str:
    if PY_HINT_RE.search(block_text):
        return "python"
    return "text"


# ---------------------------------------------------------------------------
# Heading detection (cleaned line, after header removal)
# ---------------------------------------------------------------------------
# A section heading is "N.N <Title Word...>" where the title word starts with
# an uppercase letter (real headings like "1.1 What is an LLM?"). This excludes
# numeric matrices such as "0.4 0.6 0.5" that happen to start with N.N.
SECTION_RE = re.compile(r"^\d+\.\d+\s+[A-Z][a-zA-Z]")
CHAPTER_RE = re.compile(r"^(Chapter|Appendix)\s+\d+", re.I)
PART_RE = re.compile(r"^Part\s+[IVX]+", re.I)


def classify_heading(text: str) -> str | None:
    if CHAPTER_RE.match(text) or PART_RE.match(text):
        return "## "
    if SECTION_RE.match(text):
        return "### "
    return None


# ---------------------------------------------------------------------------
# Block classification helpers
# ---------------------------------------------------------------------------
def block_spans(block: dict):
    for line in block["lines"]:
        for span in line["spans"]:
            yield span


def block_courier_frac(block: dict, cfg: StreamConfig, page_height: float) -> float:
    """Fraction of NON-header spans that are Courier."""
    tot = 0
    cou = 0
    for s in block_spans(block):
        if is_header_span(s, page_height, cfg):
            continue
        tot += 1
        if "Courier" in s["font"]:
            cou += 1
    return (cou / tot) if tot else 0.0


def cleaned_line_text(line: dict, cfg: StreamConfig, page_height: float) -> str:
    """Rebuild a line from non-header spans, no inline-code wrapping yet."""
    parts = []
    for s in line["spans"]:
        if is_header_span(s, page_height, cfg):
            continue
        parts.append(s["text"])
    return "".join(parts).strip()


def inline_code_wrap_line(line: dict, cfg: StreamConfig, page_height: float) -> str:
    out = []
    buf = []
    in_code = False
    for s in line["spans"]:
        if is_header_span(s, page_height, cfg):
            continue
        is_courier = "Courier" in s["font"]
        if is_courier and not in_code:
            in_code = True
            buf = [s["text"]]
        elif is_courier and in_code:
            buf.append(s["text"])
        elif not is_courier and in_code:
            in_code = False
            chunk = "".join(buf).strip()
            if chunk:
                out.append("`" + chunk + "`")
            out.append(s["text"])
        else:
            out.append(s["text"])
    if in_code:
        chunk = "".join(buf).strip()
        if chunk:
            out.append("`" + chunk + "`")
    return "".join(out).strip()


# ---------------------------------------------------------------------------
# Main conversion
# ---------------------------------------------------------------------------
def pdf_to_text_stream(
    pdf_path: str, cfg: StreamConfig | None = None, page_filter=None
) -> str:
    cfg = cfg or StreamConfig()
    doc = fitz.open(pdf_path)
    page_heights = {p: doc[p].rect.height for p in range(len(doc))}

    out: list[str] = []
    prev_was_code = False

    for pno in range(len(doc)):
        if page_filter is not None and not page_filter(pno):
            continue
        page = doc[pno]
        page_height = page_heights[pno]
        d = page.get_text("dict")
        blocks = d.get("blocks", [])
        blocks_sorted = sorted(
            blocks, key=lambda b: (round(b["bbox"][1], 1), round(b["bbox"][0], 1))
        )

        # Pre-compute figure geometry for this page (reuse P0 sub-module).
        fig_rects, fig_union = (None, None)
        if cfg.drop_figure_text:
            fig_rects, fig_union = page_element_regions(page)

        for block in blocks_sorted:
            if "lines" not in block:
                continue  # image / drawing only

            # Drop blocks that are entirely header/footer spans.
            non_header_lines = [
                ln for ln in block["lines"]
                if any(not is_header_span(s, page_height, cfg) for s in ln["spans"])
                and not is_header_line(ln, cfg, page_height)
            ]
            if not non_header_lines:
                continue

            # Drop text extracted from INSIDE figures (vector labels, callout
            # text, axis labels) so it doesn't duplicate the rendered PNG.
            if cfg.drop_figure_text and is_figure_text(
                block, fig_rects, fig_union, page_height
            ):
                continue

            courier_frac = block_courier_frac(block, cfg, page_height)

            # ----- Code block -----
            if courier_frac >= cfg.courier_code_threshold:
                lang = detect_code_lang(
                    " ".join(
                        cleaned_line_text(ln, cfg, page_height)
                        for ln in non_header_lines
                    )
                )
                code_lines = []
                for ln in non_header_lines:
                    txt = "".join(s["text"] for s in ln["spans"] if not is_header_span(s, page_height, cfg))
                    code_lines.append(txt.rstrip())
                code = "\n".join(code_lines).rstrip()
                if prev_was_code:
                    # merge adjacent code blocks of the same language (P2 also
                    # merges same-language fences; avoid double blank gaps)
                    out.append(code)
                    out.append("")
                else:
                    out.append(f"```{lang}")
                    out.append(code)
                    out.append("```")
                    out.append("")
                prev_was_code = True
                continue

            prev_was_code = False

            # ----- Prose / heading block -----
            line_texts = []
            for ln in non_header_lines:
                txt = cleaned_line_text(ln, cfg, page_height)
                if not txt:
                    continue
                if cfg.inline_code:
                    txt = inline_code_wrap_line(ln, cfg, page_height)
                line_texts.append(txt)

            if not line_texts:
                continue

            # Cross-line section heading stitching: a line that is exactly the
            # "N.N" section number followed by a title line (split across two
            # physical lines in the PDF) is merged into one heading.
            stitched = []
            i = 0
            while i < len(line_texts):
                cur = line_texts[i]
                m = re.match(r"^(\d+\.\d+)\s*$", cur)
                nxt = line_texts[i + 1] if i + 1 < len(line_texts) else ""
                if m and nxt and re.match(r"^[A-Z][a-zA-Z]", nxt):
                    stitched.append(m.group(1) + " " + nxt)
                    i += 2
                else:
                    stitched.append(cur)
                    i += 1
            line_texts = stitched

            # Heading: a single short line matching the heading regex.
            if len(line_texts) == 1:
                h = classify_heading(line_texts[0])
                if h and len(line_texts[0]) < 80:
                    out.append(h + line_texts[0])
                    out.append("")
                    continue

            if cfg.merge_paragraphs:
                para = " ".join(line_texts)
                para = re.sub(r"\s+", " ", para).strip()
                out.append(para)
                out.append("")
            else:
                out.extend(line_texts)
                out.append("")

    doc.close()
    return "\n".join(out)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="P1: PDF -> text-stream Markdown")
    parser.add_argument("pdf", help="Input PDF")
    parser.add_argument("output", help="Output Markdown")
    parser.add_argument("--pages", help="Optional page range for testing, e.g. 24-30")
    parser.add_argument("--no-merge", action="store_true", help="Disable paragraph merge")
    args = parser.parse_args()

    cfg = StreamConfig()
    if args.no_merge:
        cfg.merge_paragraphs = False

    if args.pages:
        m = re.match(r"(\d+)-(\d+)", args.pages)
        if not m:
            raise SystemExit("--pages must be like 24-30")
        lo, hi = int(m.group(1)), int(m.group(2))
        pf = lambda pno: (lo - 1) <= pno <= (hi - 1)  # noqa: E731
        text = pdf_to_text_stream(args.pdf, cfg, page_filter=pf)
    else:
        text = pdf_to_text_stream(args.pdf, cfg)

    Path(args.output).write_text(text, encoding="utf-8")
    print(f"Written {len(text.splitlines())} lines -> {args.output}")
