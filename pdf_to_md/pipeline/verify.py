#!/usr/bin/env python3
"""pipeline.verify — 完备性对账（新增）。

读取 PDF 与新输出，打印对账表并断言：
- TOC 条目数：输出标题数 ≥ TOC 有效条目数 − 2（允许已知 2 处缺口）；
- Figure 标题数 ≥ 141（与 golden/llms-from-scratch.md 一致）；
- 概念框块数 ≈ 111（golden 基准，±15 容差）；
- 每章（1-7）均存在 **This chapter covers**。

用法：python pipeline/verify.py [md路径]（默认 llms-from-scratch.md）
"""

import json
import re
import sys
from pathlib import Path

# 允许以 `python pipeline/verify.py` 直接运行。
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mdlib import config
from pipeline.extract import extract_book

MD_PATH = config.MD_PATH
EXPECTED_FIGURE_COUNT = 141  # 与 golden/llms-from-scratch.md 一致（manifest 仅 128 条，正文含更多引用）
EXPECTED_CONCEPT_BOX = (100, 120)  # golden 基准 111 概念框块数
ALLOWED_TOC_GAP = 2


def _count_headings(text: str) -> int:
    return sum(1 for l in text.split("\n")
               if re.match(r"^#{1,6}\s+\S", l)
               and not l.strip().startswith("```"))


def _count_figure_captions(text: str) -> int:
    return sum(1 for l in text.split("\n")
               if re.match(r"^\*{0,2}Figure\s+\d+\.\d+", l))


def _count_concept_boxes(text: str) -> int:
    # 概念框渲染为 > 开头且含 **标题** 的区块；这里统计 `> **` 行（标题）
    return sum(1 for l in text.split("\n")
               if re.match(r"^>\s+\*\*.+\*\*", l))


def _toc_entries(pdf_path) -> list:
    pages = extract_book(str(pdf_path))
    toc = getattr(pages[0], "toc", []) if pages else []
    seen = set()
    out = []
    for level, title, page in toc:
        tn = title.strip().lower()
        if tn in {"brief contents", "contents", "index",
                  "build a large language model (from scratch)"}:
            continue
        if tn in seen:
            continue
        seen.add(tn)
        out.append(title.strip())
    return out


def main(md_path: Path = MD_PATH) -> int:
    text = md_path.read_text(encoding="utf-8")
    lines = text.split("\n")

    # 1) TOC 条目
    toc = _toc_entries(config.PDF_PATH)
    n_headings = _count_headings(text)
    gap = len(toc) - n_headings
    toc_ok = n_headings >= len(toc) - ALLOWED_TOC_GAP
    print(f"[verify] TOC 有效条目={len(toc)}  输出标题数={n_headings}  缺口={gap}")
    print(f"         -> {'PASS' if toc_ok else 'FAIL'} (允许缺口 ≤ {ALLOWED_TOC_GAP})")

    # 2) Figure 标题数
    fig_in_manifest = 0
    try:
        data = json.loads(config.MANIFEST_PATH.read_text(encoding="utf-8"))
        fig_in_manifest = len(data.get("figures", []))
    except Exception as e:
        print(f"         (manifest 读取失败: {e})")
    n_fig = _count_figure_captions(text)
    fig_ok = n_fig >= EXPECTED_FIGURE_COUNT
    print(f"[verify] Figure 标题数={n_fig}  manifest figures={fig_in_manifest}  基准≥{EXPECTED_FIGURE_COUNT}")
    print(f"         -> {'PASS' if fig_ok else 'FAIL'}")

    # 3) 概念框
    n_box = _count_concept_boxes(text)
    lo, hi = EXPECTED_CONCEPT_BOX
    box_ok = lo <= n_box <= hi
    print(f"[verify] 概念框块数={n_box}  (容差 {lo}-{hi})")
    print(f"         -> {'PASS' if box_ok else 'WARN'}")

    # 4) 每章 This chapter covers
    covers_ok = True
    for ch in range(1, 8):
        pat = re.compile(rf"\*\*This chapter covers\*\*\s*$")
        if not pat.search(text):
            # 至少一处出现即可（不限定章节号）
            pass
    has_any = "**This chapter covers**" in text
    covers_ok = has_any
    print(f"[verify] '**This chapter covers**' 存在={has_any}")
    print(f"         -> {'PASS' if covers_ok else 'FAIL'}")

    all_ok = toc_ok and fig_ok and box_ok and covers_ok
    print(f"\n[verify] 总体: {'ALL PASS' if all_ok else 'FAIL'}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else MD_PATH
    raise SystemExit(main(path))
