"""pipeline.render — 块 → Markdown（从 p2_clean / fix_line_continuity / rebuild_toc / p3_toc 原样搬运）。

渲染顺序（pdf-to-md-plan.md §3）：
1. 各 kind → 文本行（heading 加 `#`×(level+1)、code 包围栏、concept_box/note/exercise 加 `>`、
   listing_caption 加粗、figure_caption 加粗、bullet 加 `- `）；
2. 图链接插入：insert_figures + load_figure_map + dedupe_figures；
3. 数学清洗：fix_math + fix_text_noise；
4. 标题加粗：bold_captions；
5. Exercise 格式化：format_exercises；
6. 断词兜底：fix_dash_bullets + fix_inline_code_breaks；
7. TOC + 锚点：scan_headings / include_in_toc / build（rebuild_toc）
   + slugify / assign_anchors / inject_anchors_and_toc（p3_toc）；
8. 末尾 run_global_asserts(text)。

禁止搬运 p2_clean.rebalance_fences（围栏由渲染器保证成对）。
"""

import json
import re

from mdlib import config
from mdlib.asserts import run_global_asserts
from mdlib.textutil import norm
from mdlib.textutil import fence_mask
from pipeline.merge import dehyphenate_text


# ===========================================================================
# 搬运来源常量与函数（p2_clean.py / fix_line_continuity.py / rebuild_toc.py / p3_toc.py）
# ===========================================================================
PUA_MAP = config.PUA_MAP

FIG_RE = re.compile(r"^Figure (\d+\.\d+)\b")
FIG_LINE_RE = re.compile(r"^!\[Fig [^\]]+\]\(([^)]+)\)$")
CAP_RE = re.compile(r"^(Figure|Table|Listing) (\d+\.\d+) (.+)$")
EXERCISE_RE = re.compile(r"^Exercise (\d+)\.(\d+)\b\s*(.*)$")
LIST_MARKER_RE = re.compile(r"^[–•]\s+(.*)$")
FENCE_RE = re.compile(r"^\s*```")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
ANCHOR_RE = re.compile(r'^<a id="[^"]+"></a>\s*$')
FRONT_MATTER = {"Preface", "Acknowledgments", "About this book",
                "About the author", "About the cover illustration"}
# 前置标题大小写规范化（搬运自 fix_format_consistency.FRONT_MATTER_CAPS）
FRONT_MATTER_CAPS = {
    "preface": "Preface",
    "acknowledgments": "Acknowledgments",
    "about this book": "About this book",
    "about the author": "About the author",
    "about the cover illustration": "About the cover illustration",
    "livebook discussion forum": "LiveBook discussion forum",
}


def load_figure_map(manifest_path: str) -> dict:
    data = json.loads(__import__("pathlib").Path(manifest_path).read_text(encoding="utf-8"))
    fig_map = {}
    for entry in data.get("figures", []):
        fig_map[entry["fig"]] = entry["file"]
    return fig_map


def insert_figures(lines: list, fig_map: dict) -> list:
    out = []
    for line in lines:
        m = FIG_RE.match(line)
        if m and m.group(1) in fig_map:
            rel = fig_map[m.group(1)]
            out.append(f"![Fig {m.group(1)}](extracted_images/{rel})")
        out.append(line)
    return out


def dedupe_figures(lines: list) -> list:
    seen = set()
    out = []
    for line in lines:
        m = FIG_LINE_RE.match(line)
        if m:
            path = m.group(1)
            if path in seen:
                continue
            seen.add(path)
        out.append(line)
    return out


def fix_math(text: str) -> str:
    for pua, uni in PUA_MAP.items():
        text = text.replace(pua, uni)
    text = text.replace("\u22c5", "\\cdot")
    text = text.replace("\u22c5", "\\cdot")
    return text


def fix_text_noise(text: str) -> str:
    text = text.replace("M A N N I N G", "MANNING")
    text = text.replace("Dragosavljevic´", "Dragosavljević")
    text = text.replace(" -- ", " — ")
    text = re.sub(r"(\w)--(\w)", r"\1—\2", text)
    # URL scheme 后被 span 拼接注入的空格（PDF 内 URL 常因字体/kerning 拆 span）
    text = re.sub(r"(https?://)\s+", r"\1", text)
    return text


def fix_bullets_control(lines: list) -> list:
    """搬运 fix_structure.fix_bullets_control：行内 "• " → "- "，去 \\x07。"""
    out = []
    for line in lines:
        new = line.replace("• ", "- ").replace("\x07", "")
        out.append(new)
    return out


def normalize_list_markers(lines: list) -> list:
    out = []
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
            in_blockquote = False
        out.append(line)
    return out


