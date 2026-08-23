#!/usr/bin/env python3
"""audit_summary_formatting — 章末 Summary 格式审计（只读，不修改任何产物）。

背景（pdf-to-md-plan.md §19）：PDF 每章 Summary 为 Wingdings 圆点列表，
旧链把每条 bullet 渲染为独立 "> " 引用块，且折行续块被 prose 链吸收成
裸段落——同一列表碎成 N 个盒子 + 孤行脱列。改造后应为 "- "/"  - " 列表。

检查项：
  S1 区域内引用行     ">" 开头的行必须为 0（旧语义残留）
  S2 裸段落行          非 "- "/缩进"- "的非空行必须为 0（续块未并入）
  S3 PDF↔MD 对账      每章列表项数须与 classify 的 summary_list items 一致
                       （需传入分类后 blocks）
  S4 en-dash 子弹残留 行内 " – "（空格包围的 en-dash）必须为 0
  S5 断词事故黑名单    timeand / GPTand / CPUand（悬挂连字符被误并的历史案例）
  S6 二级缩进规范      嵌套项恰为两空格缩进（CommonMark 嵌套列表语法）

用法：python audit_summary_formatting.py [--md PATH] [--blocks]
退出码：存在违规 → 1，否则 0。
"""

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

MD_PATH = ROOT / "llms-from-scratch.md"

# 悬挂连字符误并黑名单（_dehyphen_replace 连词守卫的回归哨兵）
BLACKLIST = ("timeand", "GPTand", "CPUand")

_SUMMARY_H = re.compile(r"^### Summary\s*$")
_NEXT_H = re.compile(r"^#{1,3} ")
_LIST_ITEM = re.compile(r"( *)- ")
_ANCHOR_LINE = re.compile(r'<a id="[^"]+"></a>\s*$')
_EN_DASH_SPACED = re.compile(r"\s–\s")


def _parse_regions(lines: list) -> list:
    """返回 [(start, end)]——'### Summary' 行到下一个 1-3 级标题前。"""
    regions = []
    i = 0
    while i < len(lines):
        if _SUMMARY_H.match(lines[i]):
            j = i + 1
            while j < len(lines) and not _NEXT_H.match(lines[j]):
                j += 1
            regions.append((i, j))
        i += 1
    return regions


def scan(md_text: str, blocks: list = None) -> dict:
    """审计 MD 文本；blocks（classify 后）提供时附加 S3 PDF↔MD 对账。
    返回 {"n_regions", "n_items", "violations": [str]}。"""
    lines = md_text.split("\n")
    regions = _parse_regions(lines)
    violations: list = []
    n_items = 0

    for s, e in regions:
        for ln in range(s + 1, e):
            line = lines[ln]
            if not line.strip():
                continue
            if line.lstrip().startswith(">"):
                violations.append(f"S1 L{ln+1} 引用行残留: {line[:60]!r}")
                continue
            if _ANCHOR_LINE.match(line):
                continue
            m = _LIST_ITEM.match(line)
            if not m:
                violations.append(f"S2 L{ln+1} 裸段落行(未并入列表): {line[:60]!r}")
                continue
            n_items += 1
            indent = m.group(1)
            if indent not in ("", "  "):
                violations.append(
                    f"S6 L{ln+1} 二级缩进须恰两空格: {line[:40]!r}")
            if _EN_DASH_SPACED.search(line):
                violations.append(f"S4 L{ln+1} en-dash 子弹残留: {line[:60]!r}")

    for bad in BLACKLIST:
        if bad in md_text:
            first = md_text.index(bad)
            ln = md_text.count("\n", 0, first) + 1
            violations.append(f"S5 L{ln} 断词事故黑名单命中: {bad!r}")

    # S3：classify 后的 blocks 提供 ground truth（每章 items 数）
    n_expected = None
    if blocks is not None:
        expected = []  # 各 Summary 区域的 items 数（文档序）
        in_s = False
        cnt = 0
        for b in blocks:
            if b.kind == "heading":
                if in_s and cnt:
                    expected.append(cnt)
                in_s = " ".join(str(b.text).split()).lower() == "summary"
                cnt = 0
            elif in_s and b.kind == "summary_list":
                cnt += len(b.meta.get("items") or [])
        if in_s and cnt:
            expected.append(cnt)
        actual = [sum(1 for ln in lines[s + 1:e] if _LIST_ITEM.match(ln))
                  for s, e in regions]
        n_expected = sum(expected)
        if len(expected) != len(actual):
            violations.append(
                f"S3 Summary 区块数不符: PDF={len(expected)} MD={len(actual)}")
        else:
            for k, (ex, ac) in enumerate(zip(expected, actual), 1):
                if ex != ac:
                    violations.append(
                        f"S3 第{k}章列表项数不符: PDF={ex} MD={ac}")

    return {"n_regions": len(regions), "n_items": n_items,
            "n_expected_items": n_expected, "violations": violations}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--md", default=str(MD_PATH))
    args = ap.parse_args()

    text = Path(args.md).read_text(encoding="utf-8")
    rep = scan(text)
    print(f"[summary-audit] 区域数={rep['n_regions']}  列表项={rep['n_items']}"
          f"{'(期望 '+str(rep['n_expected_items'])+')' if rep['n_expected_items'] is not None else ''}")
    for v in rep["violations"][:20]:
        print(f"  {v}")
    print(f"[summary-audit] -> {'FAIL' if rep['violations'] else 'PASS'}")
    return 1 if rep["violations"] else 0


if __name__ == "__main__":
    sys.exit(main())
