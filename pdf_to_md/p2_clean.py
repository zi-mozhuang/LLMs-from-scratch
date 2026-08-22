#!/usr/bin/env python3
"""
P2: post-processing of the P1 text stream (p1_text_stream.py output).

Pipeline order is FIXED (pdf-to-md-plan.md §3.1 / handoff doc):
  header removal (done in P1) -> paragraph merge (done in P1) -> THIS module:
    1. dehyphenate broken words (word- word -> wordword) with compound-prefix
       exclusion list
    2. concept boxes: PUA bullet \\uf0a1 lines -> blockquote (> ...)
    3. math: PUA greek (\\uf061->alpha, \\uf077->omega), middle dot \\u22c5 -> \\cdot
       (idempotent: applied twice)
    4. figure synthesis: insert ![Fig X.Y](extracted_images/figures/FigX.Y_pNNN.png)
       before each "Figure X.Y ..." caption line
    5. fence balance + merge adjacent same-language code blocks
    6. bold table/listing/figure caption titles (exclude verb-led body references)

Input: text_stream_full.md (or any P1 output)
Output: text_stream_p2.md
"""

import json
import re
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
# Prefixes that form REAL hyphenated compounds: keep the hyphen (do NOT join
# across a line break). e.g. self-attention, multi-head, cross-attention,
# state-of-the-art, in-progress, pre-training, non-linear ...
COMPOUND_PREFIXES = {
    "self", "state", "well", "cross", "multi", "pre", "post", "sub", "non",
    "anti", "bio", "co", "re", "un", "in", "semi", "auto", "inter", "intra",
    "extra", "super", "micro", "macro", "neuro", "socio", "geo", "cyber",
    "open", "near", "far",
}
# Prefixes that are usually broken words (join across line break):
# under-stand -> understand, over-view -> overview, up-date -> update,
# down-stream -> downstream, out-put -> output.
JOIN_PREFIXES = {"under", "over", "up", "down", "out"}

# PUA private-area glyphs used by the PDF embedded math font -> real unicode.
PUA_MAP = {
    "\uf061": "\u03b1",  # alpha
    "\uf077": "\u03c9",  # omega
}

# ---------------------------------------------------------------------------
# 1. Dehyphenation (broken words across the paragraph-merge space)
# ---------------------------------------------------------------------------
BREAK_RE = re.compile(r"(\w+)- (\w+)")


def _dehyphen_replace(m: re.Match) -> str:
    left, right = m.group(1), m.group(2)
    low = left.lower()
    # Keep the hyphen for genuine compounds (self-, multi-, in-, state-, ...).
    if low in COMPOUND_PREFIXES:
        return m.group(0)
    # Join broken words for prefixes that are usually word splits
    # (under-stand -> understand, over-view -> overview, ...).
    if low in JOIN_PREFIXES:
        return left + right
    # Exclude when the right part is a standalone capitalised proper noun start
    # that would indicate a genuine compound (e.g. "GPT-3" handled by digit rule).
    if right[0].isupper():
        return m.group(0)
    return left + right


def dehyphenate(text: str) -> str:
    prev = None
    cur = text
    # iterate until stable (handles chained breaks)
    while prev != cur:
        prev = cur
        cur = BREAK_RE.sub(_dehyphen_replace, cur)
    return cur


# ---------------------------------------------------------------------------
# 2. Concept boxes -> blockquote
# ---------------------------------------------------------------------------
def concept_boxes_to_blockquote(lines: list[str]) -> list[str]:
    """Convert ONLY lines carrying the PUA bullet (\\uf0a1) into blockquote
    lines. Lines without the bullet are passed through unchanged, so we never
    swallow surrounding body text (e.g. 'about the author', chapter TOC trees).
    Consecutive bullet lines are emitted as one blockquote block."""
    out: list[str] = []
    buf: list[str] = []
    for line in lines:
        if "\uf0a1" in line:
            buf.append(line.replace("\uf0a1", "").strip())
        else:
            if buf:
                out.extend("> " + b for b in buf if b)
                out.append("")  # blank line after blockquote
                buf = []
            out.append(line)
    if buf:
        out.extend("> " + b for b in buf if b)
        out.append("")
    return out


# ---------------------------------------------------------------------------
# 3. Math cleanup
# ---------------------------------------------------------------------------
def fix_math(text: str) -> str:
    for pua, uni in PUA_MAP.items():
        text = text.replace(pua, uni)
    # middle dot -> \cdot (idempotent twice)
    text = text.replace("\u22c5", "\\cdot")
    text = text.replace("\u22c5", "\\cdot")  # second pass (idempotent)
    return text


# ---------------------------------------------------------------------------
# 4. Figure synthesis
# ---------------------------------------------------------------------------
def load_figure_map(manifest_path: str) -> dict[str, str]:
    data = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    fig_map: dict[str, str] = {}
    for entry in data.get("figures", []):
        fig_map[entry["fig"]] = entry["file"]
    return fig_map


