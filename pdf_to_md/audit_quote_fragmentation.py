#!/usr/bin/env python3
"""audit_quote_fragmentation — 引用块碎片化审计（只读，不修改任何产物）。

背景（同族问题详见 attachments/quote-fragmentation.md）：
一个视觉概念框（CALLOUT_FILL 矩形）内的文字被 PyMuPDF 切成多个 text block，
classify 按单 block 独立判定、render 逐块输出引用 + 尾部空行，
导致 MD 中同一框碎成 N 个分离 blockquote（如 §1.5 "GPT-3 dataset details"）。

两层排查：
1. PDF ground truth：逐页枚举 CALLOUT_FILL 矩形（与 classify._is_concept_box
   同源判定：fill/尺寸/重叠 ≥0.20），统计矩形内文本块数；
   "This chapter covers" 整框跳过规则同源复刻（该类框走 bullet 链路，不算碎片）。
2. MD 启发式：扫描"空行分隔的相邻引用块对"，用归一化前缀回查 PDF 矩形归属：
   - same-rect：前后两块同属一矩形 → 真碎片（结构性缺陷）
   - cross-rect / one-sided：两框垂直堆叠或单侧可定位 → 合法相邻
   - unmatched：MD 文本无法回查 PDF（patch 注入/图注等）→ 人工分诊

用法：python audit_quote_fragmentation.py [--report PATH]
退出码：发现 same-rect 真碎片 → 1，否则 0。
"""

import argparse
import re
import sys
from pathlib import Path

import fitz

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mdlib import config
from mdlib.textutil import norm
from pipeline.extract import extract_book

CALLOUT_FILL = tuple(round(v, 3) for v in config.CALLOUT_FILL)
MIN_W, MIN_H = 100, 40          # 与 classify._is_concept_box 同源
OVERLAP = 0.20                  # 与 classify._is_concept_box 同源
SIG_LEN = 80                    # 回查匹配用的归一化前缀长度


# --------------------------------------------------------------------------- #
# PDF 侧：矩形枚举 + 块归属
# --------------------------------------------------------------------------- #
def page_callout_rects(page) -> list:
    """本页 CALLOUT_FILL 矩形（去近重复；不含 covers 跳过逻辑）。"""
    rects = []
    for fill, r in getattr(page, "drawings", []):
        if fill is None:
            continue
        if tuple(round(v, 3) for v in fill) != CALLOUT_FILL:
            continue
        if r.width < MIN_W or r.height < MIN_H:
            continue
        if any((fitz.Rect(r) & rr).get_area() > 0.9 * min(fitz.Rect(r).get_area(), rr.get_area())
               for rr in rects):
            continue
        rects.append(fitz.Rect(r))
    return rects


def covers_skip_rects(page) -> list:
    """复刻 classify 的整框跳过：含 "This chapter covers" 块所在的矩形。"""
    skip = []
    texts = [b.text for b in page.blocks]
    if not any("This chapter covers" in t for t in texts):
        return skip
    for r in page_callout_rects(page):
        for b in page.blocks:
            bx = fitz.Rect(b.bbox)
            inter = bx & r
            if inter.is_empty or bx.get_area() == 0:
                continue
            if inter.get_area() / bx.get_area() < OVERLAP:
                continue
            if "This chapter covers" in b.text:
                skip.append(r)
                break
    return skip


def build_rect_index(pages: list) -> dict:
    """矩形归属索引：(页号0基, 矩形序号) -> {"members": [norm 全文...], "n": 块数}。

    另建前缀速查表 prefix -> (页号, 矩形序号) 供 MD 行快速定位候选矩形。
    """
    index = {}
    prefix = {}
    for page in pages:
        rects = [r for r in page_callout_rects(page)
                 if not any((r & sr).get_area() > 0.9 * min(r.get_area(), sr.get_area())
                            for sr in covers_skip_rects(page))]
        for ri, r in enumerate(rects):
            members = []
            for b in page.blocks:
                bx = fitz.Rect(b.bbox)
                inter = bx & r
                if inter.is_empty or bx.get_area() == 0:
                    continue
                if inter.get_area() / bx.get_area() >= OVERLAP and b.text.strip():
                    members.append(norm(b.text))
            key = (page.index, ri)
            index[key] = {"members": members, "n": len(members)}
            for m in members:
                prefix.setdefault(m[:SIG_LEN], key)
    return index, prefix


def _locate(sig: str, prefix: dict) -> object:
    """归一化文本 → 候选矩形 key。前缀精确命中优先；
    回退仅单向（MD 行 ⊂ PDF 块全文），且要求足够长的签名防误配。"""
    if sig in prefix:
        return prefix[sig]
    if len(sig) < 30:
        return None
    probe = sig[:60]
    for k, key in prefix.items():
        if probe.startswith(k[:40]) or probe[:40] in k:
            return key
    return None


# --------------------------------------------------------------------------- #
# MD 侧：引用块切分 + 相邻对
# --------------------------------------------------------------------------- #
def quote_groups(lines: list) -> list:
    """极大连续 `>` 行组：[(起始行号0基, 结束行号0基含, [行文本...])]。"""
    groups = []
    i, n = 0, len(lines)
    while i < n:
        if lines[i].lstrip().startswith(">"):
            j = i
            while j + 1 < n and lines[j + 1].lstrip().startswith(">"):
                j += 1
            groups.append((i, j, lines[i:j + 1]))
            i = j + 1
        else:
            i += 1
    return groups


