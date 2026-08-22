#!/usr/bin/env python3
"""
render_appendix_figures.py — 渲染附录 E 的 Figure E.1–E.5 并插入图片链接。

背景：P0 只渲染了正文 `Figure X.Y`（143 个），附录 E 的 `Figure E.1–E.5`
为矢量图且未被提取，md 中只有 caption 没有图。

方案（与 P0 同思路，简化版）：
  1. 以 `Figure E.N` caption bbox 为锚点；
  2. 图形区域 = caption 上方水平带内所有绘图路径与内嵌位图的并集，
     向下延伸 20pt 以包含轴标签/图例；
  3. @3x 渲染保存 `extracted_images/figures/FigE.N_pXXX.png`；
  4. 在 md 的 `**Figure E.N**` / `Figure E.N` caption 行前插入 `![Fig E.N](...)`。

幂等可重跑。
"""

import sys
from pathlib import Path

import pymupdf

PDF = "Build a Large Language Model (From Scratch) (Sebastian Raschka).pdf"
PATH = Path("llms-from-scratch.md")
OUT_DIR = Path("extracted_images/figures")


def find_captions(doc) -> list[tuple[int, str, pymupdf.Rect]]:
    """返回 (页号, 'E.N', caption bbox)。"""
    caps = []
    for pno in range(len(doc)):
        for b in doc[pno].get_text("dict")["blocks"]:
            if b["type"] != 0:
                continue
            txt = "".join(s["text"] for l in b["lines"] for s in l["spans"]).strip()
            for n in range(1, 6):
                tag = f"Figure E.{n}"
                if txt.startswith(tag):
                    rest = txt[len(tag):].strip()
                    # 图注后跟大写开头描述；正文引用后跟小写动词(illustrates/plots)
                    if rest and rest[0].isupper() and len(rest) > 5:
                        caps.append((pno, f"E.{n}", pymupdf.Rect(b["bbox"])))
    return caps


def figure_region(page, cap: pymupdf.Rect) -> pymupdf.Rect:
    """caption 上方图形元素的并集区域。"""
    x0, y0 = cap.x0 - 10, cap.y0
    x1, y1 = cap.x1 + 10, cap.y0 + 20
    band_top = cap.y0 - 420  # 图最多向上延伸 ~420pt
    for d in page.get_drawings():
        r = d["rect"]
        if r.y1 <= cap.y0 + 2 and r.y1 > band_top and r.width > 5 and r.height > 5:
            x0, y0 = min(x0, r.x0), min(y0, r.y0)
            x1, y1 = max(x1, r.x1), max(y1, r.y1 + 20)
    for img in page.get_image_info():
        r = pymupdf.Rect(img["bbox"])
        if r.y1 <= cap.y0 + 2 and r.y1 > band_top:
            x0, y0 = min(x0, r.x0), min(y0, r.y0)
            x1, y1 = max(x1, r.x1), max(y1, r.y1)
    return pymupdf.Rect(x0, max(y0, band_top), x1, min(y1, cap.y0 + 20))


def render(doc, caps) -> dict[str, str]:
    """渲染每个图，返回 {'E.1': 相对路径}。"""
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    mat = pymupdf.Matrix(3, 3)
    out = {}
    seen = set()
    for pno, tag, cap in caps:
        if tag in seen:
            continue
        seen.add(tag)
        region = figure_region(doc[pno], cap)
        pix = doc[pno].get_pixmap(matrix=mat, clip=region)
        fname = f"Fig{tag.replace('.', '')}_p{pno + 1}.png"
        pix.save(str(OUT_DIR / fname))
        out[tag] = f"extracted_images/figures/{fname}"
        print(f"  渲染 Figure {tag}: {fname} ({pix.width}x{pix.height})")
    return out


def insert_links(lines: list[str], paths: dict[str, str]) -> int:
    """在 caption 行前插入图片链接（幂等）。"""
    n = 0
    i = 0
    while i < len(lines):
        l = lines[i].strip()
        for tag, rel in paths.items():
            cap_prefix = f"Figure {tag}"
            if (l.startswith((cap_prefix, f"**{cap_prefix}"))
                    and len(l) > len(cap_prefix) + 5
                    and not l.startswith("![")
                    and "illustrates" not in l and "plots" not in l):
                # 前两行已是该图链接（链接+空行）则跳过，保证幂等
                prev = lines[max(0, i - 2):i]
                if any(rel in p for p in prev):
                    continue
                lines[i:i] = [f"![Fig {tag}]({rel})", ""]
                n += 1
                i += 3  # 跳过插入的链接、空行与 caption 本身，避免重复处理
                break
        else:
            i += 1
    return n


def main() -> None:
    doc = pymupdf.open(PDF)
    caps = find_captions(doc)
    assert len(caps) >= 5, f"caption 发现不足: {len(caps)}"
    paths = render(doc, caps)
    doc.close()

    lines = PATH.read_text(encoding="utf-8").split("\n")
    inserted = insert_links(lines, paths)
    PATH.write_text("\n".join(lines), encoding="utf-8")
    print(f"  图片链接插入: {inserted}")
    print("render_appendix_figures 完成。")


if __name__ == "__main__":
    sys.exit(main())
