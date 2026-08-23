#!/usr/bin/env python3
"""
rebuild_toc.py — TOC 重建与锚点重注入（第二轮修复后运行）。

步骤（见 attachments/format-fix-round2.md 子方案 5）：
  1. 删除旧 `## Table of Contents` 区与全文所有 `<a id>` 锚点行；
  2. 扫描真实标题（围栏外），按文档顺序生成 TOC；
  3. 重新注入唯一锚点；
  4. 运行 §3 断言（围栏成对 / 无 PUA / TOC ⊆ 锚点）。

覆盖策略：
  - 收录：前置部分、章、编号节/子节、Appendix、附录内编号小节、
    附录 B 文献分组（### Chapter N）、附录 C 习题解答（显示加
    ' (Exercise solutions)' 后缀）、Index。
  - 排除：Summary（重复 8 次无导航价值）、liveProjects 广告、
    Table of Contents 自身。

幂等可重跑。
"""

import re
import sys
from pathlib import Path

PATH = Path("llms-from-scratch.md")
FENCE_RE = re.compile(r"^\s*```")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
ANCHOR_RE = re.compile(r'^<a id="[^"]+"></a>\s*$')
FRONT_MATTER = {"Preface", "Acknowledgments", "About this book",
                "About the author", "About the cover illustration"}


def slugify(text: str) -> str:
    s = text.strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-") or "section"


def fix_appendix_e_heading(lines: list[str]) -> int:
    """附录 E 标题为普通行（小写开头），升为 `## Appendix E ...`。"""
    for i, l in enumerate(lines):
        if l.strip() == "appendix E Parameter-efficient fine-tuning with LoRA":
            lines[i] = "## Appendix E Parameter-efficient fine-tuning with LoRA"
            return 1
    return 0


def fix_de_levels(lines: list[str]) -> int:
    """D.x/E.x 小节在 ## 附录下应为 ###（与附录 A 一致），修正 #### 跳变。"""
    n = 0
    for i, l in enumerate(lines):
        if re.match(r"^#### [DE]\.\d", l):
            lines[i] = "###" + l[4:]
            n += 1
    return n


def strip_old(lines: list[str]) -> list[str]:
    """删除旧 TOC 区与所有锚点行。"""
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


def scan_headings(lines: list[str]):
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


def include_in_toc(text: str, appendix_ctx: str) -> tuple[bool, str]:
    """返回 (是否收录, 显示文本)。"""
    if text in ("Table of Contents", "Summary"):
        return False, text
    if text.startswith("Hands-on projects"):
        return False, text
    if text in FRONT_MATTER or text == "Index":
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


def build(lines: list[str]) -> list[str]:
    heads = scan_headings(lines)
    # 唯一 slug
    used: dict[str, int] = {}
    slugs: list[str] = []
    for _, _, text in heads:
        base = slugify(text)
        used[base] = used.get(base, 0) + 1
        slugs.append(base if used[base] == 1 else f"{base}-{used[base]}")

    # TOC（文档顺序，带附录上下文）
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

    # 注入锚点
    head_pos = {h[0]: s for h, s in zip(heads, slugs)}
    out = []
    for i, l in enumerate(lines):
        if i in head_pos:
            out.append(f'<a id="{head_pos[i]}"></a>')
        out.append(l)
    # TOC 插在首个书级标题之后；无书级标题则放文件最前
    for k, l in enumerate(out):
        if l.startswith("# "):
            return out[: k + 1] + [""] + toc + out[k + 1 :]
    return toc + out


def main() -> None:
    lines = PATH.read_text(encoding="utf-8").split("\n")
    n_e = fix_appendix_e_heading(lines)
    n_de = fix_de_levels(lines)
    lines = strip_old(lines)
    final = build(lines)
    text = "\n".join(final)
    PATH.write_text(text, encoding="utf-8")

    # §3 断言
    ls = text.split("\n")
    fences = [l for l in ls if l.strip().startswith("```")]
    assert len(fences) % 2 == 0, "围栏不成对"
    assert not re.search(r"[\uF000-\uF0FF]", text), "PUA 残留"
    anchors = set(re.findall(r'<a id="([^"]+)">', text))
    toc_part = text.split("## Table of Contents")[1].split("\n## ", 1)[0] if "## Table of Contents" in text else ""
    links = set(re.findall(r"\]\(#([^)]+)\)", toc_part))
    missing = links - anchors
    assert not missing, f"TOC 链接无锚点: {missing}"
    n_heads = len(re.findall(r"^#{1,6} ", text, re.M))
    print(f"  附录 E 标题升级: {n_e}, D/E 小节级别: {n_de}")
    print(f"  标题: {n_heads}, 锚点: {len(anchors)}, TOC 链接: {len(links)}")
    print("rebuild_toc 完成，§3 断言全部通过。")


if __name__ == "__main__":
    sys.exit(main())