def adjacent_pairs(groups: list, lines: list) -> list:
    """仅隔空行的相邻引用组对 [(组A, 组B)]（间隔行必须全为空白）。"""
    pairs = []
    for a, b in zip(groups, groups[1:]):
        gap = b[0] - a[1] - 1
        if 0 < gap <= 2 and all(not lines[a[1] + 1 + k].strip() for k in range(gap)):
            pairs.append((a, b))
    return pairs


def strip_md(s: str) -> str:
    s = s.lstrip()
    if s.startswith(">"):
        s = s.lstrip("> ")
    return s.replace("**", "").replace("`", "").strip()


# --------------------------------------------------------------------------- #
# 可复用扫描（verify.py 引用）                                                 #
# --------------------------------------------------------------------------- #
def scan(md_text: str, pages: list = None) -> dict:
    """对最终 Markdown 执行碎片化扫描，返回分类计数与明细。

    返回 dict：
      n_rects / n_multi / n_groups / n_pairs
      true     : [(页号1基, A首行摘要, B首行摘要)]（同矩形真碎片）
      stacked  : 同上（跨矩形/单侧命中，疑似合法堆叠）
      unmatched: 同上（无法回查 PDF）
    """
    if pages is None:
        pages = extract_book(str(config.PDF_PATH))
    rect_index, prefix = build_rect_index(pages)

    lines = md_text.split("\n")
    groups = quote_groups(lines)
    pairs = adjacent_pairs(groups, lines)

    true_frags, stacked, unmatched = [], [], []
    for ga, gb in pairs:
        sig_b = norm(strip_md(gb[2][0]))[:SIG_LEN]
        hit_b = _locate(sig_b, prefix)
        # A 组尾行逐行回查：任一行落入 B 的候选矩形 → 同框真碎片
        hit_a_same = None
        for ln in reversed(ga[2]):
            key_b = hit_b or _locate(norm(strip_md(ln))[:SIG_LEN], prefix)
            if key_b is None:
                continue
            body = norm(strip_md(ln))
            if len(body) < 30:
                continue
            mem = rect_index[key_b]["members"]
            if any(body[:60] in m for m in mem):
                hit_a_same = key_b
                break
        entry_a = strip_md(ga[2][0])[:58]
        entry_b = strip_md(gb[2][0])[:58]
        if hit_b and hit_a_same:
            true_frags.append((hit_a_same[0] + 1, entry_a, entry_b))
        else:
            hit_a = _locate(norm(strip_md(ga[2][-1]))[:SIG_LEN], prefix)
            if hit_b or hit_a:
                hit = hit_b or hit_a
                stacked.append((hit[0] + 1, entry_a, entry_b))
            else:
                unmatched.append((None, entry_a, entry_b))

    return {
        "n_rects": len(rect_index),
        "n_multi": sum(1 for v in rect_index.values() if v["n"] > 1),
        "n_groups": len(groups),
        "n_pairs": len(pairs),
        "true": true_frags,
        "stacked": stacked,
        "unmatched": unmatched,
    }


# --------------------------------------------------------------------------- #
# 主流程                                                                       #
# --------------------------------------------------------------------------- #
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--md", default=str(config.MD_PATH))
    ap.add_argument("--report", default="")
    args = ap.parse_args()

    print(f"[audit] extract_book({config.PDF_PATH.name}) ...")
    pages = extract_book(str(config.PDF_PATH))
    r = scan(Path(args.md).read_text(encoding="utf-8"), pages)
    print(f"[audit] PDF 概念框矩形={r['n_rects']}  多块矩形={r['n_multi']}")
    print(f"[audit] MD 引用组={r['n_groups']}  相邻引用对={r['n_pairs']}")

    def brief(e):
        pg, a, b = e
        tag = f"p{pg:<4}" if pg else "p?   "
        return f"  {tag} L--- | {a}\n        {b}"

    out = []
    out.append(f"PDF 概念框矩形: {r['n_rects']}（多块矩形 {r['n_multi']} 个）")
    out.append(f"MD 引用组: {r['n_groups']}  相邻对: {r['n_pairs']}")
    out.append("")
    out.append(f"== TRUE FRAGMENT（同矩形真碎片，须修复）: {len(r['true'])}")
    for e in r["true"]:
        out.append(brief(e))
    out.append("")
    out.append(f"== STACKED（跨矩形/单侧命中，疑似合法堆叠）: {len(r['stacked'])}")
    for e in r["stacked"]:
        out.append(brief(e))
    out.append("")
    out.append(f"== UNMATCHED（无法回查 PDF，人工分诊）: {len(r['unmatched'])}")
    for e in r["unmatched"]:
        out.append(brief(e))

    report = "\n".join(out)
    print("\n" + report)
    if args.report:
        Path(args.report).write_text(report + "\n", encoding="utf-8")
        print(f"\n[audit] report -> {args.report}")
    return 1 if r["true"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
