"""pipeline.patches — 内容锚点行级补丁（从旧脚本原样搬运）。

搬运来源：
- fix_missing_sections.py：§5.4 / §7.4 标题缺失、残段与丢失代码块（全部）。
- fix_structure.py 的 run_pseudo / run_round2 相关 pass：
  macOS callout 重建、附录 B/C/D 标题、Figure E.1 泄漏清理、输出块围栏化、
  §7.8 评估方法区重建、Exercise 7.2 空壳删除、附录 Listing 加粗、
  E.2 标题拆出、附录 B Chapter1 恢复、第1章标题插入。
  （封底裁剪 cut_back_cover 已删：索引区截断（render 步骤 6.7）覆盖其全部
  输出，A/B monkeypatch identity → 全量渲染 MD 哈希不变，2026-08。）
  附录图现走 P0 主通道（P0 validate+render），FigE 特例链已删（2026-08）。

所有补丁按内容特征定位、幂等可重跑；逻辑与阈值原样保留。
"""

import json
import re
from pathlib import Path

import pymupdf

from mdlib import config

ANCHOR_RE = re.compile(r'^<a id="[^"]+"></a>$')

# 内容重建数据与锚点逻辑分离：文案在 patches_data.json（含 PDF 来源页注释），
# 定位/替换逻辑在本文件。修正重建内容只改数据文件。
_DATA_PATH = Path(__file__).resolve().parent / "patches_data.json"
with open(_DATA_PATH, encoding="utf-8") as _f:
    PATCHES_DATA = json.load(_f)


# ===========================================================================
# 搬运自 fix_missing_sections.py
# ===========================================================================
# §7.4 开头段与丢失的 device 初始化代码块：文案见 patches_data.json
# （依据 PDF 物理页 245/246 原文重建，行内代码已加反引号）。
SECTION_74_OPENING = PATCHES_DATA["section_74_opening"]
DEVICE_CODE = list(PATCHES_DATA["device_code"])

# 截图式代码图（Fig 3.9/3.16/5.7/6.5/6.6）：书版把代码只画进矢量图、
# 文本层无对应文字，正文引导语悬空。从 PNG 逐字转录重建（2025-08）。
FIGURE_CODE_REBUILDS = PATCHES_DATA["figure_code_rebuilds"]

# 截图式/绘制式数学公式重建：书版把展示型公式只画进矢量图（文本层无对应
# 文字，正文以 "…:" / "approximation" 等引导语悬空），与 figure_code_rebuilds
# 同理，从 PDF 物理内容逐字转录 LaTeX 注入锚点后。display=true 用 $$…$$
# 块级公式；inline 用 $…$ 行内公式（由 fix_equation_gaps 按锚点就地包裹）。
EQUATION_REBUILDS = PATCHES_DATA.get("equation_rebuilds", [])


def fix_figure_code_gaps(lines: list) -> int:
    """截图式代码图的内容重建：在引导语锚点行后插入图内独有代码围栏
    （可选 output 段：代码运行输出一并重建）。幂等：锚点后下一非空行
    已是围栏则跳过；锚点须全书唯一（计数守卫）。"""
    n = 0
    for spec in FIGURE_CODE_REBUILDS:
        anchor, code = spec["anchor"], spec["code"]
        hits = [i for i, l in enumerate(lines) if anchor in l]
        if len(hits) != 1:
            continue  # 锚点不唯一/缺失：放弃（不误插）
        i = hits[0]
        j = i + 1
        while j < len(lines) and not lines[j].strip():
            j += 1
        if j < len(lines) and lines[j].strip().startswith("```"):
            continue  # 已重建
        insert = ["", "```python"] + code + ["```"]
        if spec.get("output"):
            insert += ["", "```text"] + spec["output"] + ["```"]
        lines[i + 1:i + 1] = insert
        n += 1
    return n


