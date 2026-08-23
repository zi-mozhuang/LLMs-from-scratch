"""pipeline.index — 索引区多栏重排（从 fix_index.py 原样搬运）。

根因（见 attachments/format-fix-round2.md 子方案 2）：
  索引页为三栏版式，P1 按行 y 坐标合并导致三栏内容交叉拼接、不可读。

方案：物理页 359–365（0 基 358–364）重新提取：
  1. 每页按行 bbox x 中点聚为 3 栏（边界 230 / 385）。
  2. 栏内按 y 排序；不以页码结尾的行视为续行并入下一条。
  3. 输出 `## Index` + 分节（Symbols/Numerics/A–Z）+ `- 术语, 页码` 列表。
  4. 整段替换 md 中 `index` 行到 `## Hands-on projects` 之间的乱码区。

幂等可重跑。
"""

import re

import pymupdf

from mdlib import config

PDF = str(config.PDF_PATH)
INDEX_PAGES = range(358, 365)  # 物理页 359–365

# 行尾页码模式：148 / 64–74 / 148, 294 / 29–31, 34
PAGES_RE = re.compile(
    r"^(?P<term>.*?)[\s\u00a0]+(?P<pages>\d{1,3}(?:–\d{1,3})?(?:\s*,\s*\d{1,3}(?:–\d{1,3})?)*)\s*$"
)
SECTION_RE = re.compile(r"^(Symbols|Numerics|[A-Z])$")
HEADER_RE = re.compile(r"^(INDEX|index|\d{1,3})$")
PURE_PAGES_RE = re.compile(r"^\d{1,3}(?:–\d{1,3})?(?:\s*,\s*\d{1,3}(?:–\d{1,3})?)*$")


def extract_index_lines(doc) -> list:
    """返回 (page, col, y, text) 全部索引行，按阅读顺序。"""
    rows = []
    for pno in INDEX_PAGES:
        d = doc[pno].get_text("dict")
        for b in d["blocks"]:
            if b["type"] != 0:
                continue
            xc = (b["bbox"][0] + b["bbox"][2]) / 2
            col = 0 if xc < 230 else (1 if xc < 385 else 2)
            for l in b["lines"]:
                txt = "".join(s["text"] for s in l["spans"]).strip()
                txt = re.sub(r"\s+", " ", txt)
                if not txt or HEADER_RE.match(txt):
                    continue
                rows.append((pno, col, l["bbox"][1], l["bbox"][0], txt))
    rows.sort(key=lambda r: (r[0], r[1], r[2]))
    return rows


def build_entries(rows) -> list:
    """把行合并为 (节名|'', 条目文本, 是否子条目)。
    合并规则：
      - buf 已以页码结尾 -> 新行必是新条目；
      - buf 未以页码结尾且新行 x 与 buf 接近 -> 断行续接；
      - buf 未以页码结尾且新行缩进 -> buf 是分组词，落盘后开新子条目。
    """
    entries = []
    section = ""
    buf_text, buf_x = "", 0.0
    sub_indent_x = {}  # 每栏首个主条目的 x，用于判断子条目缩进

    def flush() -> None:
        nonlocal buf_text
        m = PAGES_RE.match(buf_text)
        text = f"{m.group('term').strip()}, {m.group('pages')}" if m else buf_text.strip()
        entries.append((section, text, buf_x > base_x_of_current_col + 6))
        buf_text = ""

    base_x_of_current_col = 0.0
    # 上一行的 (页, 栏, x)：折行续行判定需要同栏同页约束
    prev_pno = prev_col = None
    for pno, col, y, x, txt in rows:
        if SECTION_RE.match(txt):
            if buf_text:
                flush()
            section = txt
            sub_indent_x.pop(col, None)
            continue
        base_x_of_current_col = sub_indent_x.setdefault(col, x)
        if buf_text and PAGES_RE.match(buf_text) is None and PURE_PAGES_RE.match(txt):
            buf_text += " " + txt  # 页码被拆到独立行，并回上一条目
            continue
        if not buf_text:
            buf_text, buf_x = txt, x
        elif PAGES_RE.match(buf_text) is None and abs(x - buf_x) <= 6:
            buf_text += " " + txt  # 断行续接
        elif (PAGES_RE.match(buf_text) is None
              and (pno, col) == (prev_pno, prev_col)
              and x - buf_x >= 12):
            # 悬挂缩进折行续接（实测全书 47 处，续行恒比本条目首行深 ≥15pt；
            # 子条目起始仅 +9pt，阈值 12 可无损区分。跨栏/跨页不并，防误粘）
            buf_text += " " + txt
        else:
            flush()
            buf_text, buf_x = txt, x
        prev_pno, prev_col = pno, col
    if buf_text:
        flush()
    return entries


def render_markdown(entries) -> list:
    out = ["## Index", ""]
    cur = None
    for section, text, is_sub in entries:
        if section != cur:
            out += [f"### {section}", ""]
            cur = section
        prefix = "  - " if is_sub else "- "
        out.append(f"{prefix}{text}")
        if not is_sub:
            pass
        # 主条目之间不插空行，保持列表紧凑；分节切换时已有空行
    out.append("")
    return out


def replace_region(lines: list, new_block: list) -> int:
    """替换 md 中索引区（首次从 `index` 独立行，重跑从 `## Index`）到 `## Hands-on projects`。"""
    e = next((i for i, l in enumerate(lines)
              if l.startswith("## Hands-on projects for learning your way")), None)
    s = next((i for i, l in enumerate(lines) if l.strip() == "## Index"), None)
    if s is None:
        s = next((i for i, l in enumerate(lines) if l.strip() == "index"
                  and i > len(lines) * 0.9), None)
    if s is None or e is None or e <= s:
        return 0
    # 吃掉 index 行前的锚点行
    if s > 0 and lines[s - 1].strip().startswith("<a id="):
        s -= 1
    lines[s:e] = new_block
    return 1


def rebuild_index(lines: list) -> tuple[list, int]:
    """搬运 fix_index.main 的主体：重提索引并整段替换。返回 (新行列表, 是否替换)。"""
    doc = pymupdf.open(PDF)
    rows = extract_index_lines(doc)
    doc.close()
    entries = build_entries(rows)
    block = render_markdown(entries)

    lines = list(lines)
    replaced = replace_region(lines, block)

    n_entries = sum(1 for _, t, _ in entries if t)
    # 自检（搬运 fix_index.main）
    assert n_entries > 400, f"索引条目过少({n_entries})，提取可能失败"
    assert any(t == "AdamW optimizer, 148, 294" for _, t, _ in entries)
    assert any(s == "Symbols" for s, _, _ in entries)
    text = "\n".join(lines)
    assert "problem with modeling long sequences 52 self-attention" not in text, "乱码残留"
    print(f"[pipeline.index] 提取行: {len(rows)}, 条目: {n_entries}, 区域替换: {replaced}")
    return lines, replaced