FIG_RE = re.compile(r"^Figure (\d+\.\d+)\b")


FIG_LINE_RE = re.compile(r"^!\[Fig [^\]]+\]\(([^)]+)\)$")


def dedupe_figures(lines: list[str]) -> list[str]:
    """Drop duplicate figure-reference lines.

    Some PDF pages render the same figure (e.g. in the body and again in a
    repeated footer/margin note), so ``insert_figures`` can emit the same
    ``![Fig X.X](...)`` line more than once. Render it only once, keeping the
    surrounding caption/body text of every occurrence.
    """
    seen: set[str] = set()
    out: list[str] = []
    for line in lines:
        m = FIG_LINE_RE.match(line)
        if m:
            path = m.group(1)
            if path in seen:
                continue
            seen.add(path)
        out.append(line)
    return out


def fix_text_noise(text: str) -> str:
    """Fix a handful of extraction artifacts introduced by the PDF text layer.

    * Publisher wordmark letters are spaced out (``M A N N I N G``).
    * Author names with a combining acute accent are mis-encoded
      (``Dragosavljevic´`` -> ``Dragosavljević``).
    * ASCII ``--`` used as an em dash.
    """
    # Publisher wordmark.
    text = text.replace("M A N N I N G", "MANNING")
    # Mis-encoded author name (combining acute -> precomposed c-with-acute).
    text = text.replace("Dragosavljevic´", "Dragosavljević")
    # Em dash: space-surrounded "--" becomes "—".
    text = text.replace(" -- ", " — ")
    # Em dash inside prose: "word--word" -> "word—word". Restricted to
    # letter-to-letter so code/token output like "'--'" is left untouched.
    text = re.sub(r"(\w)--(\w)", r"\1—\2", text)
    return text


LIST_MARKER_RE = re.compile(r"^[–•]\s+(.*)$")


def normalize_list_markers(lines: list[str]) -> list[str]:
    """Normalize list markers and keep blockquote lists properly fenced.

    * En-dash / bullet list markers (``– ``, ``• ``) become ``- ``.
    * A list item that belongs to a preceding blockquote (its previous
      non-empty line starts with ``> ``) gets the ``> `` prefix restored, so it
      renders inside the quote instead of breaking out of it.
    """
    out: list[str] = []
    in_blockquote = False
    for line in lines:
        if line.lstrip().startswith(">"):
            in_blockquote = True
            out.append(line)
            continue
        m = LIST_MARKER_RE.match(line)
        if m:
            if in_blockquote:
                out.append("> - " + m.group(1).strip())
            else:
                out.append("- " + m.group(1).strip())
            continue
        if line.strip():
            # A normal paragraph ends the blockquote list context.
            in_blockquote = False
        out.append(line)
    return out


def insert_figures(lines: list[str], fig_map: dict[str, str]) -> list[str]:
    out = []
    for line in lines:
        m = FIG_RE.match(line)
        if m and m.group(1) in fig_map:
            rel = fig_map[m.group(1)]
            # manifest stores paths relative to extracted_images/; the final md
            # lives in pdf_to_md/, so prefix with extracted_images/.
            out.append(f"![Fig {m.group(1)}](extracted_images/{rel})")
        out.append(line)
    return out


# ---------------------------------------------------------------------------
# 5. Fence balance + merge adjacent same-language blocks
# ---------------------------------------------------------------------------
FENCE_RE = re.compile(r"^```(\w*)$")


def rebalance_fences(lines: list[str]) -> list[str]:
    """Ensure fences are paired and merge adjacent same-language code blocks."""
    out: list[str] = []
    i = 0
    n = len(lines)
    open_lang = None
    while i < n:
        line = lines[i]
        fm = FENCE_RE.match(line)
        if fm:
            lang = fm.group(1) or "text"
            if open_lang is None:
                out.append(f"```{lang}")
                open_lang = lang
                i += 1
                # consume code body until closing fence
                code = []
                while i < n and not FENCE_RE.match(lines[i]):
                    code.append(lines[i])
                    i += 1
                # skip the closing fence line if present
                if i < n:
                    i += 1
                # peek next block: if immediately another same-lang fence, merge
                j = i
                while j < n and lines[j].strip() == "":
                    j += 1
                merge = False
                if j < n and FENCE_RE.match(lines[j]):
                    nxt_lang = FENCE_RE.match(lines[j]).group(1) or "text"
                    if nxt_lang == open_lang:
                        merge = True
                        i = j + 1  # skip opening fence of next block
                        while i < n and not FENCE_RE.match(lines[i]):
                            code.append(lines[i])
                            i += 1
                        if i < n:
                            i += 1  # skip its closing fence
                out.append("\n".join(code).rstrip())
                out.append("```")
                open_lang = None
            else:
                # stray closing fence without a tracked open -> skip
                i += 1
        else:
            out.append(line)
            i += 1
    # Close a dangling open fence at EOF (PDF extraction sometimes drops the
    # trailing ``` of the last code block on a page).
    if open_lang is not None:
        out.append("```")
    return out