def fix_equation_gaps(lines: list) -> int:
    """绘制式/截图式数学公式重建（同 figure_code_rebuilds 思路）。书版把展示型
    公式只画进矢量图，文本层无对应文字，正文以引导语悬空。display=true 在
    锚点行后插入块级 $$…$$；display=false 把锚点行内公式片段就地包成 $…$。
    幂等：已存在相同 tex 则跳过；锚点全书唯一才插（守卫）。"""
    n = 0
    for spec in EQUATION_REBUILDS:
        anchor, tex = spec["anchor"], spec["tex"]
        disp = spec.get("display", False)
        hits = [i for i, l in enumerate(lines) if anchor in l]
        if len(hits) != 1:
            continue  # 锚点不唯一/缺失：放弃（不误插）
        i = hits[0]
        if disp:
            block = ["", "$$" + tex + "$$", ""]
            j = i + 1
            while j < len(lines) and not lines[j].strip():
                j += 1
            if j < len(lines) and ("$$" + tex + "$$") in lines[j]:
                continue  # 已重建
            lines[i + 1:i + 1] = block
            n += 1
        else:
            line = lines[i]
            if ("$" + tex + "$") in line:
                continue  # 已重建
            if tex in line:
                lines[i] = line.replace(tex, "$" + tex + "$")
                n += 1
    return n


def fix_54_heading(lines: list) -> int:
    """在 §5.4 首段前插入缺失的节标题；返回插入次数。"""
    if any(l.startswith("### 5.4 ") for l in lines):
        return 0
    anchor = "Thus far, we have discussed how to numerically evaluate the training progress"
    for i, l in enumerate(lines):
        if l.startswith(anchor):
            lines[i:i] = ["### 5.4 Loading and saving model weights in PyTorch", ""]
            return 1
    return 0


def fix_54_hyphenation(lines: list) -> int:
    """合并 §5.4 区残留的断词（P1 跨行拆字未清干净）。"""
    n = 0
    for i, l in enumerate(lines):
        new = re.sub(r"(pre|fig)- (train|ure)", r"\1\2", l)
        if new != l:
            lines[i] = new
            n += 1
    return n


def fix_74_heading(lines: list) -> int:
    """在 §7.4 首段前插入缺失的节标题；返回插入次数。"""
    if any(l.startswith("### 7.4 ") for l in lines):
        return 0
    anchor = "As of this writing, researchers are divided on whether masking"
    for i, l in enumerate(lines):
        if l.startswith(anchor):
            lines[i:i] = ["### 7.4 Creating data loaders for an instruction dataset", ""]
            return 1
    return 0


def fix_74_opening(lines: list) -> int:
    """用完整开头段替换残留碎片 `custom_collate_fn` function for the instruction dataset.。"""
    fragment = "`custom_collate_fn` function for the instruction dataset. As shown in figure 7.14"
    for i, l in enumerate(lines):
        if l.startswith(fragment):
            lines[i] = SECTION_74_OPENING
            return 1
    return 0


def fix_74_device_code(lines: list) -> int:
    """在 device 引导句后补入丢失的初始化代码块。"""
    lead = "The following code initializes the `device` variable:"
    for i, l in enumerate(lines):
        if l.startswith(lead):
            # 幂等：后面已是代码块则跳过
            j = i + 1
            while j < len(lines) and lines[j].strip() == "":
                j += 1
            if j < len(lines) and lines[j].strip().startswith("```"):
                return 0
            lines[i + 1:i + 1] = [""] + DEVICE_CODE
            return 1
    return 0


def apply_missing_sections(lines: list) -> list:
    """搬运 fix_missing_sections.main 的 pass 序列。"""
    fix_54_heading(lines)
    fix_54_hyphenation(lines)
    fix_74_heading(lines)
    fix_74_opening(lines)
    fix_74_device_code(lines)
    fix_figure_code_gaps(lines)
    fix_equation_gaps(lines)
    return lines


# ===========================================================================
# 搬运自 fix_structure.py（run_pseudo 伪标题/损坏区 pass）
# ===========================================================================
# --- macOS callout 重建（依据 PDF 物理页 304 原文；文案见 patches_data.json） ---
MACOS_CALLOUT = list(PATCHES_DATA["macos_callout"])


def fix_macos_callout(lines: list) -> int:
    """重建被切断/缺内联代码的 PyTorch on macOS 引用块。

    覆盖两种病史形态：
    - 旧链伪标题形态：引用块后跟 '### to to take advantage of this chip'
    - 框碎片合并形态（merge_box_fragments）：连续引用块内缺两个 device
      内联代码跨度（Courier 片段在提取期丢失，正文残留双 'to'）
    幂等：引用块已含 mps 代码跨度则跳过。"""
    start = next((i for i, l in enumerate(lines)
                  if l.strip() == "> **PyTorch on macOS**"), None)
    if start is None:
        return 0
    end = start + 1
    while end < len(lines) and lines[end].lstrip().startswith(">"):
        end += 1
    segment = lines[start:end]
    if any('torch.backends.mps.is_available' in l for l in segment):
        return 0  # 已是完整版（含内联代码），幂等跳过
    lines[start:end] = list(MACOS_CALLOUT)
    # 旧链伪标题残留（紧跟空行后）一并删除
    j = end
    while j < len(lines) and lines[j].strip() == "":
        j += 1
    if j < len(lines) and lines[j].startswith("### to to take advantage"):
        del lines[end:j + 1]
    return 1


