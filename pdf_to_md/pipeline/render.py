"""pipeline.render — 块 → Markdown（从 p2_clean / fix_line_continuity / rebuild_toc / p3_toc 原样搬运）。

渲染顺序（pdf-to-md-plan.md「文本转换规则速查」）：
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
IMG_MD_LINE_RE = re.compile(r"^!\[[^\]]*\]\([^)]+\)$")
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


# 块级合成已插入的图（供兜底 pass 判定缺口；render 每次调用前重置）
_INSERTED_FIGS: set = set()


def insert_figures(lines: list, fig_map: dict) -> list:
    """兜底图链插入：仅为块级合成未覆盖的 fig 补插。

    主路径是 _block_to_lines 的 figure_caption 块级合成——caption 块本身
    就是精确锚点。本兜底只处理 manifest 有图但全书无 figure_caption 块
    的缺口 fig；锚定行要求斜体图注形态且排除正文引用句动词续行
    （shows/illustrates/...），防 'Figure X.Y shows…' 正文段误锚
    （L3219 型错插事故：图链被拉到引用段前、真图注孤悬）。"""
    prose_ref = re.compile(
        r"^\*{0,2}Figure [\w.]+\*{0,2}\s+(shows|illustrates|plots|graphs|"
        r"depicts|summarizes|displays|presents|outlines|demonstrates|"
        r"compares|visualizes|captures)\b", re.I)
    out = []
    for line in lines:
        m = re.match(r"^\*{0,2}Figure ([\w.]+)\b", line)
        if m and m.group(1) in fig_map \
                and m.group(1) not in _INSERTED_FIGS \
                and not prose_ref.match(line):
            rel = fig_map[m.group(1)]
            out.append(f"![Fig {m.group(1)}](extracted_images/{rel})")
            _INSERTED_FIGS.add(m.group(1))
        out.append(line)
    return out


def fix_image_hard_breaks(lines: list) -> list:
    """图片行后紧跟非空行时补两个空格硬换行（GFM 语义）。

    无硬换行时 `![Fig]` 与下一行（图注/正文）在 CommonMark 下并段渲染成一行。
    必须在 build(TOC) 之后调用，避免被后续 pass 的 rstrip 抹掉。
    对应 md_lint R1；见 attachments/md-lint.md。
    """
    out = []
    for i, line in enumerate(lines):
        nxt = lines[i + 1] if i + 1 < len(lines) else ""
        if IMG_MD_LINE_RE.match(line) and nxt.strip():
            out.append(line.rstrip() + "  ")
        else:
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


# concept_boxes_to_blockquote 已删除：PUA bullet 在上游各 kind 渲染时消净
# （callout 剥离、concept_box bodies 排除、bullet 去 Wingdings），此行级
# 兜底为旧链遗迹；A/B 禁用后输出逐字节一致（2025-08 审计）。

def bold_captions(lines: list) -> list:
    out = []
    for line in lines:
        m = CAP_RE.match(line)
        if m:
            kind, num, rest = m.group(1), m.group(2), m.group(3)
            first_word = rest.split(" ", 1)[0]
            if first_word and first_word[0].islower():
                out.append(line)
            elif kind == "Figure":
                # 图注斜体（出版排版惯例，区别于正文；Table/Listing 保持加粗）
                out.append(f"*{kind} {num}* {rest}")
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


def merge_fragmented_inline_code(lines: list, mask: list) -> list:
    """相邻行内代码片段合并：`a` `b` `c` → `a b c`（迭代至不动点，整链合一）。

    PDF 把 Courier 引文按词/符号切 span，逐 span 包反引号产生逐词碎片
    （如 `"Every` `effort` `moves"`、`context_vec` `=` `attn_weights`、
    `[2,` `4,` `50257]`、断词残段 `num_` `tokens`）。围栏内不处理；
    引用块剥前缀后处理再还原。"""
    BT = chr(96)
    pair = re.compile(BT + "([^" + BT + "]+)" + BT + " " + BT +
                      "([^" + BT + "]+)" + BT)
    out = []
    for line, m in zip(lines, mask):
        if m:
            out.append(line)
            continue
        s = line
        prefix = ""
        core = s.lstrip()
        if core.startswith(">"):
            lead = core[1:]
            prefix = s[:len(s) - len(core)] + ">" + lead[:len(lead) - len(lead.lstrip())]
            s = lead.lstrip()
            if not s:
                out.append(line)
                continue
        while True:
            new = pair.sub(BT + "\\1 \\2" + BT, s)
            if new == s:
                break
            s = new
        out.append(prefix + s)
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
    # 悬挂连字符守卫："X- and Y-" 并列悬挂式是合法原样，不是断词
    # （与 merge._dehyphen_replace 同规则；time- and / GPT- and / CPU- and）
    if m.group(2).lower() in ("and", "or", "nor", "to"):
        return m.group(0)
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
def _block_to_lines(block, fig_map=None) -> list:
    """把单个 Block 渲染为 markdown 行（数学清洗/加粗仍由后续 pass 承担；
    figure_caption 的图链在此块级合成——caption 块即精确锚点）。"""
    k = block.kind
    t = block.text.strip()

    if block.meta.get("showcase"):
        lines = []
        if block.meta.get("showcase_first"):
            lines.append("```text")
        for ln in t.split("\n"):
            if ln.strip():
                lines.append(ln)
        if block.meta.get("showcase_last"):
            lines += ["```", ""]
        return lines

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
        for idx, body in enumerate(bodies):
            if lines:
                lines.append(">")  # 段间以 ">" 延续引用（整框单一视觉块，不碎裂）
            # typed body：框内代码 → GFM 引用内围栏（空行用 ">" 保持盒子连续）
            if isinstance(body, dict) and body.get("t") == "code":
                lang = body.get("lang") or "python"
                lines.append(f"> ```{lang}")
                for cl in body["v"].split("\n"):
                    lines.append(f"> {cl}" if cl.strip() else ">")
                lines.append("> ```")
            else:
                v = body["v"] if isinstance(body, dict) else body
                for sub in v.split("\n"):
                    lines.append(f"> {sub}" if sub.strip() else ">")
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
        # 单一练习框：标题行加粗；同框多段（merge_box_fragments 吸收的
        # 练习正文）以 ">" 分隔延续同一引用块。行级 format_exercises 只处理
        # 裸 "^Exercise N.N" 行（补丁注入内容），对 "> " 开头的渲染结果不生效。
        parts = [p for p in (x.strip() for x in t.split("\n")) if p]
        head = parts[0] if parts else t.strip()
        lines = [f"> **{head}**"]
        for p in parts[1:]:
            lines.append(">")
            for sub in p.split("\n"):
                lines.append(f"> {sub}" if sub.strip() else ">")
        lines.append("")
        return lines

    if k == "listing_caption":
        # 搬运旧链路径：p1 输出普通行，p2 bold_captions 的 CAP_RE 统一加粗；
        # 附录 A-E 行由 patches.fix_appendix_listings 兜底加粗。
        full = block.meta.get("full", t)
        return [f"{full}", ""]

    if k == "figure_caption":
        # 折叠同块多行为单行（等价旧链 p1 段落合并；bold_captions 依赖单行匹配）
        t = " ".join(t.split("\n")).strip()
        out = []
        m = re.match(r"^Figure ([\w.]+)\b", t)
        if fig_map and m and m.group(1) in fig_map \
                and m.group(1) not in _INSERTED_FIGS:
            rel = fig_map[m.group(1)]
            # 图+图注合成 <figure>，图注用 <figcaption> 区别于正文
            # （不再发独立 ![Fig] 行 + 斜体 lead；bold_captions 仅管 Table/Listing）
            esc = t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            out.append("<figure>")
            out.append(f'<img src="extracted_images/{rel}" alt="Fig {m.group(1)}">')
            out.append(f"<figcaption>{esc}</figcaption>")
            out.append("</figure>")
            out.append("")
            _INSERTED_FIGS.add(m.group(1))
        else:
            out += [f"{t}", ""]
        return out

    if k == "summary_list":
        # 章末 Summary：Wingdings 圆点列表 → Markdown 无序列表（merge_summary_items
        # 已把折行续块并入 items；一级 "- "，二级两个空格缩进 "  - "）。
        # 不再走旧链 callout 的 "> " 引用块语义。
        items = block.meta.get("items") or [[1, t.replace("\uf0a1", "").strip()]]
        lines = []
        for lvl, txt in items:
            pad = "" if lvl == 1 else "  "
            lines.append(f"{pad}- {txt}")
        lines.append("")
        return lines

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


def fix_blockquote_continuations(lines: list) -> list:
    """修复引用框（callout/concept_box/note/exercise 等）因 PDF 折行导致的续行
    掉出 `>` 块：某行非空白、非 `>`（排除代码围栏内 `>>` 与围栏行），其上下最近
    非空白行均为 `>` 续行、且上行 `>` 未以句末标点结束（本行是上一句续写）→
    该行重加 `>` 前缀并吞掉两侧空行。确定性、不误伤独立段落（独立段落前的 `>`
    行以句末标点收尾，不会触发）。覆盖 concept_box/exercise 多行体拆分之外的
    折行续行（如 callout 引用 URL 折到次行、参考文献描述句大写开头）。"""
    TERM = set(".!?:;)])\"’'”")
    n = len(lines)
    infence = [False] * n
    f = False
    for j, l in enumerate(lines):
        if l.strip().startswith("```"):
            f = not f
            infence[j] = True
            continue
        infence[j] = f
    out = []
    i = 0
    while i < n:
        line = lines[i]
        s = line.lstrip()
        if (not infence[i] and line.strip()
                and not s.startswith(">")
                and not s.startswith(">>")
                and not s.startswith("```")):
            u = i - 1
            while u >= 0 and lines[u].strip() == "":
                u -= 1
            d = i + 1
            while d < n and lines[d].strip() == "":
                d += 1
            if (u >= 0 and d < n
                    and lines[u].lstrip().startswith(">")
                    and not lines[u].lstrip().startswith(">>")
                    and lines[d].lstrip().startswith(">")
                    and not lines[d].lstrip().startswith(">>")):
                up = lines[u].lstrip()[1:].strip()
                # 实体结尾非断句：URL（附录 B 文献签名）、数字（ISBN/年份）、
                # 连字符断词残留——其后的独立段落不得吞入引用块
                if up and up[-1] not in TERM \
                        and not re.search(r"https?://\S+$", up) \
                        and not up[-1].isdigit() \
                        and not up.endswith("-"):
                    while out and out[-1].strip() == "":
                        out.pop()
                    out.append(f"> {line.strip()}")
                    i += 1
                    while i < n and lines[i].strip() == "":
                        i += 1
                    continue
        out.append(line)
        i += 1
    return out


def render(blocks: list, manifest_path) -> str:
    fig_map = load_figure_map(str(manifest_path))
    _INSERTED_FIGS.clear()
    # 步骤 1：Block → 行（figure_caption 块级合成图链）
    lines = []
    for b in blocks:
        lines.extend(_block_to_lines(b, fig_map))

    # 连续引用块拼接：同一 callout 被切成多个 IR 块时，块间裸空行会终止
    # blockquote，使引用框断裂（如 "Information leakage" 被分成标题块与续写块）。
    # 块间裸空行若上下均为 '>' 续行、且下一行非新标题 callout（'> **…'），
    # 补 '>' 保持单一引用块连续；下一行是 '> **Title**' 则保持为独立引用框。
    merged = []
    for i, line in enumerate(lines):
        if (line == "" and i > 0 and i + 1 < len(lines)
                and lines[i - 1].lstrip().startswith(">")
                and lines[i + 1].lstrip().startswith(">")
                and not lines[i + 1].lstrip().startswith("> **")):
            merged.append(">")
        else:
            merged.append(line)
    lines = merged

    # 步骤 1.5：断词处理（搬运 p2_clean 第 351-352 行：行级、不跳围栏；
    # 随后 fix_structure.fix_hyphenation：仅围栏外。顺序等价旧链）
    lines = [dehyphenate_text(l) for l in lines]
    lines = fix_hyphenation(lines)
    # 行内 bullet 符号归一（搬运 fix_structure.fix_bullets_control）
    lines = fix_bullets_control(lines)

    # 步骤 2：图链接兜底（仅补块级合成未覆盖的缺口 fig；斜体形态锚定
    # 且排除正文引用句动词续行，见 insert_figures docstring）
    lines = insert_figures(lines, fig_map)
    lines = dedupe_figures(lines)

    # 步骤 3：数学清洗
    lines = [fix_math(l) for l in lines]
    lines = [fix_text_noise(l) for l in lines]

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
    # 步骤 6.4：相邻行内代码片段合并（PDF 逐词 span 碎片 → 单一代码段）
    lines = merge_fragmented_inline_code(lines, fence_mask(lines))

    # 步骤 6.5：内容锚点补丁（搬运 fix_missing_sections.py 全部 pass）
    lines = apply_missing_sections(lines)

    # 步骤 6.6：伪标题/损坏区修复（搬运 fix_structure.run_pseudo；
    # 原封底裁剪已并入步骤 6.7 索引截断）
    lines = apply_pseudo_patches(lines)

    # 步骤 6.7：索引区截断（自 Index 标题起不入 MD；原三栏重排已废弃，
    # 见 attachments/format-fix-round2.md 子方案 2 的历史记录）
    from pipeline.index import drop_index
    lines, _ = drop_index(lines)

    # 步骤 6.8：附录 E 图渲染与链接插入（搬运 render_appendix_figures.py）
    lines = insert_appendix_figure_links(lines)

    # 步骤 6.9：格式一致性收尾（搬运 fix_structure.run_round2 相关 pass）
    lines = apply_format_patches(lines)

    # 步骤 6.10：等价旧链的落盘/重读边界——把 pass 内嵌入的 "\n" 拆成独立行
    # （旧链各脚本以 write_text + split("\n") 交接，行列表元素内不会保留换行）
    lines = [sub for l in lines for sub in l.split("\n")]

    # 步骤 6.11：引用框折行续行修复（掉出 > 块的裸行重加前缀）
    lines = fix_blockquote_continuations(lines)

    # 步骤 7：TOC + 锚点
    # 使用 rebuild_toc.build（文档顺序，含附录上下文）
    lines = build(lines)

    # 步骤 7.5：图片行硬换行（GFM；TOC 之后执行防被 rstrip 抹掉）
    lines = fix_image_hard_breaks(lines)

    text = "\n".join(lines)

    # 步骤 8：全局断言
    run_global_asserts(text)
    return text
