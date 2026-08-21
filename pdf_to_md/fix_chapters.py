#!/usr/bin/env python3
"""fix_chapters.py - Fix chapter titles and 'This chapter covers' sections.

Root cause
----------
The P1 pipeline (pdf_text_stream.py) has two issues with chapter intro pages:

1. Chapter titles: The chapter number and title are in separate PDF blocks
   with large italic font. P1 does not recognize them as headings (it only
   recognizes "Chapter N" / "N.N Title" patterns). Multi-line titles get
   split into separate paragraphs.

2. "This chapter covers" bullets: The bullet items use Wingdings font for
   the bullet marker, which P1's inline_code_wrap_line doesn't handle.
   The bullet text (in a different font) becomes a separate block, and
   most bullets are lost entirely. Only the last bullet's tail text
   sometimes survives as a standalone line.

Fix strategy
------------
1. Extract complete bullet lists from the PDF (using font detection to
   identify Wingdings bullet markers and merge continuation blocks).
2. Replace the broken "This chapter covers" + fragments in MD with
   complete markdown bullet lists from the PDF.
3. Add proper ## N heading prefixes to chapter titles, merging fragments.

Usage
-----
    python fix_chapters.py              # dry-run preview
    python fix_chapters.py --apply      # apply changes
"""

import re
import sys
import unicodedata
from pathlib import Path

import pymupdf


def norm(s: str) -> str:
    """NFKC-normalize and lowercase for comparison."""
    return unicodedata.normalize("NFKC", s).strip().lower()


def extract_chapter_data(pdf_path: str) -> list[dict]:
    """Extract chapter titles and 'This chapter covers' bullets from PDF.

    Returns a list of dicts with keys:
    - chapter_num: int (1-7)
    - title: str (full chapter title without number)
    - title_parts: list[str] (fragments as they appear in PDF blocks)
    - covers_bullets: list[str] (complete bullet items)
    """
    doc = pymupdf.open(pdf_path)
    toc = doc.get_toc()

    # Get chapter entries from TOC (level 1 = chapter)
    chapter_entries = []
    for level, title, page in toc:
        if level != 1:
            continue
        tn = norm(title.strip())
        if tn in ("contents", "index", "preface", "brief contents"):
            continue
        # Extract chapter number and title
        m = re.match(r"(\d+)\s+(.*)", title.strip())
        if m:
            chapter_entries.append({
                "chapter_num": int(m.group(1)),
                "full_title": title.strip(),
                "title_after_num": m.group(2),
                "page": page,
            })

    # For each chapter, extract block structure from the intro page
    for entry in chapter_entries:
        pg = entry["page"] - 1  # 0-indexed
        if pg >= len(doc):
            entry["title_parts"] = []
            entry["covers_bullets"] = []
            continue

        page = doc[pg]
        blocks = page.get_text("dict")["blocks"]

        # Extract title parts (large italic blocks before main text)
        title_parts = []
        covers_bullets = []
        found_covers = False
        current_bullet = ""

        for block in blocks:
            if block["type"] != 0:
                continue

            # Get block text and font info
            block_text = ""
            font_name = ""
            font_size = 0
            for line in block["lines"]:
                line_text = ""
                for span in line["spans"]:
                    line_text += span["text"]
                block_text += line_text
                if not font_name:
                    font_name = line["spans"][0]["font"]
                    font_size = line["spans"][0]["size"]
            block_text = block_text.strip()

            if not block_text:
                continue

            # Detect "This chapter covers"
            if "this chapter covers" in block_text.lower():
                found_covers = True
                # Save any accumulated bullet
                if current_bullet:
                    covers_bullets.append(current_bullet)
                    current_bullet = ""
                continue

            if not found_covers:
                # Title parts: large font (>20pt) italic blocks
                if font_size > 20 and ("Italic" in font_name or "Baskerville" in font_name):
                    title_parts.append(block_text)
            else:
                # After "This chapter covers"
                is_bullet = "Wingdings" in font_name
                if is_bullet:
                    # Save previous bullet
                    if current_bullet:
                        covers_bullets.append(current_bullet)
                    # Start new bullet (remove the Wingdings bullet char)
                    text = block_text.strip()
                    # Wingdings chars are non-ASCII, remove them
                    text = re.sub(r"[^\x20-\x7e]", "", text).strip()
                    current_bullet = text
                else:
                    # Continuation of current bullet
                    if current_bullet:
                        current_bullet += " " + block_text
                    # else: orphan text (shouldn't happen)

        # Save last bullet
        if current_bullet:
            covers_bullets.append(current_bullet)

        entry["title_parts"] = title_parts
        entry["covers_bullets"] = covers_bullets

    doc.close()
    return chapter_entries