def fix_appendix_headings(lines: list) -> int:
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


def fix_figure_e1_leak(lines: list) -> int:
    """删除 Figure E.1 图内文字泄漏碎片（孤立 ΔW/W/d 行与 '### W d' 伪标题）。
    仅在 'Figure E.1 illustrates' 到 caption 之间的窗口内清理，避免误删。"""
    start = next((i for i, l in enumerate(lines)
                  if l.startswith("Figure E.1 illustrates")), None)
    end = next((i for i, l in enumerate(lines)
                if l.strip().startswith(("*Figure E.1", "**Figure E.1", "Figure E.1 A comparison"))), None)
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


def _outside_fence(lines: list) -> list:
    out, in_f = [], False
    for l in lines:
        if l.strip().startswith("```"):
            in_f = not in_f
            continue
        if not in_f:
            out.append(l)
    return out


def wrap_leaked_output_block(lines: list) -> int:
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


def fix_evaluation_section(lines: list) -> int:
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
        *PATCHES_DATA["evaluation_bullets"],
        "",
        PATCHES_DATA["evaluation_in_practice"],
        "",
        f"> {PATCHES_DATA['conversational_quote_title']}",
        ">",
        f"> {PATCHES_DATA['conversational_quote_body']}",
    ]
    lines[s:e + 1] = rebuilt
    return 1


def fix_exercise_72_dup(lines: list) -> int:
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


def apply_pseudo_patches(lines: list) -> list:
    """搬运 fix_structure.run_pseudo 的 pass 序列（顺序一致）。
    （原 cut_back_cover 已删：输出被索引截断完全覆盖，A/B 哈希不变。
     原 wrap_leaked_output_block 已由 classify._mark_showcase_groups +
     render 围栏渲染结构化接管——覆盖同区 3 组并新增 p265/266 评分
     输出 2 组；且不再剥 '### ' 前缀，围栏内容恢复 PDF 原文。）"""
    fix_macos_callout(lines)
    fix_appendix_headings(lines)
    fix_figure_e1_leak(lines)
    fix_evaluation_section(lines)
    fix_exercise_72_dup(lines)
    return lines


# ===========================================================================
# 搬运自 fix_structure.py（run_round2 格式一致性相关 pass）
# ===========================================================================
def fix_exercise_levels(lines: list) -> int:
    """搬运 fix_structure.fix_exercise_levels：附录习题标题 #### → ###。"""
    n = 0
    for i, l in enumerate(lines):
        if re.match(r"^#### Exercise [A-E]\.\d", l):
            lines[i] = "###" + l[4:]
            n += 1
    return n


def fix_appendix_listings(lines: list) -> int:
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


def fix_e2_heading(lines: list) -> int:
    for i, l in enumerate(lines):
        if l.startswith("E.2 Preparing the dataset Before applying LoRA"):
            lines[i] = "#### E.2 Preparing the dataset\n\n" + l[len("E.2 Preparing the dataset"):].strip()
            return 1
    return 0



def fix_appendix_chapter_dups(lines: list) -> int:
    """附录 B（References）内的 `## Chapter N` 是文献分组小节，降为 `###`；
    附录 C（Exercise solutions）内的 `## Chapter N` 是习题解答主节，保留 `##`。"""
    n = 0
    b_start = next((i for i, l in enumerate(lines)
                    if l.startswith("## Appendix B References")), None)
    c_start = next((i for i, l in enumerate(lines)
                    if l.startswith("## Appendix C Exercise solutions")), None)
    if b_start is None:
        return 0
    b_end = c_start if c_start is not None else len(lines)
    for i in range(b_start, b_end):
        if re.match(r"^## Chapter \d+", lines[i]):
            lines[i] = "#" + lines[i]
            n += 1
    return n


def fix_appendix_b_ch1(lines: list) -> int:
    for i, l in enumerate(lines):
        if l.strip() == "## Chapter 1 Understanding large language models" and i > len(lines) // 2:
            lines[i] = "### Chapter 1"
            return 1
    return 0


