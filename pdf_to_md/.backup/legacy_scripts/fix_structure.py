#!/usr/bin/env python3
"""
fix_structure.py — 合并自以下 5 个脚本（结构类修复，逻辑原样搬运）：
  - fix_headings.py
  - fix_chapters.py
  - fix_pseudo_headings.py
  - fix_format_consistency.py
  - fix_chapter_covers.py

按 pdf-to-md-plan.md §3 顺序依次执行各修复函数，统一读取/写回
llms-from-scratch.md，保留每个原脚本的自检断言。

用法：
    python fix_structure.py [--apply] [--pdf <book.pdf>] [--md <file.md>]
（原 5 个脚本的 --apply 语义统一为「应用写回」，未指定 --apply 则 dry-run 预览。）
"""

from __future__ import annotations

import argparse
import glob
import re
import sys
import unicodedata
from pathlib import Path

import pymupdf


# --------------------------------------------------------------------------- #
# 共享配置 / 路径                                                              #
# --------------------------------------------------------------------------- #
PATH = Path("llms-from-scratch.md")


# =========================================================================== #
# 原 fix_headings.py                                                          #
# =========================================================================== #
# PDF TOC entries to skip (structural, not content headings).
SKIP_TITLES = {
    "brief contents", "contents", "index",
    "build a large language model (from scratch)",
}

# Map PDF TOC level → Markdown heading prefix.
LEVEL_TO_PREFIX = {1: "## ", 2: "### ", 3: "#### "}


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


# =========================================================================== #
# 原 fix_chapters.py                                                          #
# =========================================================================== #
def norm_ch(s: str) -> str:
    """NFKC-normalize and lowercase for comparison (renamed from norm to avoid
    clash with fix_headings.norm)."""
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
        tn = norm_ch(title.strip())
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
        title_n = norm_ch(title_text)
        found_single = False
        for i, line in enumerate(lines):
            if line is None:
                continue
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            if norm_ch(s) == title_n:
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
            part1_n = norm_ch(title_parts[0])
            for i, line in enumerate(lines):
                if line is None:
                    continue
                s = line.strip()
                if not s or s.startswith("#"):
                    continue
                if norm_ch(s) == part1_n:
                    # Check if next non-blank lines match remaining parts
                    j = i + 1
                    remaining_parts = list(title_parts[1:])
                    while j < len(lines) and remaining_parts:
                        lj = lines[j]
                        if lj is None or lj.strip() == "":
                            j += 1
                            continue
                        if norm_ch(lj.strip()) == norm_ch(remaining_parts[0]):
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
                lines[i] = "**This chapter covers**"
                insert_at = i + 1
            else:
                # Build replacement: "This chapter covers:" + bullets + blank
                lines[i] = "**This chapter covers**"
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


# =========================================================================== #
# 原 fix_pseudo_headings.py                                                   #
# =========================================================================== #
ANCHOR_RE = re.compile(r'^<a id="[^"]+"></a>$')

# --- macOS callout 重建（依据 PDF 物理页 304 原文） ---
MACOS_CALLOUT = [
    "> **PyTorch on macOS**",
    ">",
    "> On an Apple Mac with an Apple Silicon chip (like the M1, M2, M3, or newer models) "
    "instead of a computer with an Nvidia GPU, you can change "
    '`device = torch.device("cuda" if torch.cuda.is_available() else "cpu")` to '
    '`device = torch.device("mps" if torch.backends.mps.is_available() else "cpu")` '
    "to take advantage of this chip.",
]


def fix_macos_callout(lines: list[str]) -> int:
    """重建被切断的 PyTorch on macOS 引用块，并删除伪标题 'to to take...'。"""
    if any("to take advantage of this chip" in l and l.startswith(">") for l in lines):
        return 0
    start = next((i for i, l in enumerate(lines) if l.strip() == "> **PyTorch on macOS**"), None)
    if start is None:
        return 0
    end = next((i for i, l in enumerate(lines)
                if l.startswith("### to to take advantage of this chip")), None)
    if end is None:
        return 0
    # 删除伪标题前的锚点行
    seg_end = end + 1
    lines[start:seg_end] = MACOS_CALLOUT
    return 1


