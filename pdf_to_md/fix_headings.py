#!/usr/bin/env python3
"""fix_headings.py - Add missing Markdown heading prefixes (#) by cross-referencing
the PDF's built-in Table of Contents.

Background
----------
The PDF-to-MD pipeline (pdf_text_stream.py) only recognises headings that match
``Chapter N`` / ``N.N Title`` patterns.  Unnumbered subsection headings (e.g.
"Who should read this book", "Summary") and sub-subsection headings (e.g.
"3.3.1 A simple self-attention mechanism...") are emitted as plain text lines.

Approach
--------
1. Extract the PDF's built-in TOC (193 entries) as ground truth.
2. For each TOC entry, search the Markdown for the heading text.
3. If the text is found as a standalone line WITHOUT a ``#`` prefix, add the
   appropriate prefix (PDF L1→``##``, L2→``###``, L3→``####``).
4. Skip entries that are already headings, are structural (brief contents,
   index), or whose text appears only inside body paragraphs.

Usage
-----
    python fix_headings.py                        # dry-run preview
    python fix_headings.py --apply                # apply and overwrite
    python fix_headings.py --pdf path/to/book.pdf --md path/to/file.md
"""

from __future__ import annotations

import argparse
import re
import sys
import unicodedata
from pathlib import Path

import pymupdf


# --------------------------------------------------------------------------- #
# Configuration                                                                #
# --------------------------------------------------------------------------- #
# PDF TOC entries to skip (structural, not content headings).
SKIP_TITLES = {
    "brief contents", "contents", "index",
    "build a large language model (from scratch)",
}

# Map PDF TOC level → Markdown heading prefix.
LEVEL_TO_PREFIX = {1: "## ", 2: "### ", 3: "#### "}


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #
def norm(s: str) -> str:
    """NFKC-normalise and lowercase for robust comparison."""
    return unicodedata.normalize("NFKC", s).strip().lower()


def heading_level_from_toc(toc_level: int) -> int:
    """PDF TOC level 1 → MD ## (2), level 2 → ### (3), level 3 → #### (4)."""
    return toc_level + 1


def find_all_heading_candidates(lines: list[str], title: str) -> list[tuple[int, bool]]:
    """Return all 0-based line indices where *title* appears as a standalone
    non-heading line, plus a flag indicating merged heading+body text.

    The match is NFKC-normalised and case-insensitive.  A line qualifies when:
    * Not blank, not already a heading, not inside a code fence.
    * Not a figure link, blockquote, or list item.
    * Title length > 2 (rejects index letter separators).
    * **Exact match**: line text == title, AND the **next** line is blank
      (proves it is a standalone heading, not a mid-paragraph reference).
    * **Merged match**: line starts with title text followed by body text
      (e.g. "E.2 Preparing the dataset Before applying LoRA...").
    """
    title_n = norm(title)
    if len(title_n) <= 2:
        return []

    results: list[tuple[int, bool]] = []
    in_fence = False

    for i, line in enumerate(lines):
        s = line.strip()
        if s.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if not s:
            continue
        if s.startswith("#"):
            continue
        if s.startswith(("!", ">", "-", "*", "|", "<a ")):
            continue

        line_n = norm(s)

        # --- Exact match ---
        if line_n == title_n:
            # A genuine heading must have a blank line AFTER it (or be at
            # end of file).  Cross-references in running prose are followed
            # by more body text.
            next_blank = (i + 1 >= len(lines) or lines[i + 1].strip() == "")
            if next_blank:
                results.append((i, False))
            continue

        # --- Merged match (heading text + body text on same line) ---
        if line_n.startswith(title_n) and len(line_n) > len(title_n) + 5:
            remainder = line_n[len(title_n):]
            if remainder and remainder[0].isalpha():
                results.append((i, True))

    return results


def _is_cross_reference(lines: list[str], idx: int, text: str) -> bool:
    """Deprecated – kept for reference but no longer used."""
    return False


# --------------------------------------------------------------------------- #
# Main logic                                                                   #
# --------------------------------------------------------------------------- #
def fix_headings(pdf_path: str, md_path: Path, apply: bool = False) -> int:
    # 1. Extract PDF TOC.
    doc = pymupdf.open(pdf_path)
    toc = doc.get_toc()
    doc.close()

    # 2. Read Markdown.
    lines = md_path.read_text(encoding="utf-8").split("\n")

    # 3. Deduplicate TOC entries (e.g. "Summary" appears 8 times,
    #    "Appendix A" appears twice).
    seen_titles: dict[str, int] = {}  # norm_title → toc_level
    unique_entries: list[tuple[int, str]] = []
    for toc_level, title, page in toc:
        tn = norm(title.strip())
        if tn in SKIP_TITLES:
            continue
        if tn in seen_titles:
            continue  # skip duplicate TOC entries
        seen_titles[tn] = toc_level
        unique_entries.append((toc_level, title.strip()))

    # 4. For each unique TOC entry, find ALL matching lines in the MD.
    # fixes: (line_idx, old_line, new_line)
    fixes: list[tuple[int, str, str]] = []
    used_lines: set[int] = set()  # line indices already claimed

    for toc_level, title in unique_entries:
        md_level = heading_level_from_toc(toc_level)
        prefix = "#" * md_level + " "

        candidates = find_all_heading_candidates(lines, title)
        for idx, is_merged in candidates:
            if idx in used_lines:
                continue

            old_line = lines[idx]

            if is_merged:
                # Split merged heading + body text.
                split_pos = len(title)
                heading_part = old_line[:split_pos].strip()
                body_part = old_line[split_pos:].strip()
                new_line = f"{prefix}{heading_part}\n\n{body_part}"
            else:
                new_line = prefix + old_line.strip()

            fixes.append((idx, old_line, new_line))
            used_lines.add(idx)

    if not fixes:
        print("[info] No missing headings found.")
        return 0

    # Report findings.
    print(f"[info] Found {len(fixes)} missing headings:\n")
    for idx, old, new in sorted(fixes):
        tag = " [MERGED]" if "\n" in new else ""
        print(f"  L{idx + 1}{tag}: {old.strip()!r}")
        print(f"       → {new!r}\n")

    if apply:
        for idx, old, new in fixes:
            lines[idx] = new
        md_path.write_text("\n".join(lines), encoding="utf-8")
        print(f"[done] Applied {len(fixes)} heading fixes to {md_path}")
    else:
        print("--- DRY RUN (use --apply to write) ---")

    return len(fixes)


# --------------------------------------------------------------------------- #
# CLI                                                                          #
# --------------------------------------------------------------------------- #
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    here = Path(__file__).resolve().parent
    ap.add_argument("--md", default=str(here / "llms-from-scratch.md"))
    ap.add_argument("--pdf", default=None)
    ap.add_argument("--apply", action="store_true",
                    help="Apply fixes (default is dry-run preview).")
    args = ap.parse_args(argv)

    md_path = Path(args.md)
    if not md_path.exists():
        print(f"[error] markdown not found: {md_path}", file=sys.stderr)
        return 2

    import glob as _glob
    pdf_path = args.pdf or _glob.glob(str(here / "*Sebastian Raschka*.pdf"))
    if isinstance(pdf_path, list):
        pdf_path = pdf_path[0] if pdf_path else None
    if not pdf_path or not Path(pdf_path).exists():
        print("[error] source PDF not found (pass --pdf)", file=sys.stderr)
        return 2

    print(f"[info] PDF : {pdf_path}")
    print(f"[info] MD  : {md_path}")

    n = fix_headings(pdf_path, md_path, apply=args.apply)
    print(f"\n[info] Total fixes: {n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