def fix_chapter_titles(lines: list[str], chapter_data: list[dict]) -> int:
    """Fix chapter title lines: merge fragments and add ## N prefix.

    Uses the TOC title (which has correct spacing) for matching,
    not the block-extracted text (which may have missing spaces).

    Returns the number of fixes applied.
    """
    fixes = 0

    for entry in chapter_data:
        ch_num = entry["chapter_num"]
        title_text = entry["title_after_num"]  # From TOC, correct spacing
        title_parts = entry["title_parts"]  # From blocks, may have bad spacing

        if not title_parts:
            print(f"  WARNING: Ch{ch_num} has no title parts in PDF")
            continue

        # Clean None lines from previous iterations
        lines[:] = [l for l in lines if l is not None]

        # Case 1: Full title on a single line (e.g., Ch2, Ch4, Ch5, Ch6)
        title_n = norm(title_text)
        found_single = False
        for i, line in enumerate(lines):
            if line is None:
                continue
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            if norm(s) == title_n:
                # Check context: should be standalone (blank before and after)
                next_blank = (i + 1 >= len(lines) or lines[i + 1].strip() == "")
                prev_blank = (i == 0 or lines[i - 1].strip() == "")
                if next_blank and prev_blank:
                    lines[i] = f"## {ch_num} {s}"
                    print(f"  Ch{ch_num}: single-line title fixed at L{i+1}")
                    fixes += 1
                    found_single = True
                    break

        if found_single:
            continue

        # Case 2: Title split across multiple lines (e.g., Ch1, Ch3, Ch7)
        # Use block-extracted title parts to find fragments in MD
        if len(title_parts) >= 2:
            part1_n = norm(title_parts[0])
            for i, line in enumerate(lines):
                if line is None:
                    continue
                s = line.strip()
                if not s or s.startswith("#"):
                    continue
                if norm(s) == part1_n:
                    # Check if next non-blank lines match remaining parts
                    j = i + 1
                    remaining_parts = list(title_parts[1:])
                    while j < len(lines) and remaining_parts:
                        lj = lines[j]
                        if lj is None or lj.strip() == "":
                            j += 1
                            continue
                        if norm(lj.strip()) == norm(remaining_parts[0]):
                            remaining_parts.pop(0)
                            j += 1
                        else:
                            break

                    if not remaining_parts:
                        # All parts found! Replace first with full heading
                        lines[i] = f"## {ch_num} {title_text}"
                        # Blank out intermediate lines
                        for k in range(i + 1, j):
                            lines[k] = None
                        print(f"  Ch{ch_num}: merged {len(title_parts)} title parts at L{i+1}")
                        fixes += 1
                        break

    # Final cleanup
    lines[:] = [l for l in lines if l is not None]
    return fixes