def fix_appendix_headings(lines: list[str]) -> int:
    """修复附录 B/C/D 的标题缺失/损坏/级别。"""
    n = 0
    # 损坏标题残留：孤立 `.bz/EZJR` 行（当前 P1 输出中单独成行，
    # 正确标题 `## Appendix C Exercise solutions` 由 P3 锚点注入保留在下方）。
    # 删除孤立行；若其后缺失正确标题则补上。
    i = 0
    while i < len(lines):
        if lines[i].strip() == ".bz/EZJR":
            j = i + 1
            while j < len(lines) and (lines[j].strip() == ""
                                      or lines[j].startswith("<a id=")):
                j += 1
            if j < len(lines) and lines[j].strip() == "## Appendix C Exercise solutions":
                del lines[i]
            else:
                lines[i] = "## Appendix C Exercise solutions"
            n += 1
            continue
        i += 1
    fixes = [
        # (匹配正则, 替换结果)——兼容旧版同行损坏形态
        (re.compile(r"^#{2,3} \.bz/EZJR appendix C Exercise solutions"),
         "## Appendix C Exercise solutions"),
        (re.compile(r"^### appendix D Adding bells and whistles to the training loop"),
         "## Appendix D Adding bells and whistles to the training loop"),
        (re.compile(r"^appendix B References and further reading\s*$"),
         "## Appendix B References and further reading"),
    ]
    for i, l in enumerate(lines):
        for pat, repl in fixes:
            if pat.match(l):
                lines[i] = repl
                n += 1
    # 附录 D/E 小节标题 ### D.x / ### E.x 降级为 ####（父级升为 ## 后保持层级）
    sub_re = re.compile(r"^### ([DE]\.\d+ .+)$")
    for i, l in enumerate(lines):
        m = sub_re.match(l)
        if m:
            lines[i] = f"#### {m.group(1)}"
            n += 1
    return n


def fix_figure_e1_leak(lines: list[str]) -> int:
    """删除 Figure E.1 图内文字泄漏碎片（孤立 ΔW/W/d 行与 '### W d' 伪标题）。
    仅在 'Figure E.1 illustrates' 到 caption 之间的窗口内清理，避免误删。"""
    start = next((i for i, l in enumerate(lines)
                  if l.startswith("Figure E.1 illustrates")), None)
    end = next((i for i, l in enumerate(lines)
                if l.strip().startswith(("**Figure E.1", "Figure E.1 A comparison"))), None)
    if start is None or end is None or end <= start:
        return 0
    n = 0
    frag = {"ΔW", "W", "d"}
    keep = []
    for i in range(start, end):
        l = lines[i]
        is_frag = (l.startswith("### W d") or l.strip() in frag
                   or (ANCHOR_RE.match(l) and i + 1 < end
                       and lines[i + 1].startswith("### W d")))
        if is_frag:
            if keep and ANCHOR_RE.match(keep[-1]):
                keep.pop()
            n += 1
            continue
        keep.append(l)
    lines[start:end] = keep
    return n


def wrap_leaked_output_block(lines: list[str]) -> int:
    """把泄漏为正文的模型输出块（### Instruction: / ### Input:）包进 ```text 围栏。"""
    if any(l.strip() == "### Instruction:" for l in _outside_fence(lines)):
        pass
    else:
        return 0
    start = next((i for i, l in enumerate(lines)
                  if l.startswith("Below is an instruction that describes a task")
                  and i > 0 and "are shown next" in lines[i - 2]), None)
    if start is None:
        # 兜底：第一处非围栏内的 Below is an instruction
        for i, l in enumerate(lines):
            if l.startswith("Below is an instruction that describes a task"):
                start = i
                break
    if start is None:
        return 0
    end = next((i for i, l in enumerate(lines)
                if l.startswith(">> The author of ‘Pride and Prejudice’ is Jane Austen.")), None)
    if end is None:
        return 0
    seg = []
    for l in lines[start:end + 1]:
        if not l.strip():
            continue
        if ANCHOR_RE.match(l):
            continue
        seg.append(l[len("### "):] if l.startswith("### ") else l)
    lines[start:end + 1] = ["```text"] + seg + ["```"]
    return 1