def fix_chapter1_heading(lines: list) -> int:
    if any(l.strip() == "## 1 Understanding large language models" for l in lines):
        return 0
    for i, l in enumerate(lines):
        if l.strip() in ("This chapter covers", "**This chapter covers**"):
            lines[i:i] = ["## 1 Understanding large language models", ""]
            return 1
    return 0


def fix_table_1_1(lines: list) -> int:
    """全书唯一表格 Table 1.1 转为 Markdown 管道表。

    内容逐字核对自 PDF 物理页 33（5 个数据行，无遗漏）。
    自动提取（find_tables）会把多行并成一行造成事实损坏，故采用
    内容锚点的一次性重建（与 fix_evaluation_section 同先例）。幂等可重跑。
    """
    if any(l.startswith("| Dataset name |") for l in lines):
        return 0
    t11 = PATCHES_DATA["table_1_1"]
    s = next((i for i, l in enumerate(lines)
              if l.strip() == t11["first_row_anchor"]), None)
    if s is None:
        return 0
    e = next((i for i, l in enumerate(lines)
              if l.strip() == t11["last_row_anchor"]), None)
    if e is None or e < s:
        return 0
    lines[s:e + 1] = list(t11["lines"])
    return 1


def fix_hardware_runtime_table(lines: list) -> int:
    """ch7 概念框内无格线参考表（表头 'Model name/Device/Run time' +
    6 数据行）→ 引用块内 GFM 管道表。

    该表无网格线（区别于 Table 1.1），PDF 以每视觉行一个块提取、
    块内 \n 分隔各列；被 concept_box 吸收后压成单段长文。本 pass 按
    结构信号全书定位（页内 ≥4 个等高多列短块且首块含 'Model name'），
    列数据运行时从 PDF 提取，无硬编码内容。幂等可重跑。"""
    if any(l.startswith("> | Model name |") for l in lines):
        return 0
    anchor = next((i for i, l in enumerate(lines)
                   if l.startswith("> Model name Device Run time")
                   and "gpt2-medium" in l), None)
    if anchor is None:
        return 0
    doc = pymupdf.open(str(config.PDF_PATH))
    rows = None
    for page in doc:
        blocks = []
        for b in page.get_text("dict")["blocks"]:
            if b["type"] != 0:
                continue
            h = b["bbox"][3] - b["bbox"][1]
            txts = ["".join(s["text"] for s in ln["spans"]).strip()
                    for ln in b.get("lines", [])]
            txts = [t for t in txts if t]
            if h < 12 and len(txts) >= 2 and all(len(t) < 60 for t in txts):
                blocks.append((b["bbox"][1], txts))
        blocks.sort(key=lambda t: t[0])
        # 连续等距(17±2pt)行链 ≥5 且首行含 'Model name'
        run = []
        prev = None
        chains = []
        for y, txts in blocks:
            if prev is not None and abs((y - prev) - 17.0) <= 3.0:
                run.append(txts)
            else:
                if len(run) >= 5:
                    chains.append(run)
                run = [txts]
            prev = y
        if len(run) >= 5:
            chains.append(run)
        for ch in chains:
            if ch[0] and ch[0][0] == "Model name":
                rows = ch
                break
        if rows:
            break
    doc.close()
    if not rows:
        return 0
    header = rows[0]
    gfm = ["> | " + " | ".join(header) + " |",
           "> |" + "---|" * len(header)]
    for r in rows[1:]:
        gfm.append("> | " + " | ".join(r) + " |")
    lines[anchor:anchor + 1] = gfm
    return 1


def fix_duplicate_chapter_headings(lines: list) -> int:
    """章级标题全局去重：`## <数字> 标题` 全书只应出现一次（TOC 目录项是
    链接不是标题）。重复源于 classify 的 TOC 别名前缀回退同时命中真标题
    残片与 bullet 折行残片（如第 7 章的 'fine-tuning'）。保留首个，
    后续重复行置空；若其紧邻上一行为锚点则一并删除。幂等可重跑。"""
    n = 0
    seen = set()
    i = 0
    while i < len(lines):
        m = re.match(r"^## (\d+\s.+)$", lines[i])
        if m:
            key = m.group(1)
            if key in seen:
                if i > 0 and lines[i - 1].startswith("<a id="):
                    lines[i - 1] = ""
                lines[i] = ""
                n += 1
                i += 1
                continue
            seen.add(key)
        i += 1
    return n


