#!/usr/bin/env python3
"""
P3: TOC generation + anchor injection + global validation.

Runs AFTER all P1/P2 cleaning. Steps:
  1. Scan real headings (outside fenced code blocks).
  2. Assign unique HTML anchors <a id="slug"></a> to each heading; duplicate
     heading text gets a numeric suffix so every anchor is unique.
  3. Insert a book-level title (# ...) and a clickable Table of Contents at the
     top, linking to every anchor (TOC links subset-of anchors invariant).
  4. Run the §3.3 validation assertions:
       - fences paired
       - no PUA
       - TOC links ⊆ anchors

Input : text_stream_p2.md
Output: llms-from-scratch.md  (final)
"""

import re
import sys
from pathlib import Path

FENCE_RE = re.compile(r"^\s*```")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
CHAPTER_RE = re.compile(r"^Chapter\s+(\d+)", re.I)
APPENDIX_RE = re.compile(r"^appendix\s+([A-Z])", re.I)
SECTION_RE = re.compile(r"^(\d+)\.(\d+)\b")


def slugify(text: str) -> str:
    s = text.strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-") or "section"


def scan_headings(lines: list[str]):
    """Return list of (index, level, text) for headings outside code fences."""
    heads = []
    in_fence = False
    for i, line in enumerate(lines):
        if FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        m = HEADING_RE.match(line)
        if m:
            heads.append((i, len(m.group(1)), m.group(2)))
    return heads


def assign_anchors(heads):
    """Map each heading index -> unique slug. Duplicate slugs get -2, -3..."""
    used = {}
    anchors = {}
    for idx, level, text in heads:
        base = slugify(text)
        if base in used:
            used[base] += 1
            slug = f"{base}-{used[base]}"
        else:
            used[base] = 1
            slug = base
        anchors[idx] = slug
    return anchors


def heading_sort_key(idx, level, text, order):
    """Logical order: Chapter N (##) first, then sections N.M (###) grouped
    under their chapter, sorted numerically. Headings without a chapter context
    (e.g. a stray 'N.M' mention in the front matter) sort by their N.M number
    under a synthetic chapter."""
    ch = CHAPTER_RE.match(text)
    if ch:
        return (int(ch.group(1)), 0, order)
    sec = SECTION_RE.match(text)
    if sec:
        return (int(sec.group(1)), int(sec.group(2)), order)
    # non-numbered heading (e.g. Preface/Contents) -> front, by occurrence
    return (0, 0, order)


def is_structural(text: str) -> bool:
    """A heading belongs in the TOC only if it is a real book-structure title:
    a Chapter/Appendix, or a numbered section 'N.M ...'. Dataset sample labels
    like '### Instruction:' are content formatting, not structure."""
    return bool(CHAPTER_RE.match(text) or APPENDIX_RE.match(text) or SECTION_RE.match(text))


def build_toc(heads, anchors):
    """Build a markdown TOC block (list of links) under a book title, sorted in
    logical reading order (Chapter -> sections). Only structural headings are
    listed; anchors are still injected for every heading (TOC ⊆ anchors).
    Duplicate chapter titles (e.g. the exercise chapters in the appendix) get a
    '(Appendix)' suffix in the TOC display only -- the anchor and body text are
    unchanged."""
    toc = ["# Build a Large Language Model (From Scratch)", ""]
    toc.append("## Table of Contents")
    toc.append("")
    structural = [h for h in heads if is_structural(h[2])]
    ordered = sorted(
        structural,
        key=lambda h: heading_sort_key(h[0], h[1], h[2], heads.index(h)),
    )
    seen_text: dict[str, int] = {}
    for idx, level, text in ordered:
        seen_text[text] = seen_text.get(text, 0) + 1
        display = text + " (Appendix)" if seen_text[text] > 1 else text
        slug = anchors[idx]
        indent = "  " * max(0, level - 2)  # ## -> top, ### -> indented
        link = f"{indent}- [{display}](#{slug})"
        toc.append(link)
    toc.append("")
    return toc


def inject_anchors_and_toc(lines: list[str], heads, anchors, toc):
    """Insert <a id> right before each heading line, then prepend TOC."""
    out = []
    head_idx = {h[0] for h in heads}
    for i, line in enumerate(lines):
        if i in head_idx:
            slug = anchors[i]
            out.append(f'<a id="{slug}"></a>')
        out.append(line)
    # prepend TOC at very top
    return "\n".join(toc + out)


# ---------------------------------------------------------------------------
# §3.3 validation
# ---------------------------------------------------------------------------
def validate(final_text: str, anchors: set, toc_links: set) -> list[str]:
    errors = []
    lines = final_text.split("\n")
    # 1. fences paired
    fences = [l for l in lines if l.strip().startswith("```")]
    if len(fences) % 2 != 0:
        errors.append(f"fences not paired: {len(fences)}")
    # 2. no PUA
    if any(0xF000 <= ord(c) <= 0xF0FF for c in final_text):
        errors.append("PUA characters present")
    # 3. TOC links subset of anchors
    if not toc_links.issubset(anchors):
        missing = toc_links - anchors
        errors.append(f"TOC links not subset of anchors: {missing}")
    return errors


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="P3: TOC + anchors + validate")
    parser.add_argument("input", nargs="?", default="text_stream_p2.md")
    parser.add_argument("output", nargs="?", default="llms-from-scratch.md")
    args = parser.parse_args()

    lines = Path(args.input).read_text(encoding="utf-8").split("\n")
    heads = scan_headings(lines)
    anchors_map = assign_anchors(heads)
    anchor_set = set(anchors_map.values())

    toc = build_toc(heads, anchors_map)
    # collect TOC link targets
    toc_links = set(re.findall(r"\]\(#([a-z0-9-]+)\)", "\n".join(toc)))

    final = inject_anchors_and_toc(lines, heads, anchors_map, toc)
    Path(args.output).write_text(final, encoding="utf-8")

    errors = validate(final, anchor_set, toc_links)
    print(f"P3 written {len(final.splitlines())} lines -> {args.output}")
    print(f"headings: {len(heads)}, anchors: {len(anchor_set)}, toc-links: {len(toc_links)}")
    if errors:
        print("VALIDATION FAILED:")
        for e in errors:
            print("  -", e)
        sys.exit(1)
    else:
        print("§3.3 VALIDATION PASSED (fences paired, no PUA, TOC⊆anchors)")