def fix_evaluation_section(lines: list[str]) -> int:
    """重建 §7.8 评估方法列表 + 断裂的 Conversational performance 概念框。"""
    if any(l.strip() == "> In practice, it can be useful to consider all three types"
           for l in lines):
        return 0
    s = next((i for i, l in enumerate(lines)
              if l.startswith("Most importantly, model evaluation is not as straightforward")), None)
    if s is None:
        return 0
    e = next((i for i, l in enumerate(lines)
              if l.startswith("Conversational performance of LLMs refers")), None)
    if e is None:
        return 0
    rebuilt = [
        lines[s],  # Most importantly, ... 段保留
        "",
        "- Short-answer and multiple-choice benchmarks, such as Measuring Massive Multitask "
        "Language Understanding (MMLU; https://arxiv.org/abs/2009.03300), which test the "
        "general knowledge of a model.",
        "- Human preference comparison to other LLMs, such as LMSYS chatbot arena "
        "(https://arena.lmsys.org).",
        "- Automated conversational benchmarks, where another LLM like GPT-4 is used to "
        "evaluate the responses, such as AlpacaEval (https://tatsu-lab.github.io/alpaca_eval/).",
        "",
        "In practice, it can be useful to consider all three types of evaluation methods: "
        "multiple-choice question answering, human evaluation, and automated metrics that "
        "measure conversational performance. However, since we are primarily interested in "
        "assessing conversational performance rather than just the ability to answer "
        "multiple-choice questions, human evaluation and automated metrics may be more relevant.",
        "",
        "> **Conversational performance**",
        ">",
        "> Conversational performance of LLMs refers to their ability to engage in human-like "
        "communication by understanding context, nuance, and intent. It encompasses skills "
        "such as providing relevant and coherent responses, maintaining consistency, and "
        "adapting to different topics and styles of interaction.",
    ]
    lines[s:e + 1] = rebuilt
    return 1


def fix_exercise_72_dup(lines: list[str]) -> int:
    """删除 Exercise 7.2 的空壳引用块（保留完整版）。"""
    n = 0
    i = 0
    while i < len(lines) - 2:
        if (lines[i].strip() == "> **Exercise 7.2**"
                and lines[i + 1].strip() == ">"
                and lines[i + 2].strip() == "> Instruction and input masking"):
            # 吃掉空壳块及其后空行
            j = i + 3
            while j < len(lines) and lines[j].strip() == "":
                j += 1
            del lines[i:j]
            n += 1
            continue
        i += 1
    return n


def cut_back_cover(lines: list[str]) -> int:
    """删除封底营销区（封面描述段之后到文件末尾），并为 liveProjects 页补标题。"""
    n = 0
    idx = next((i for i, l in enumerate(lines)
                if l.startswith("A view of the text processing steps in the context of an LLM")), None)
    if idx is not None:
        del lines[idx:]
        n += 1
    # liveProjects 推广页首行补成标题（幂等）
    for i, l in enumerate(lines):
        if l.strip() == "Hands-on projects for learning your way":
            if not lines[i].startswith("#"):
                lines[i] = "## Hands-on projects for learning your way"
                n += 1
            break
    return n


def _outside_fence(lines: list[str]) -> list[str]:
    out, in_f = [], False
    for l in lines:
        if l.strip().startswith("```"):
            in_f = not in_f
            continue
        if not in_f:
            out.append(l)
    return out


# =========================================================================== #
# 原 fix_format_consistency.py                                                #
# =========================================================================== #
FRONT_MATTER_CAPS = {
    "preface": "Preface",
    "acknowledgments": "Acknowledgments",
    "about this book": "About this book",
    "about the author": "About the author",
    "about the cover illustration": "About the cover illustration",
    "liveBook discussion forum": "LiveBook discussion forum",
}

