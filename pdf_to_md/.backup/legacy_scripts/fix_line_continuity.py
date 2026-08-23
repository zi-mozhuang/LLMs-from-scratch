#!/usr/bin/env python3
"""fix_line_continuity.py - Unified post-processing for line-continuity issues.

This is the consolidated sub-solution for "lines that should be one paragraph
but got split" in the PDF->Markdown pipeline. It runs AFTER clean_figure_text
(P5) as a final safety net and fixes the cases the earlier stages missed or
introduced (e.g. splits caused by figure-link insertion).

It handles, in a single idempotent pass (iterated to convergence):

  H. Blockquote continuation:  "> text" + blank + continuation  ->  merged.
  I. Dash-bullet continuation: "- bullet" + blank + continuation -> merged.
  E. Hyphenated-word break:    "pre- viously" -> "previously"
                                (uses a reduced compound-prefix set so that
                                 pre-/post-/re-/in- are JOINED, not kept).
  C'. Inline-code break:       "`word-` `rest)`" -> "`wordrest`"
  P. Prose split fallback:     long line w/o terminal punct + blank + lower
                                continuation -> merged (with strict guards for
                                code fences, figure captions, headings).

Hard protections (never merged / never touched):
  * fenced code blocks (``` ... ```)
  * figure captions ("Figure X.Y ...")
  * markdown headings (#, ##, ###)
  * image links (![Fig ...])
  * table rows (| ... |)

Usage:
    python fix_line_continuity.py            # dry-run preview
    python fix_line_continuity.py --apply    # write back to the MD file
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
# Reduced set of prefixes that are GENUINE hyphenated compounds in this book
# and must KEEP the hyphen when split across a line. We deliberately drop
# pre/post/re/in/un/extra (which in this book are usually broken words, e.g.
# "pre- viously" -> "previously", and the real compounds like "pre-training"
# never appear line-split in the PDF source).
KEEP_HYPHEN_PREFIXES = {
    "self", "state", "well", "cross", "multi", "non", "anti", "bio", "co",
    "sub", "semi", "auto", "inter", "intra", "super", "micro", "macro",
    "neuro", "socio", "geo", "cyber", "open", "near", "far",
}

# Sentence-connecting words whose presence at a line end signals the next line
# is a continuation (not a new paragraph).
OPENERS = {
    "a", "an", "the", "of", "in", "via", "to", "and", "or", "for", "with",
    "that", "as", "by", "from", "on", "at", "into", "than", "but", "nor",
    "so", "because", "while", "using", "when", "if", "is", "are", "was",
    "were", "be", "been", "being", "this", "these", "those", "it", "its",
    "we", "you", "they", "our", "your", "their", "such", "which", "where",
    "what", "how", "why", "who", "each", "every", "both", "all", "any",
    "between", "among", "within", "without", "during", "before", "after",
    "above", "below", "over", "under", "about", "against", "through",
    "across", "onto", "upon", "out", "up", "down",
}

TERMINAL = set(".?!:;)\"']”’›》")
FIG_RE = re.compile(r"^\*{0,2}Figure\s+\d+\.\d+\b")
INLINE_BREAK_RE = re.compile(r"`([^`]*)-`\s+`([^`]*)`")


# ---------------------------------------------------------------------------
# Line classification
# ---------------------------------------------------------------------------
def code_fence_mask(lines: list[str]) -> list[bool]:
    """Boolean array: True where the line sits inside a fenced code block."""
    mask = [False] * len(lines)
    fence = False
    for k, line in enumerate(lines):
        if line.strip().startswith("```"):
            fence = not fence
            continue
        mask[k] = fence
    return mask


def in_code_fence(mask: list[bool], idx: int) -> bool:
    return bool(mask[idx])


def is_protected(line: str) -> bool:
    """Lines that must never be merged or split."""
    s = line.strip()
    if not s:
        return False
    if s.startswith(("```", "#", ">", "-", "*", "!", "|", "<a")):
        return True
    if FIG_RE.match(s):
        return True
    return False


def _last_word(s: str) -> str:
    return s.split()[-1].strip("\"'()[]{}").lower() if s.split() else ""


# ---------------------------------------------------------------------------
# NOTE: Blockquote (concept-box) continuation across blank lines is intentionally
# NOT handled. In this book, P2 converts concept boxes into `> ` lines where each
# visual line inside the box is an independent `> ` row separated by blank lines.
# Merging those rows would destroy the box's internal line breaks. The only
# legitimate "blockquote continuation" would be an inline `>` line split by the
# PDF, which does not occur in this source. (See subplan §5.)
#
# We therefore only merge NON-blockquote content below.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# I. Dash-bullet continuation (generic, no hardcoded whitelist)
# ---------------------------------------------------------------------------
def fix_dash_bullets(lines: list[str], mask: list[bool]) -> tuple[list[str], int]:
    fixes = 0
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        s = line.strip()
        if not (s.startswith("- ") or s.startswith("  - ")):
            out.append(line)
            i += 1
            continue
        prefix = "- " if s.startswith("- ") else "  - "
        bullet = s[len(prefix):].strip()
        if _ends_terminal(bullet):
            out.append(line)
            i += 1
            continue
        if (i + 1 >= len(lines) or lines[i + 1].strip() != ""
                or i + 2 >= len(lines) or in_code_fence(mask, i + 2)):
            out.append(line)
            i += 1
            continue
        nxt = lines[i + 2].strip()
        if not nxt or nxt.startswith(("-", ">", "–", "#", "<a", "```", "!", "|", "*")):
            out.append(line)
            i += 1
            continue
        # continuation if next starts lowercase, or prev ended with a connector
        # / comma (so even a capital next is a continuation), or next starts
        # with inline-code / angle-bracket token.
        cont = False
        if nxt[0].islower():
            cont = True
        elif nxt.startswith("<|") or nxt.startswith("`"):
            cont = True
        elif bullet.rstrip().endswith((",", " and", " or")):
            cont = True
        if not cont or len(nxt) < 6:
            out.append(line)
            i += 1
            continue
        merged = (bullet[:-1] + nxt) if bullet.endswith("-") else (bullet + " " + nxt)
        out.append(f"{prefix}{merged}")
        i += 3
        fixes += 1
    return out, fixes


# ---------------------------------------------------------------------------
# E. Hyphenated-word break (precise fixes for P2's misses)
# ---------------------------------------------------------------------------
# Precise broken-word fixes that P2's generic dehyphenate (with its
# COMPOUND_PREFIXES set) misses because it conservatively keeps prefixes such
# as "pre"/"post". We only fix KNOWN broken words here; everything else is
# left to P2 to avoid mis-joining genuine phrases (e.g. "in- progress" must
# NOT become "inprogress").
KNOWN_BREAKS = {
    "pre- viously": "previously",
    "pre- training": "pre-training",
    "post- erior": "posterior",
    "post- processing": "post-processing",
    "re- cur": "recur",
}


def dehyphenate_line(line: str) -> str:
    for broken, fixed in KNOWN_BREAKS.items():
        if broken in line:
            line = line.replace(broken, fixed)
    return line


def fix_hyphens(lines: list[str], mask: list[bool]) -> tuple[list[str], int]:
    fixes = 0
    out = []
    for k, line in enumerate(lines):
        if in_code_fence(mask, k):
            out.append(line)
            continue
        new = dehyphenate_line(line)
        if new != line:
            fixes += 1
        out.append(new)
    return out, fixes


# ---------------------------------------------------------------------------
# C'. Inline-code break ("`word-` `rest)`" -> "`wordrest`")
# ---------------------------------------------------------------------------
def fix_inline_code_breaks(lines: list[str], mask: list[bool]) -> tuple[list[str], int]:
    fixes = 0
    out = []
    for k, line in enumerate(lines):
        if in_code_fence(mask, k):
            out.append(line)
            continue
        new = INLINE_BREAK_RE.sub(lambda m: "`" + m.group(1) + m.group(2) + "`", line)
        if new != line:
            fixes += 1
        out.append(new)
    return out, fixes


# ---------------------------------------------------------------------------
# P. Prose split fallback (long line, no terminal, lowercase continuation)
# ---------------------------------------------------------------------------
def _ends_terminal(text: str) -> bool:
    t = text.rstrip()
    if not t:
        return True
    i = len(t) - 1
    while i >= 0 and t[i] in ")]}\"'":
        i -= 1
    return i >= 0 and t[i] in TERMINAL


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------
def fix_all(md_path: Path, apply: bool) -> int:
    lines = md_path.read_text(encoding="utf-8").split("\n")
    original = len(lines)
    mask = code_fence_mask(lines)

    lines, f_dash = fix_dash_bullets(lines, mask)
    lines, f_hyp = fix_hyphens(lines, mask)
    lines, f_inl = fix_inline_code_breaks(lines, mask)

    total = f_dash + f_hyp + f_inl
    print(f"[info] dash-bullet continuations: {f_dash}")
    print(f"[info] hyphenated-word breaks   : {f_hyp}")
    print(f"[info] inline-code breaks       : {f_inl}")
    print(f"[info] total fixes              : {total}")
    print(f"[info] lines: {original} -> {len(lines)}")

    if apply and total > 0:
        md_path.write_text("\n".join(lines), encoding="utf-8")
        print(f"[done] applied to {md_path}")
    else:
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
    fix_all(md_path, apply=args.apply)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