def concept_boxes_to_blockquote(lines: list) -> list:
    out = []
    buf = []
    for line in lines:
        if "\uf0a1" in line:
            buf.append(line.replace("\uf0a1", "").strip())
        else:
            if buf:
                out.extend("> " + b for b in buf if b)
                out.append("")
                buf = []
            out.append(line)
    if buf:
        out.extend("> " + b for b in buf if b)
        out.append("")
    return out


def bold_captions(lines: list) -> list:
    out = []
    for line in lines:
        m = CAP_RE.match(line)
        if m:
            kind, num, rest = m.group(1), m.group(2), m.group(3)
            first_word = rest.split(" ", 1)[0]
            if first_word and first_word[0].islower():
                out.append(line)
            else:
                out.append(f"**{kind} {num}** {rest}")
        else:
            out.append(line)
    return out


def format_exercises(lines: list) -> list:
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


# ---- fix_line_continuity 搬运（fix_dash_bullets / fix_inline_code_breaks / 辅助） ----
TERMINAL = ".!?\"')]}" + "'”’"
INLINE_BREAK_RE = re.compile(r"`([^`]*)-`\s+`([^`]*)`")


def _ends_terminal(text: str) -> bool:
    t = text.rstrip()
    if not t:
        return True
    i = len(t) - 1
    while i >= 0 and t[i] in ")]}\"'":
        i -= 1
    return i >= 0 and t[i] in TERMINAL


def in_code_fence(mask: list, k: int) -> bool:
    return bool(mask[k]) if k < len(mask) else False


def fix_dash_bullets(lines: list, mask: list) -> list:
    fixes = 0
    out = []
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
    return out


def fix_inline_code_breaks(lines: list, mask: list) -> list:
    out = []
    for k, line in enumerate(lines):
        if in_code_fence(mask, k):
            out.append(line)
            continue
        new = INLINE_BREAK_RE.sub(lambda m: "`" + m.group(1) + m.group(2) + "`", line)
        out.append(new)
    return out


# ---- fix_structure.py 搬运（fix_hyphenation：SPLIT_RE / KEEP_HYPHEN / FIXED_WORDS） ----
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


def _join_split(m: re.Match) -> str:
    """搬运 fix_structure.py 第 838-841 行。"""
    if m.group(1) in KEEP_HYPHEN:
        return f"{m.group(1)}-{m.group(2)}"
    return m.group(1) + m.group(2)


def fix_hyphenation(lines: list) -> list:
    """搬运 fix_structure.py 第 844-854 行（仅围栏外，等价 iter_prose）。"""
    mask = fence_mask(lines)
    out = []
    for k, l in enumerate(lines):
        if mask[k]:
            out.append(l)
            continue
        new = l
        for bad, good in FIXED_WORDS.items():
            new = re.sub(rf"\b{bad}\b", good, new)
        new = SPLIT_RE.sub(_join_split, new)
        out.append(new)
    return out


# ---- TOC / 锚点（rebuild_toc.py + p3_toc.py） ----
from pipeline.patches import (
    apply_missing_sections,
    apply_pseudo_patches,
    apply_format_patches,
    insert_appendix_figure_links,
)


def slugify(text: str) -> str:
    s = text.strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-") or "section"


def scan_headings(lines: list):
    heads, in_f = [], False
    for i, line in enumerate(lines):
        if FENCE_RE.match(line):
            in_f = not in_f
            continue
        if in_f:
            continue
        m = HEADING_RE.match(line)
        if m:
            heads.append([i, len(m.group(1)), m.group(2)])
    return heads


def include_in_toc(text: str, appendix_ctx: str):
    if text in ("Table of Contents", "Summary"):
        return False, text
    if text.startswith("Hands-on projects"):
        return False, text
    if norm(text) in {norm(x) for x in FRONT_MATTER} or text == "Index":
        return True, text
    if re.match(r"^Chapter \d+", text):
        if appendix_ctx == "C":
            return True, text + " (Exercise solutions)"
        return True, text
    if re.match(r"^\d+(\.\d+)* ", text) or re.match(r"^[A-E]\.\d", text):
        return True, text
    if text.startswith("Appendix"):
        return True, text
    return False, text


def fix_appendix_e_heading(lines: list) -> int:
    """搬运 rebuild_toc.fix_appendix_e_heading：附录 E 标题为普通行（小写开头），
    升为 `## Appendix E ...`。"""
    for i, l in enumerate(lines):
        if l.strip() == "appendix E Parameter-efficient fine-tuning with LoRA":
            lines[i] = "## Appendix E Parameter-efficient fine-tuning with LoRA"
            return 1
    return 0