# 允许合并的断词（词首小写 + 连字符 + 空格 + 小写）；
# in/self/non/cross 前缀保留连字符（in-progress / self-attention / non-linear / cross-entropy）
SPLIT_RE = re.compile(r"\b([a-z]{2,})- ([a-z]{2,})")
KEEP_HYPHEN = {"in", "self", "non", "cross"}
FIXED_WORDS = {
    "onedimensional": "one-dimensional",
    "twodimensional": "two-dimensional",
    "shortstory": "short-story",
    "finetuning": "fine-tuning",
    "pretraining": "pretraining",  # 原样保留
}


def iter_prose(lines):
    """yield (i, line) 仅围栏外的行。"""
    in_f = False
    for i, l in enumerate(lines):
        if l.strip().startswith("```"):
            in_f = not in_f
            continue
        if not in_f:
            yield i, l


def fix_chapter1(lines: list[str]) -> int:
    n = 0
    for i, l in enumerate(lines):
        if l.strip() == "## Chapter 1":
            lines[i] = "## Chapter 1 Understanding large language models"
            n += 1
        elif l.strip() == "### Understanding large language models":
            # 删除重复章标题行（连同前面的锚点行）
            if i > 0 and lines[i - 1].strip().startswith("<a id="):
                lines[i - 1] = ""
            lines[i] = ""
            n += 1
    return n


def fix_appendix_chapter_dups(lines: list[str]) -> int:
    """附录 B（References）内的 `## Chapter N` 是文献分组小节，降为 `###`；
    附录 C（Exercise solutions）内的 `## Chapter N` 是习题解答主节，保留 `##`。"""
    n = 0
    b_start = next((i for i, l in enumerate(lines)
                    if l.startswith("## Appendix B References")), None)
    c_start = next((i for i, l in enumerate(lines)
                    if l.startswith("## Appendix C Exercise solutions")), None)
    if b_start is None or c_start is None:
        return 0
    for i in range(b_start, c_start):
        m = re.match(r"^## Chapter (\d)\s*$", lines[i])
        if m:
            lines[i] = f"### Chapter {m.group(1)}"
            n += 1
    return n


def fix_front_matter_caps(lines: list[str]) -> int:
    n = 0
    for i, l in enumerate(lines):
        m = re.match(r"^(#{1,6}) (.+)$", l)
        if m and m.group(2) in FRONT_MATTER_CAPS:
            lines[i] = f"{m.group(1)} {FRONT_MATTER_CAPS[m.group(2)]}"
            n += 1
    return n


def fix_exercise_levels(lines: list[str]) -> int:
    n = 0
    for i, l in enumerate(lines):
        if re.match(r"^#### Exercise [A-E]\.\d", l):
            lines[i] = "###" + l[4:]
            n += 1
    return n


def fix_appendix_listings(lines: list[str]) -> int:
    """附录 Listing 标题加粗 + E.x 全行加粗样式统一 + 相邻重复去重。"""
    n = 0
    in_f = False
    for i, l in enumerate(lines):
        s = l.strip()
        if s.startswith("```"):
            in_f = not in_f
            continue
        if in_f:
            continue
        m = re.match(r"^Listing ([A-E]\.\d+) (.+)$", s)
        if m:
            bold = f"**Listing {m.group(1)}** {m.group(2)}"
            # 相邻（±2 行内）已有加粗重复 -> 删普通行
            if any(bold == lines[j].strip() for j in range(max(0, i - 2), min(len(lines), i + 3)) if j != i):
                lines[i] = ""
                n += 1
            else:
                lines[i] = bold
                n += 1
            continue
        m2 = re.match(r"^\*\*Listing ([A-E]\.\d+) (.+)\*\*$", s)
        if m2:
            lines[i] = f"**Listing {m2.group(1)}** {m2.group(2)}"
            n += 1
    return n


def fix_e2_heading(lines: list[str]) -> int:
    for i, l in enumerate(lines):
        if l.startswith("E.2 Preparing the dataset Before applying LoRA"):
            lines[i] = "#### E.2 Preparing the dataset\n\n" + l[len("E.2 Preparing the dataset"):].strip()
            return 1
    return 0


def fix_figure_e_captions(lines: list[str]) -> int:
    n = 0
    for i, l in enumerate(lines):
        m = re.match(r"^Figure (E\.\d) (.+)$", l.strip())
        if m and "illustrates" not in l and "plots" not in l:
            lines[i] = f"**Figure {m.group(1)}** {m.group(2)}"
            n += 1
    return n