_BARE_TOKEN_RE = re.compile(r"<\|[^|<>]+\|>")


def fix_url_internal_spaces(lines: list) -> int:
    """URL 中段空格修复（附录 B 文献区高发，全书实测 20 处）。三类形态：
      ①斜杠后空格   books/ build-a-…   -> books/build-a-…
      ②域名点前空格  wikisource .org    -> wikisource.org
      ③编号内空格    2405 .01535        -> 2405.01535
    仅处理含 http 的围栏外行；幂等可重跑。"""
    n = 0
    in_f = False
    for i, l in enumerate(lines):
        s = l.strip()
        if s.startswith("```"):
            in_f = not in_f
            continue
        if in_f or "http" not in l:
            continue
        orig = l
        l = re.sub(r"(https?://\S*/)[ \t]+(?=[A-Za-z0-9_\-.#/])", r"\1", l)
        l = re.sub(r"([A-Za-z0-9])[ \t]+(\.(?:com|org|net|io|bz|ai|dev|edu|gov)\b)", r"\1\2", l)
        l = re.sub(r"(\d)[ \t]+(\.\d{3,})", r"\1\2", l)
        if l != orig:
            lines[i] = l
            n += 1
    return n



def fix_url_word_damage(lines: list) -> int:
    """三处 URL/词内损伤单点修复（均 golden 同错，以 PDF 原文为准）：
      live- Book → liveBook        （PDF 页20: 'live-\\nBook'，liveBook 为真词，
                                     dehyphenate 的右侧大写保留规则误判）
      LLMsfrom-scratch → LLMs-from-scratch  （旧链 JOIN_PREFIXES 过度合并）
      OpenAccess -AI-Collective → OpenAccess-AI-Collective  （span 空格注入）
    """
    fixes = [
        ("live- Book", "liveBook"),
        ("LLMsfrom-scratch", "LLMs-from-scratch"),
        ("OpenAccess -AI-Collective", "OpenAccess-AI-Collective"),
    ]
    n = 0
    in_f = False
    for i, l in enumerate(lines):
        s = l.strip()
        if s.startswith("```"):
            in_f = not in_f
            continue
        if in_f:
            continue
        for old, new in fixes:
            if old in l:
                lines[i] = l.replace(old, new)
                n += 1
                l = lines[i]
    return n


def fix_bare_special_tokens(lines: list) -> int:
    """围栏外裸露的 `<|token|>` 包反引号（与全文其余 40 处风格一致；
    当前仅索引区 2 行命中）。先剥离已有代码跨度再判定，避免误双包。"""
    codespan = re.compile(r"`[^`]*`")
    n = 0
    in_f = False
    for i, l in enumerate(lines):
        s = l.strip()
        if s.startswith("```"):
            in_f = not in_f
            continue
        if in_f or "<|" not in l:
            continue
        stripped_parts = []
        last = 0
        for m in codespan.finditer(l):
            stripped_parts.append(l[last:m.start()])
            last = m.end()
        stripped_parts.append(l[last:])
        residue = "".join(stripped_parts)
        if "<|" in residue and _BARE_TOKEN_RE.search(residue):
            # 仅对裸 token 所在跨度包反引号：逐段重建
            out = []
            last = 0
            for m in codespan.finditer(l):
                seg = l[last:m.start()]
                out.append(_BARE_TOKEN_RE.sub(lambda x: f"`{x.group(0)}`", seg))
                out.append(m.group(0))
                last = m.end()
            seg = l[last:]
            out.append(_BARE_TOKEN_RE.sub(lambda x: f"`{x.group(0)}`", seg))
            lines[i] = "".join(out)
            n += 1
    return n


def apply_format_patches(lines: list) -> list:
    """搬运 fix_structure.run_round2 中尚未由 Block 渲染覆盖的 pass（顺序一致）。"""
    fix_exercise_levels(lines)
    fix_appendix_listings(lines)
    fix_table_1_1(lines)
    fix_hardware_runtime_table(lines)
    fix_duplicate_chapter_headings(lines)
    fix_bare_special_tokens(lines)
    fix_url_word_damage(lines)
    fix_url_internal_spaces(lines)
    fix_e2_heading(lines)
    fix_appendix_chapter_dups(lines)
    fix_appendix_b_ch1(lines)
    fix_chapter1_heading(lines)
    return lines
