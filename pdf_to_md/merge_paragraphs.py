#!/usr/bin/env python3
"""merge_paragraphs.py - Fix paragraph splitting anomalies in PDF-to-MD output.

Root cause
----------
The P1 pipeline (pdf_text_stream.py) inserts a blank line between every PDF
text block.  When a logical paragraph spans multiple physical blocks (e.g.
because a blockquote is a separate block, or a long paragraph wraps across
blocks), the result is a false paragraph break.

Two anomaly types are fixed:

A) Blockquote continuation: a ``> text`` line followed by a blank line and
   then a continuation line that should be part of the blockquote.
   Fix: merge the continuation into the blockquote (add ``> `` prefix).

B) Prose paragraph split: a long prose line (>60 chars) ending without
   terminal punctuation, followed by a blank line and a continuation
   starting with lowercase.
   Fix: merge the two lines with a space, removing the blank line.

Usage
-----
    python merge_paragraphs.py              # dry-run preview
    python merge_paragraphs.py --apply       # apply and overwrite
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


# Terminal punctuation that ends a sentence/paragraph.
TERMINAL_PUNCT = set(".?!:。？！：")


def _ends_terminal(text: str) -> bool:
    """Return True if *text* ends with terminal punctuation or a closing
    bracket/quote that typically follows terminal punctuation."""
    t = text.rstrip()
    if not t:
        return True
    # Walk backwards past closing brackets/quotes
    i = len(t) - 1
    while i >= 0 and t[i] in ")]}\"'":
        i -= 1
    if i < 0:
        return True
    return t[i] in TERMINAL_PUNCT


def _is_special(line: str) -> bool:
    """Return True if the line is a structural element that should not be
    merged (heading, list item, figure link, table, anchor, code fence)."""
    s = line.strip()
    if not s:
        return False
    return s.startswith(("#", ">", "-", "*", "|", "!", "<a", "```"))


def _is_bq_contin(line: str) -> bool:
    """Return True if *line* can be a blockquote continuation.

    Accepts:
    - Regular prose (not starting with special markers)
    - Another ``>`` line (multi-line blockquote)
    - A line starting with ``-`` that is clearly a URL continuation
      (e.g. ``-scratch`` after ``LLMs-from``)
    """
    s = line.strip()
    if not s:
        return False
    # Another blockquote line
    if s.startswith("> "):
        return True
    # URL continuation: starts with - but NOT a markdown list item
    # (list items have "- " with a space, URL fragments like "-scratch" don't)
    if s.startswith("-") and len(s) > 1 and s[1] != " ":
        return True
    # Regular prose (not special)
    if not s.startswith(("#", "- ", "* ", "|", "!", "<a", "```")):
        return True
    return False


def _extract_bq_text(line: str) -> str:
    """Extract the text content from a blockquote or continuation line."""
    s = line.strip()
    if s.startswith("> "):
        return s[2:]
    return s


def fix_blockquote_continuations(lines: list[str]) -> tuple[list[str], int]:
    """Merge blockquote lines with their continuation text.

    Handles:
    - ``> text`` + blank + ``continuation`` → ``> text continuation``
    - ``> text`` + blank + ``> more text`` → ``> text more text``
    - ``> text`` + blank + ``-url_fragment`` → ``> text-url_fragment``
    - Multi-line: iterates until no more merges are found.
    """
    total_fixes = 0
    while True:
        result: list[str] = []
        fixes = 0
        i = 0
        while i < len(lines):
            line = lines[i]
            s = line.strip()

            # Check for blockquote continuation pattern
            if s.startswith("> ") and not _ends_terminal(s[2:]):
                # Next line blank?
                if i + 1 < len(lines) and lines[i + 1].strip() == "":
                    # Line after blank is a continuation?
                    if i + 2 < len(lines) and _is_bq_contin(lines[i + 2]):
                        bq_text = s[2:]  # text after "> "
                        nxt_text = _extract_bq_text(lines[i + 2])
                        # For URL fragments starting with -, join without space
                        if nxt_text.startswith("-") and len(nxt_text) > 1 and nxt_text[1] != " ":
                            merged = f"> {bq_text}{nxt_text}"
                        else:
                            merged = f"> {bq_text} {nxt_text}"
                        result.append(merged)
                        # Skip blank line and continuation
                        i += 3
                        fixes += 1
                        continue

            result.append(line)
            i += 1

        total_fixes += fixes
        lines = result
        if fixes == 0:
            break

    return lines, total_fixes


def fix_prose_splits(lines: list[str]) -> tuple[list[str], int]:
    """Merge prose paragraphs that were incorrectly split across blocks.

    Pattern::

        Long prose line ending without terminal punct (>60 chars)
        (blank)
        continuation starting with lowercase

    Fix: merge with a space.  Iterates until convergence.
    """
    total_fixes = 0
    while True:
        result: list[str] = []
        fixes = 0
        i = 0
        while i < len(lines):
            line = lines[i]
            s = line.strip()

            # Skip special lines, short lines, blank lines
            if not s or _is_special(line) or len(s) < 60:
                result.append(line)
                i += 1
                continue

            # Check if line ends without terminal punctuation
            if _ends_terminal(s):
                result.append(line)
                i += 1
                continue

            # Next line blank?
            if i + 1 >= len(lines) or lines[i + 1].strip() != "":
                result.append(line)
                i += 1
                continue

            # Line after blank starts with lowercase and is not special?
            if i + 2 >= len(lines):
                result.append(line)
                i += 1
                continue

            nxt = lines[i + 2].strip()
            if not nxt or not nxt[0].islower() or _is_special(lines[i + 2]):
                result.append(line)
                i += 1
                continue

            # Also check: next line should be substantial (not a tiny fragment)
            if len(nxt) < 10:
                result.append(line)
                i += 1
                continue

            # Merge
            merged = s + " " + nxt
            result.append(merged)
            i += 3  # skip blank line and continuation
            fixes += 1

        total_fixes += fixes
        lines = result
        if fixes == 0:
            break

    return lines, total_fixes


def merge_paragraphs(md_path: Path, apply: bool = False) -> int:
    lines = md_path.read_text(encoding="utf-8").split("\n")
    original_count = len(lines)

    # Pass 1: blockquote continuations
    lines, bq_fixes = fix_blockquote_continuations(lines)

    # Pass 2: prose paragraph splits
    lines, prose_fixes = fix_prose_splits(lines)

    total = bq_fixes + prose_fixes
    final_count = len(lines)

    print(f"[info] Blockquote continuations merged: {bq_fixes}")
    print(f"[info] Prose paragraph splits merged:   {prose_fixes}")
    print(f"[info] Total fixes: {total}")
    print(f"[info] Lines: {original_count} → {final_count} "
          f"(removed {original_count - final_count} blank lines)")

    if apply and total > 0:
        md_path.write_text("\n".join(lines), encoding="utf-8")
        print(f"[done] Applied to {md_path}")
    elif not apply:
        print("--- DRY RUN (use --apply to write) ---")

    return total


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    here = Path(__file__).resolve().parent
    ap.add_argument("--md", default=str(here / "llms-from-scratch.md"))
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args(argv)

    md_path = Path(args.md)
    if not md_path.exists():
        print(f"[error] not found: {md_path}", file=sys.stderr)
        return 2

    n = merge_paragraphs(md_path, apply=args.apply)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