def _join_split(m: re.Match) -> str:
    if m.group(1) in KEEP_HYPHEN:
        return f"{m.group(1)}-{m.group(2)}"
    return m.group(1) + m.group(2)


def fix_hyphenation(lines: list[str]) -> int:
    n = 0
    for i, l in iter_prose(lines):
        new = l
        for bad, good in FIXED_WORDS.items():
            new = re.sub(rf"\b{bad}\b", good, new)
        new = SPLIT_RE.sub(_join_split, new)
        if new != l:
            lines[i] = new
            n += 1
    return n


def fix_bullets_control(lines: list[str]) -> int:
    n = 0
    for i, l in enumerate(lines):
        new = l.replace("• ", "- ").replace("\x07", "")
        if new != l:
            lines[i] = new
            n += 1
    return n


# =========================================================================== #
# 原 fix_chapter_covers.py                                                    #
# =========================================================================== #
def fix_appendix_b_ch1(lines: list[str]) -> int:
    for i, l in enumerate(lines):
        if l.strip() == "## Chapter 1 Understanding large language models" and i > len(lines) // 2:
            lines[i] = "### Chapter 1"
            return 1
    return 0


def fix_chapter1_heading(lines: list[str]) -> int:
    if any(l.strip() == "## 1 Understanding large language models" for l in lines):
        return 0
    for i, l in enumerate(lines):
        if l.strip() in ("This chapter covers", "**This chapter covers**"):
            lines[i:i] = ["## 1 Understanding large language models", ""]
            return 1
    return 0


def normalize_covers(lines: list[str]) -> int:
    """把每个 This chapter covers 块转为 **...** + '- ' 列表。"""
    n = 0
    out = []
    i = 0
    while i < len(lines):
        l = lines[i]
        if l.strip() in ("This chapter covers", "**This chapter covers**"):
            # 收集其后的引用块（允许中间空行）
            j = i + 1
            quote: list[str] = []
            while j < len(lines):
                s = lines[j].strip()
                if s.startswith(">"):
                    quote.append(s[1:].strip())
                    j += 1
                elif s == "" and j + 1 < len(lines) and lines[j + 1].strip().startswith(">"):
                    j += 1
                else:
                    break
            items = [re.sub(r"^#{1,6}\s+", "", q) for q in quote if q]
            if items:
                out.append("**This chapter covers**")
                out.append("")
                out.extend(f"- {it}" for it in items)
                n += 1
                i = j
                continue
        out.append(l)
        i += 1
    lines[:] = out
    return n


# =========================================================================== #
# 统一 main：按 pdf-to-md-plan.md §3 顺序依次执行                              #
# =========================================================================== #
def _load_lines(md_path: Path, apply: bool) -> list[str]:
    return md_path.read_text(encoding="utf-8").split("\n")


def _write_back(md_path: Path, lines: list[str], apply: bool) -> None:
    if apply:
        md_path.write_text("\n".join(lines), encoding="utf-8")


def run_round1(pdf_path, md_path, apply, lines):
    """round1 标题 + 章节（对应 pdf-to-md-plan.md §3 round1）。"""
    print("=== [fix_headings] ===")
    fix_headings(pdf_path, md_path, apply=apply)
    # fix_headings 内部已写回（apply 时），统一从文件重新读
    lines[:] = _load_lines(md_path, apply)
    print("=== [fix_chapters] ===")
    chapter_data = extract_chapter_data(pdf_path)
    title_fixes = fix_chapter_titles(lines, chapter_data)
    covers_fixes = fix_chapter_covers(lines, chapter_data)
    print(f"  Title fixes: {title_fixes}")
    print(f"  Covers fixes: {covers_fixes}")
    _write_back(md_path, lines, apply)