# ---------------------------------------------------------------------------
# 6. Bold table / listing / figure caption titles
# ---------------------------------------------------------------------------
CAP_RE = re.compile(r"^(Figure|Table|Listing) (\d+\.\d+) (.+)$")


def bold_captions(lines: list[str]) -> list[str]:
    out = []
    for line in lines:
        m = CAP_RE.match(line)
        if m:
            kind, num, rest = m.group(1), m.group(2), m.group(3)
            # Exclude verb-led body reference sentences (e.g. "Table 1.1 reports...")
            first_word = rest.split(" ", 1)[0]
            if first_word and first_word[0].islower():
                out.append(line)  # body reference, leave as-is
            else:
                # Bold only the caption name (kind + number), keep description
                # in normal text.
                out.append(f"**{kind} {num}** {rest}")
        else:
            out.append(line)
    return out


# ---------------------------------------------------------------------------
# 7. Exercise headings
# ---------------------------------------------------------------------------
EXERCISE_RE = re.compile(r"^Exercise (\d+)\.(\d+)\b\s*(.*)$")


def format_exercises(lines: list[str]) -> list[str]:
    """Render 'Exercise N.N ...' paragraphs as a blockquote so they are visually
    distinct from body prose.

    In the PDF the exercise title (FranklinGothic-Demi, 10.5pt) and its
    description (FranklinGothic-Book, 9.5pt) are different fonts but get merged
    into one paragraph by P1. We render both as a blockquote:
      > **Exercise N.N**
      >
      > <original description text>

    The short title phrase (e.g. 'Byte pair encoding of unknown words') is
    preserved as the first words of the description, so no text is lost.
    Because the exercise is no longer a Markdown heading, P3's heading/anchor
    scan ignores it; exercises are also excluded from the TOC by is_structural,
    so the TOC subset invariant still holds.
    """
    out = []
    for line in lines:
        m = EXERCISE_RE.match(line)
        if m:
            num = f"{m.group(1)}.{m.group(2)}"
            desc = m.group(3).strip()
            out.append(f"> **Exercise {num}**")
            if desc:
                out.append(">")
                out.append(f"> {desc}")
        else:
            out.append(line)
    return out


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------
def p2_clean(text: str, manifest_path: str) -> str:
    lines = text.split("\n")

    # 1. dehyphenate (line-level; operates on already paragraph-merged text)
    lines = [dehyphenate(l) for l in lines]

    # 2. concept boxes -> blockquote
    lines = concept_boxes_to_blockquote(lines)

    # 3. math cleanup
    lines = [fix_math(l) for l in lines]

    # 4. figure synthesis
    fig_map = load_figure_map(manifest_path)
    lines = insert_figures(lines, fig_map)

    # 4b. drop duplicate figure-reference lines
    lines = dedupe_figures(lines)

    # 5. fence rebalance + merge
    lines = rebalance_fences(lines)

    # 6. bold captions
    lines = bold_captions(lines)

    # 7. exercise paragraphs -> #### headings
    lines = format_exercises(lines)

    # 8. publisher wordmark / author-name / em-dash noise.
    # Skip *code* fences (python/bash/...), but still process `text` fences,
    # which often contain prose excerpts where "--" is meant as an em dash.
    in_code_fence = False
    cleaned: list[str] = []
    for line in lines:
        m = FENCE_RE.match(line)
        if m:
            lang = (m.group(1) or "").strip().lower()
            if line.strip().startswith("```"):
                opening = not line.strip().endswith("```")  # not a one-liner
                if opening:
                    in_code_fence = lang not in ("", "text")
            cleaned.append(line)
            continue
        cleaned.append(line if in_code_fence else fix_text_noise(line))
    lines = cleaned

    # 9. normalize list markers (en-dash/bullet -> "-", keep blockquote lists)
    lines = normalize_list_markers(lines)

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="P2: clean P1 text stream")
    parser.add_argument("input", nargs="?", default="text_stream_full.md")
    parser.add_argument("output", nargs="?", default="text_stream_p2.md")
    parser.add_argument(
        "--manifest",
        default="extracted_images/manifest.json",
    )
    args = parser.parse_args()

    text = Path(args.input).read_text(encoding="utf-8")
    result = p2_clean(text, args.manifest)
    Path(args.output).write_text(result, encoding="utf-8")
    print(f"P2 written {len(result.splitlines())} lines -> {args.output}")