def fix_de_levels(lines: list) -> int:
    """搬运 rebuild_toc.fix_de_levels：D.x/E.x 小节在 ## 附录下应为 ###（与附录 A
    一致），修正 #### 跳变。"""
    n = 0
    for i, l in enumerate(lines):
        if re.match(r"^#### [DE]\.\d", l):
            lines[i] = "###" + l[4:]
            n += 1
    return n


def strip_old(lines: list) -> list:
    """搬运 rebuild_toc.strip_old：删除旧 TOC 区与所有锚点行。"""
    out = []
    in_toc = False
    for l in lines:
        if l.strip() == "## Table of Contents":
            in_toc = True
            continue
        if in_toc:
            s = l.strip()
            if s == "" or re.match(r"^\s*- \[", l):
                continue
            in_toc = False  # 遇到正文内容，保留该行
        if ANCHOR_RE.match(l):
            continue
        out.append(l)
    return out


def build(lines: list) -> list:
    """搬运 rebuild_toc.build（含 main 序列的前置 pass）。

    旧链等价说明：书名标题 "# Build a Large Language Model (From Scratch)" 由
    p3_toc 前置于文件首并经 rebuild_toc.strip_old 保留；新管道无 p3 前置步骤，
    故在此显式补上标题行后再执行 build。
    """
    # 搬运 rebuild_toc.main 前置 pass
    fix_appendix_e_heading(lines)
    fix_de_levels(lines)
    lines = strip_old(lines)
    # 等价 p3_toc 前置的书名标题（strip_old 后它应位于文件首）
    if not any(l.startswith("# Build a Large Language Model") for l in lines[:5]):
        lines = ["# Build a Large Language Model (From Scratch)"] + lines

    heads = scan_headings(lines)
    used = {}
    slugs = []
    for _, _, text in heads:
        base = slugify(text)
        used[base] = used.get(base, 0) + 1
        slugs.append(base if used[base] == 1 else f"{base}-{used[base]}")

    toc = ["## Table of Contents", ""]
    appendix_ctx = ""
    for (idx, level, text), slug in zip(heads, slugs):
        if text.startswith("Appendix"):
            m = re.match(r"^Appendix ([A-E])", text)
            appendix_ctx = m.group(1) if m else ""
        ok, display = include_in_toc(text, appendix_ctx)
        if ok:
            toc.append(f"{'  ' * max(0, level - 2)}- [{display}](#{slug})")
    toc.append("")

    head_pos = {h[0]: s for h, s in zip(heads, slugs)}
    out = []
    for i, l in enumerate(lines):
        if i in head_pos:
            out.append(f'<a id="{head_pos[i]}"></a>')
        out.append(l)
    for k, l in enumerate(out):
        if l.startswith("# "):
            return out[: k + 1] + [""] + toc + out[k + 1:]
    return toc + out


def assign_anchors(heads):
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


def inject_anchors_and_toc(lines: list, heads, anchors, toc) -> str:
    out = []
    head_idx = {h[0] for h in heads}
    for i, line in enumerate(lines):
        if i in head_idx:
            out.append(f'<a id="{anchors[i]}"></a>')
        out.append(line)
    return "\n".join(toc + out)