def run_pseudo(pdf_path, md_path, apply, lines):
    """gaps 阶段：伪标题/损坏标题（对应 §3 gaps: B-pseudo）。"""
    print("=== [fix_pseudo_headings] ===")
    stats = {
        "macOS callout 重建": fix_macos_callout(lines),
        "附录 B/C/D 标题": fix_appendix_headings(lines),
        "Figure E.1 碎片清理": fix_figure_e1_leak(lines),
        "输出块围栏化": wrap_leaked_output_block(lines),
        "评估方法区重建": fix_evaluation_section(lines),
        "Exercise 7.2 空壳删除": fix_exercise_72_dup(lines),
        "封底清理": cut_back_cover(lines),
    }
    for k, v in stats.items():
        print(f"  {k}: {v}")
    _write_back(md_path, lines, apply)


def run_round2(pdf_path, md_path, apply, lines):
    """round2 格式一致性 + 章节封面（对应 §3 round2: B-format, B-covers）。"""
    print("=== [fix_format_consistency] ===")
    stats_fc = {
        "Chapter 1 标题": fix_chapter1(lines),
        "附录 B Chapter 降级": fix_appendix_chapter_dups(lines),
        "前置标题大写": fix_front_matter_caps(lines),
        "Exercise 级别": fix_exercise_levels(lines),
        "附录 Listing 加粗/去重": fix_appendix_listings(lines),
        "E.2 标题拆出": fix_e2_heading(lines),
        "Figure E caption 加粗": fix_figure_e_captions(lines),
        "断词合并": fix_hyphenation(lines),
        "bullets/控制字符": fix_bullets_control(lines),
    }
    # 清理由置空产生的连续空行
    out, prev_blank = [], False
    for l in lines:
        blank = l.strip() == ""
        if blank and prev_blank:
            continue
        out.append(l)
        prev_blank = blank
    lines[:] = out
    for k, v in stats_fc.items():
        print(f"  {k}: {v}")

    print("=== [fix_chapter_covers] ===")
    stats_cc = {
        "附录B Chapter1 恢复": fix_appendix_b_ch1(lines),
        "第1章标题插入": fix_chapter1_heading(lines),
        "covers 块标准化": normalize_covers(lines),
    }
    for k, v in stats_cc.items():
        print(f"  {k}: {v}")
    _write_back(md_path, lines, apply)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    here = Path(__file__).resolve().parent
    ap.add_argument("--md", default=str(here / "llms-from-scratch.md"))
    ap.add_argument("--pdf", default=None)
    ap.add_argument("--apply", action="store_true",
                    help="Apply fixes (default is dry-run preview).")
    ap.add_argument("--phase", choices=["round1", "pseudo", "round2", "all"],
                    default="all",
                    help="Which phase to run (default: all in §3 order). "
                         "round1=headings+chapters; pseudo=gaps pseudo-headings; "
                         "round2=format+covers.")
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

    apply = args.apply
    print(f"[info] PDF : {pdf_path}")
    print(f"[info] MD  : {md_path}  (apply={apply}, phase={args.phase})")
    if not apply:
        print("--- DRY RUN (use --apply to write) ---\n")

    if args.phase in ("round1", "all"):
        lines = _load_lines(md_path, apply)
        run_round1(pdf_path, md_path, apply, lines)

    if args.phase in ("pseudo", "all"):
        lines = _load_lines(md_path, apply)
        run_pseudo(pdf_path, md_path, apply, lines)

    if args.phase in ("round2", "all"):
        lines = _load_lines(md_path, apply)
        run_round2(pdf_path, md_path, apply, lines)

    # --- 统一自检（仅在 all 时执行完整断言）---
    if args.phase == "all":
        lines = _load_lines(md_path, apply)
        outside = _outside_fence(lines)
        assert not any(l.startswith("### Instruction") for l in outside), "Instruction 伪标题残留"
        assert not any(l.startswith("### W d") for l in outside), "W d 伪标题残留"
        assert not any(".bz/EZJR" in l for l in lines), "损坏标题残留"
        fences = [l for l in lines if l.strip().startswith("```")]
        assert len(fences) % 2 == 0, "围栏不再成对"
        text = "\n".join(lines)
        assert "\x07" not in text
        assert "• " not in text
        assert "## 1 Understanding large language models" in text
        assert "> ### High-level explanations" not in text
        assert text.count("**This chapter covers**") == 7
        print("\nfix_structure 完成，自检通过。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