def fix_chapter_covers(lines: list[str], chapter_data: list[dict]) -> int:
    """Fix 'This chapter covers' sections with complete bullet lists from PDF.

    Returns the number of fixes applied.
    """
    fixes = 0

    for entry in chapter_data:
        ch_num = entry["chapter_num"]
        bullets = entry["covers_bullets"]

        if not bullets:
            print(f"  WARNING: Ch{ch_num} has no bullets in PDF")
            continue

        # Clean None lines
        lines[:] = [l for l in lines if l is not None]

        # Find "This chapter covers" line near the chapter heading
        ch_heading = f"## {ch_num} "
        for i, line in enumerate(lines):
            s = line.strip()
            if s.lower() != "this chapter covers" or s.startswith("#"):
                continue

            # Check if this is near the chapter heading (within 20 lines)
            found_nearby = False
            for k in range(max(0, i - 20), i):
                if lines[k].strip().startswith(ch_heading):
                    found_nearby = True
                    break
            if not found_nearby:
                continue

            # Found the right "This chapter covers"
            # Scan forward to find all fragment lines (short, no terminal punct)
            # Fragments are separated by blank lines
            j = i + 1
            fragment_lines = []  # indices of fragment text lines
            blank_lines = []  # indices of blank lines between fragments
            while j < len(lines):
                sj = lines[j].strip()
                if sj == "":
                    blank_lines.append(j)
                    j += 1
                    continue
                # Is this a fragment? (short, no terminal punct, no special prefix)
                is_fragment = (
                    len(sj) < 80
                    and not sj.startswith(("#", ">", "- ", "* ", "!", "<a", "```", "["))
                    and sj[-1] not in ".?!:。？！："
                )
                if is_fragment:
                    fragment_lines.append(j)
                    j += 1
                else:
                    break

            if not fragment_lines:
                # No fragments to remove, just replace the heading
                lines[i] = "This chapter covers:"
                insert_at = i + 1
            else:
                # Build replacement: "This chapter covers:" + bullets + blank
                lines[i] = "This chapter covers:"
                bullet_text = []
                for bullet in bullets:
                    bullet_text.append(f"- {bullet}")

                # Remove everything from i+1 to j-1 (fragments + blanks)
                # Replace with bullet list + trailing blank
                lines[i + 1:j] = bullet_text + [""]

            print(f"  Ch{ch_num}: replaced 'This chapter covers' with {len(bullets)} bullets (removed {len(fragment_lines)} fragments)")
            fixes += 1
            break

    # Final cleanup
    lines[:] = [l for l in lines if l is not None]
    return fixes


def main():
    pdf_path = "Build a Large Language Model (From Scratch) (Sebastian Raschka).pdf"
    md_path = Path("llms-from-scratch.md")
    apply = "--apply" in sys.argv

    # Extract chapter data from PDF
    print("=== Extracting chapter data from PDF ===")
    chapter_data = extract_chapter_data(pdf_path)
    for entry in chapter_data:
        print(f"  Ch{entry['chapter_num']}: '{entry['title_after_num']}'")
        print(f"    Title parts: {entry['title_parts']}")
        print(f"    Bullets: {entry['covers_bullets']}")
        print()

    # Read MD
    lines = md_path.read_text(encoding="utf-8").split("\n")
    original_count = len(lines)

    # Fix 1: Chapter titles
    print("=== Fixing chapter titles ===")
    title_fixes = fix_chapter_titles(lines, chapter_data)
    print(f"  {title_fixes} title fixes")
    print()

    # Fix 2: "This chapter covers" sections
    print("=== Fixing 'This chapter covers' sections ===")
    covers_fixes = fix_chapter_covers(lines, chapter_data)
    print(f"  {covers_fixes} covers fixes")
    print()

    # Summary
    new_count = len(lines)
    print(f"=== Summary ===")
    print(f"  Original lines: {original_count}")
    print(f"  New lines: {new_count}")
    print(f"  Title fixes: {title_fixes}")
    print(f"  Covers fixes: {covers_fixes}")

    if apply:
        md_path.write_text("\n".join(lines), encoding="utf-8")
        print(f"\n  Applied! Written to {md_path}")
    else:
        print(f"\n  Dry run. Use --apply to apply changes.")


if __name__ == "__main__":
    main()
