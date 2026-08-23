#!/usr/bin/env python3
"""format_scan — PDF 侧特殊格式清单扫描（Phase 0 基线）。

逐页抽取 PyMuPDF 的结构信号（font/size/flags/color/bbox/fill）+ link 注解，
按类别建"特殊格式清单"，写出 format_scan.json，并打印摘要。
供 format_audit.py 与 MD 做差分。

类别：margin_notes / bold / italic / math_pua / superscript / links /
      tables(heuristic) / callouts / footnotes。
"""
import fitz
import json
import re
import collections

PDF = "Build a Large Language Model (From Scratch) (Sebastian Raschka).pdf"

# 排除：页眉/页脚字体、代码字体（Courier）。这些不算正文强调。
HDR_OR_CODE = ("NewBaskerville", "FranklinGothic", "Courier")
MARGIN_FONT = "HumanistMann"
PUA_LO, PUA_HI = 0xF000, 0xF0FF
BODY_BOLD_MAX = 13.0      # 仅统计正文级 bold（排除大号标题）
FOOTNOTE_MAX = 8.0        # 页脚/脚注小字阈值
BOTTOM_MARGIN = 70.0      # 距页底 < 此值视为页脚区

CONCEPT_FILL = (0.969, 0.961, 0.910)
LISTING_FILL = (0.438, 0.652, 0.801)


def norm(t: str) -> str:
    return re.sub(r"\s+", " ", t).strip()


def is_hdr_or_code(font: str) -> bool:
    return any(h in font for h in HDR_OR_CODE)


def main():
    doc = fitz.open(PDF)
    n = len(doc)
    data = {
        "pdf": PDF,
        "pages": n,
        "margin_notes": [],
        "bold": [],
        "italic": [],
        "math_pua": [],
        "superscript": [],
        "links": [],
        "tables": [],
        "callouts": [],
        "footnotes": [],
    }
    pua_chars = collections.Counter()

    for pno in range(n):
        page = doc[pno]
        h = page.rect.height
        w = page.rect.width
        d = page.get_text("dict")

        # ---- 文本块级信号 ----
        for b in d["blocks"]:
            if "lines" not in b:
                continue
            for ln in b["lines"]:
                for s in ln["spans"]:
                    f = s["flags"]
                    font = s["font"]
                    size = s["size"]
                    t = s["text"]
                    if not t.strip():
                        continue
                    x0, y0, x1, y1 = s["bbox"]
                    # 页边注：HumanistMann 且在右侧页边（真正的侧注/边注）。
                    # 该字体也用于页内 STAGE 标签等，故限 right_margin + 句子级 + 非全大写。
                    if MARGIN_FONT in font and x0 > w * 0.62:
                        nt = norm(t)
                        if len(nt.split()) >= 3 and not nt.isupper():
                            data["margin_notes"].append(
                                {"page": pno, "text": nt[:80],
                                 "x0": round(x0, 1), "right_margin": True})
                    # 上标
                    if f & 1 and not is_hdr_or_code(font):
                        data["superscript"].append(
                            {"page": pno, "text": norm(t)[:40]})
                    # 正文 bold 强调（排除大标题/页眉/代码/封面/图注/全大写标签）
                    if f & 16 and not is_hdr_or_code(font) and size <= BODY_BOLD_MAX:
                        tt = norm(t)
                        if pno < 2:
                            continue
                        if tt[:7].lower() in ("figure ", "table ", "listing"):
                            continue
                        if tt.isupper() and len(tt) < 25:
                            continue
                        data["bold"].append({"page": pno, "text": tt[:40],
                                             "size": round(size, 2)})
                    # 正文 italic 强调
                    if f & 2 and not is_hdr_or_code(font) and size <= BODY_BOLD_MAX:
                        data["italic"].append({"page": pno, "text": norm(t)[:40]})
                    # 数学/PUA 私有区字形
                    for ch in t:
                        o = ord(ch)
                        if PUA_LO <= o <= PUA_HI:
                            pua_chars[ch] += 1
                            data["math_pua"].append(
                                {"page": pno, "ch": ch, "text": norm(t)[:40]})
                    # 脚注/页脚小字
                    if size <= FOOTNOTE_MAX and y1 > h - BOTTOM_MARGIN and not is_hdr_or_code(font):
                        data["footnotes"].append(
                            {"page": pno, "text": norm(t)[:60], "size": round(size, 2)})

        # ---- 绘图级信号（callout 填充 / 表格网格） ----
        draws = page.get_drawings()
        for dr in draws:
            fill = dr.get("fill")
            if fill:
                fr = tuple(round(c, 3) for c in fill)
                if (abs(fr[0] - CONCEPT_FILL[0]) < 0.02 and
                        abs(fr[1] - CONCEPT_FILL[1]) < 0.02 and
                        abs(fr[2] - CONCEPT_FILL[2]) < 0.02) or \
                   (abs(fr[0] - LISTING_FILL[0]) < 0.02 and
                        abs(fr[1] - LISTING_FILL[1]) < 0.02 and
                        abs(fr[2] - LISTING_FILL[2]) < 0.02):
                    data["callouts"].append(
                        {"page": pno, "fill": fr,
                         "rect": [round(v, 1) for v in dr["rect"]]})

        # ---- 表格启发式：>=2 横 + >=2 竖 描边线 包围文本 ----
        hlines = [dr["rect"] for dr in draws
                  if not dr.get("fill") and (dr["rect"][3] - dr["rect"][1]) < 2]
        vlines = [dr["rect"] for dr in draws
                  if not dr.get("fill") and (dr["rect"][2] - dr["rect"][0]) < 2]
        if len(hlines) >= 2 and len(vlines) >= 2:
            xs = [r[0] for r in vlines] + [r[2] for r in vlines]
            ys = [r[1] for r in hlines] + [r[3] for r in hlines]
            bx0, bx1 = min(xs), max(xs)
            by0, by1 = min(ys), max(ys)
            # 区域内有文本块？
            has_txt = any(b.get("lines") and
                          bx0 - 2 <= b["bbox"][0] and b["bbox"][2] <= bx1 + 2 and
                          by0 - 2 <= b["bbox"][1] and b["bbox"][3] <= by1 + 2
                          for b in d["blocks"])
            if has_txt:
                data["tables"].append(
                    {"page": pno, "rect": [round(bx0, 1), round(by0, 1),
                                           round(bx1, 1), round(by1, 1)]})

        # ---- 内部超链接注解 ----
        for lnk in page.get_links():
            txt = norm(lnk.get("fromdest") or "")
            data["links"].append(
                {"page": pno, "text": txt[:50], "kind": lnk.get("kind")})

    doc.close()

    # 汇总统计
    summary = {
        "pages": n,
        "margin_notes": len(data["margin_notes"]),
        "bold": len(data["bold"]),
        "italic": len(data["italic"]),
        "math_pua": len(data["math_pua"]),
        "pua_distinct": len(pua_chars),
        "superscript": len(data["superscript"]),
        "links": len(data["links"]),
        "tables_candidate": len(data["tables"]),
        "callouts": len(data["callouts"]),
        "footnotes": len(data["footnotes"]),
    }
    data["summary"] = summary

    out = "format_scan.json"
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1)
    print("[scan] wrote", out)
    for k, v in summary.items():
        if k != "pages":
            print(f"  {k:16s}: {v}")


if __name__ == "__main__":
    main()