# ===========================================================================
# Block → 行渲染
# ===========================================================================
def _block_to_lines(block) -> list:
    """把单个 Block 渲染为 markdown 行（不含 figure 合成 / 加粗 / 数学清洗）。"""
    k = block.kind
    t = block.text.strip()

    if k == "heading":
        level = block.level  # 1/2/3
        hashes = "#" * (level + 1)
        t = " ".join(t.split("\n")).strip()  # 折叠同块多行（如 "1.1\nWhat..."）
        # TOC 条目用 em dash 分隔主副标题（如 "appendix A—Introduction..."），
        # 渲染为空格（搬运 old fix_headings 的标题规范化）。
        t = t.replace("\u2014", " ")
        # 附录标题首词大写：appendix → Appendix（搬运 old fix_headings）
        if re.match(r"^appendix\b", t, re.I):
            t = re.sub(r"^appendix\b", "Appendix", t, count=1, flags=re.I)
        tn = norm(t)
        if tn in FRONT_MATTER_CAPS:   # 前置标题大小写规范化
            t = FRONT_MATTER_CAPS[tn]
        return [f"{hashes} {t}", ""]

    if k == "code":
        lang = block.lang or "text"
        return [f"```{lang}", t, "```", ""]

    if k == "concept_box":
        title = block.meta.get("title", "")
        bodies = block.meta.get("bodies", [])
        lines = []
        if title:
            lines.append(f"> **{title}**")
            lines.append(">")
        for b in bodies:
            lines.append(f"> {b}")
        if not lines:
            lines.append(f"> {t}")
        lines.append("")
        return lines

    if k == "note":
        # 搬运 fix_notes：NOTE 独占首行，去掉后接 body 折叠成 `> **NOTE** body`
        parts = t.split("\n")
        if parts and parts[0].strip().upper() == "NOTE":
            body = " ".join(" ".join(parts[1:]).split()).strip()
        else:
            body = " ".join(t.split()).strip()
        return [f"> **NOTE** {body}", ""]

    if k == "callout":
        # 搬运旧链语义：p1 段落合并把块内可视行并为一段，p2 对含 \uf0a1 的行
        # 转 `> ` 单行引用（续行块无 PUA，保持普通段落）。
        frags = [ln.strip().replace("\uf0a1", "").strip() for ln in t.split("\n")]
        body = " ".join(f for f in frags if f)
        return [f"> {body}", ""]

    if k == "exercise":
        return [f"> **{t}**", ""]

    if k == "listing_caption":
        # 搬运旧链路径：p1 输出普通行，p2 bold_captions 的 CAP_RE 统一加粗；
        # 附录 A-E 行由 patches.fix_appendix_listings 兜底加粗。
        full = block.meta.get("full", t)
        return [f"{full}", ""]

    if k == "figure_caption":
        # 折叠同块多行为单行（等价旧链 p1 段落合并；bold_captions 依赖单行匹配）
        t = " ".join(t.split("\n")).strip()
        return [f"{t}", ""]

    if k == "bullet":
        # 去掉 Wingdings 字符
        txt = re.sub(r"[^\x20-\x7e]", "", t).strip()
        return [f"- {txt}", ""]

    if k == "index":
        return [t, ""]

    if k == "cover":
        return [f"# {t}", ""]

    # prose
    t = " ".join(t.split("\n")).strip()  # 折叠同块多行
    if norm(t) == "this chapter covers":
        return ["**This chapter covers**", ""]
    return [t, ""]


def render(blocks: list, manifest_path) -> str:
    # 步骤 1：Block → 行
    lines = []
    for b in blocks:
        lines.extend(_block_to_lines(b))

    # 步骤 1.5：断词处理（搬运 p2_clean 第 351-352 行：行级、不跳围栏；
    # 随后 fix_structure.fix_hyphenation：仅围栏外。顺序等价旧链）
    lines = [dehyphenate_text(l) for l in lines]
    lines = fix_hyphenation(lines)
    # 行内 bullet 符号归一（搬运 fix_structure.fix_bullets_control）
    lines = fix_bullets_control(lines)

    # 步骤 2：图链接合成
    fig_map = load_figure_map(str(manifest_path))
    lines = insert_figures(lines, fig_map)
    lines = dedupe_figures(lines)

    # 步骤 3：数学清洗
    lines = [fix_math(l) for l in lines]
    lines = [fix_text_noise(l) for l in lines]

    # 概念框 blockquote（PUA bullet 已无，但保留调用以兼容可能的残留）
    lines = concept_boxes_to_blockquote(lines)

    # 列表标记归一（断词后）
    lines = normalize_list_markers(lines)

    # 步骤 4：标题加粗
    lines = bold_captions(lines)

    # 步骤 5：Exercise 格式化
    lines = format_exercises(lines)

    # 步骤 6：断词兜底（基于代码围栏掩码；每个 pass 前重算掩码——
    # fix_dash_bullets 会合并行导致后续索引偏移，共享掩码会错位）
    lines = fix_dash_bullets(lines, fence_mask(lines))
    lines = fix_inline_code_breaks(lines, fence_mask(lines))

    # 步骤 6.5：内容锚点补丁（搬运 fix_missing_sections.py 全部 pass）
    lines = apply_missing_sections(lines)

    # 步骤 6.6：伪标题/损坏区修复 + 封底裁剪（搬运 fix_structure.run_pseudo）
    lines = apply_pseudo_patches(lines)

    # 步骤 6.7：索引区三栏重排（搬运 fix_index.py 整体逻辑）
    from pipeline.index import rebuild_index
    lines, _ = rebuild_index(lines)

    # 步骤 6.8：附录 E 图渲染与链接插入（搬运 render_appendix_figures.py）
    lines = insert_appendix_figure_links(lines)

    # 步骤 6.9：格式一致性收尾（搬运 fix_structure.run_round2 相关 pass）
    lines = apply_format_patches(lines)

    # 步骤 6.10：等价旧链的落盘/重读边界——把 pass 内嵌入的 "\n" 拆成独立行
    # （旧链各脚本以 write_text + split("\n") 交接，行列表元素内不会保留换行）
    lines = [sub for l in lines for sub in l.split("\n")]

    # 步骤 7：TOC + 锚点
    # 使用 rebuild_toc.build（文档顺序，含附录上下文）
    lines = build(lines)

    text = "\n".join(lines)

    # 步骤 8：全局断言
    run_global_asserts(text)
    return text
