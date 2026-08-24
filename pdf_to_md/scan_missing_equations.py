"""排查书版中以矢量绘制（无文本层）的展示型数学公式。

书版很多公式只画进矢量图，PDF 文本层无对应文字；正文以 "…:" / "approximation"
等引导语悬空。本脚本逐页找出「绘制密集、自身几乎无文字、且不属于 figure 裁剪框」
的水平条带，即为潜在丢失的展示型公式区域，供人工转录进 patches_data.json 的
equation_rebuilds。

用法:
    python scan_missing_equations.py [pdf] [manifest]

默认读取同目录的 PDF 与 extracted_images/manifest.json。
"""
from __future__ import annotations

import fitz
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PDF = sys.argv[1] if len(sys.argv) > 1 else str(ROOT / "Build a Large Language Model (From Scratch) (Sebastian Raschka).pdf")
MANIFEST = sys.argv[2] if len(sys.argv) > 2 else str(ROOT / "extracted_images" / "manifest.json")

MIN_STROKES = 6      # 条带内最少矢量笔画数
MAX_H = 170          # 条带最大高度（排除大框/整页规则线）
MAX_W_FRAC = 0.85    # 条带最大宽度占页宽比例
MAX_TEXT = 10        # 条带内允许的最多文字字符（>此值视为带文字的非纯公式）


def main() -> None:
    doc = fitz.open(PDF)
    man = json.loads(Path(MANIFEST).read_text("utf-8"))

    def page_clips(pno: int):
        return [fitz.Rect(e["clip"]) for e in man["figures"] if e["page"] == pno + 1]

    print(f"{'page':>5} {'y0':>5} {'h':>4} {'strokes':>7}  (candidate display equations)")
    found = 0
    for pno in range(len(doc)):
        page = doc[pno]
        fcs = page_clips(pno)
        draws = [fitz.Rect(d["rect"]) for d in page.get_drawings()
                 if not any(fitz.Rect(d["rect"]).intersects(fc) for fc in fcs)
                 and d["rect"].width > 3 and d["rect"].height > 3]
        if not draws:
            continue
        draws.sort(key=lambda r: (r.y0, r.x0))
        bands = []
        for r in draws:
            if bands and r.y0 <= bands[-1][1] + 6:
                bands[-1][0].append(r)
                bands[-1][1] = max(bands[-1][1], r.y1)
            else:
                bands.append([[r], r.y1])
        for bnd in bands:
            rects = bnd[0]
            y1 = bnd[1]
            y0 = min(r.y0 for r in rects)
            x0 = min(r.x0 for r in rects)
            x1 = max(r.x1 for r in rects)
            h = y1 - y0
            w = x1 - x0
            if len(rects) < MIN_STROKES:
                continue
            if h > MAX_H or w > page.rect.width * MAX_W_FRAC:
                continue
            band = fitz.Rect(x0, y0, x1, y1)
            txt = page.get_text("text", clip=band).strip()
            if len(txt) > MAX_TEXT:
                continue
            print(f"{pno + 1:5d} {y0:5.0f} {h:4.0f} {len(rects):7d}")
            found += 1
    print(f"\n共 {found} 个候选展示型公式区域（需人工核对并转录进 equation_rebuilds）")


if __name__ == "__main__":
    main()
