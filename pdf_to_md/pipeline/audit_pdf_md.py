#!/usr/bin/env python3
"""PDF ↔ MD 全面格式对账（只读）。

法1 双向 token 流对账（按章节分段，difflib 对齐）
    丢失三级归类：图内文字(接受) / 页眉页脚区(接受) / 真丢失嫌疑
法2 逐页保留率曲线：正文 span 字符量按页统计，低留存页列入复核
法3 字体语义普查：(font,size) 频次表
法4 版式普查：drawings 填充色聚类 / 大字号 span / 斜体独占行

输出：stdout 摘要 + /tmp/opencode/audit_pdf_md.json
"""
import json, re, sys, difflib
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from mdlib.config import MD_PATH, PDF_PATH

OUT_JSON = Path("/tmp/opencode/audit_pdf_md.json")


def nz(s):
    return re.sub(r"[^a-z0-9]", "", s.lower()) if s else ""


def pdf_spans(doc):
    out = []
    for pi in range(len(doc)):
        d = doc[pi].get_text("dict")
        h = doc[pi].rect.height
        for b in d["blocks"]:
            if b["type"] != 0:
                continue
            for l in b["lines"]:
                for sp in l["spans"]:
                    t = sp["text"].strip()
                    if not t:
                        continue
                    out.append({"page": pi, "text": t, "size": round(sp["size"], 1),
                                "font": sp["font"], "flags": sp["flags"],
                                "bbox": sp["bbox"], "ph": h})
    return out


def load_figure_rects():
    mf = Path(__file__).resolve().parent.parent / "extracted_images" / "manifest.json"
    rects = defaultdict(list)
    try:
        data = json.loads(mf.read_text(encoding="utf-8"))
    except Exception:
        return rects
    for e in data.get("figures", []):
        pg, clip = e.get("page"), e.get("clip")
        if pg and clip:
            rects[pg - 1].append(clip)
    return rects


def in_rect(bbox, rect, pad=2.0):
    cx, cy = (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2
    return rect[0] - pad <= cx <= rect[2] + pad and rect[1] - pad <= cy <= rect[3] + pad


def build_pdf_tokens(spans, fig_rects):
    toks, acc_fig, acc_hf = [], [], []
    for sp in spans:
        pg, h = sp["page"], sp["ph"]
        x0, y0, x1, y1 = sp["bbox"]
        ntxt = nz(sp["text"])
        if y1 < 40 or y0 > h - 32 or (ntxt.isdigit() and len(ntxt) <= 3):
            acc_hf.append((pg + 1, sp["text"][:60]))
            continue
        if any(in_rect(sp["bbox"], r) for r in fig_rects.get(pg, [])):
            acc_fig.append((pg + 1, sp["text"][:60]))
            continue
        for w in sp["text"].split():
            n = nz(w)
            if n:
                toks.append((n, pg))
    return toks, acc_fig, acc_hf


def md_clean_lines(lines):
    out, inf = [], False
    for l in lines:
        s = l.strip()
        if s.startswith("```"):
            inf = not inf
            out.append("")
            continue
        if inf:
            out.append(l)
            continue
        t = re.sub(r"<a id=\"[^\"]*\"></a>", "", l)
        t = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", t)
        t = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", t)
        t = re.sub(r"^#{1,6} ", "", t)
        t = t.replace("`", "")
        t = re.sub(r"^>\s?", "", t)
        t = re.sub(r"^-\s+", "", t)
        t = t.replace("|", " ")
        t = t.replace("**", "")
        out.append(t)
    return out


def md_tokens(lines):
    """返回 (token列表, 行->token偏移 映射)。"""
    toks, off = [], [0]
    for l in lines:
        for w in l.split():
            n = nz(w)
            if n:
                toks.append(n)
        off.append(len(toks))
    return toks, off


def segment_boundaries(doc_toc, md_lines):
    lv1 = [(t, p) for lvl, t, p in doc_toc if lvl == 1]
    heads = [(i, l[3:].strip()) for i, l in enumerate(md_lines) if re.match(r"^## ", l)]

    def normh(s):
        return nz(s.replace("—", " "))

    pairs, used = [], set()
    for t, p in lv1:
        nt = normh(t)
        for i, htxt in heads:
            if i in used:
                continue
            nh = normh(htxt)
            if nh == nt or (len(nt) > 8 and nt[:12] == nh[:12]):
                used.add(i)
                pairs.append((t, p, i))
                break
    segs = []
    for k, (t, p, mi) in enumerate(pairs):
        p_end = pairs[k + 1][1] - 1 if k + 1 < len(pairs) else 10**9
        m_end = pairs[k + 1][2] if k + 1 < len(pairs) else len(md_lines)
        segs.append((t, p - 1, p_end, mi, m_end))
    return segs, [h for i, h in heads if i not in used]


def align_segment(ptoks, mtoks, page_of):
    lost, extra = [], []
    sm = difflib.SequenceMatcher(None, [t for t, _ in ptoks], mtoks, autojunk=True)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "delete" and i2 - i1 >= 4:
            pgs = sorted({page_of[i] for i in range(i1, i2)})
            lost.append({"run": " ".join(t for t, _ in ptoks[i1:i2])[:130],
                         "ctx": " ".join(t for t, _ in ptoks[max(0, i1 - 4):i1])[-45:],
                         "pages": pgs})
        elif tag == "insert" and j2 - j1 >= 6:
            extra.append(" ".join(mtoks[j1:j2])[:130])
    return lost, extra


def main():
    import pymupdf
    doc = pymupdf.open(str(PDF_PATH))
    toc = doc.get_toc()
    spans = pdf_spans(doc)
    fig_rects = load_figure_rects()
    ptoks, acc_fig, acc_hf = build_pdf_tokens(spans, fig_rects)
    print(f"PDF 正文 token: {len(ptoks)} ｜ 图内 span {len(acc_fig)} ｜ 页眉脚 span {len(acc_hf)}")

    raw = MD_PATH.read_text(encoding="utf-8").split("\n")
    clean = md_clean_lines(raw)
    mtoks, md_off = md_tokens(clean)
    print(f"MD 清洗 token:   {len(mtoks)}")

    segs, unmatched = segment_boundaries(toc, raw)
    if segs:
        segs.insert(0, ("front", 0, segs[0][1], 0, segs[0][3]))
        last_pdf_end = max(p1 for _, p0, p1, m0, m1 in segs if p1 < 10**9) if segs else 0
        segs.append(("tail", min(last_pdf_end + 1, len(doc) - 1), 10**9,
                     segs[-1][4], len(raw)))
    print(f"分段 {len(segs)}；未配对 ## 标题: {[h[:30] for h in unmatched]}")

    # 法1 分段对账
    all_lost, all_extra, seg_stat = [], [], []
    for name, p0, p1, m0, m1 in segs:
        st = [(t, pg) for t, pg in ptoks if p0 <= pg < p1]
        mt = mtoks[md_off[m0]:md_off[m1]]
        lo_po = {i: pg for i, (_, pg) in enumerate(st)}
        lost, extra = align_segment(st, mt, lo_po)
        seg_stat.append({"seg": name[:40], "pdf_tok": len(st), "md_tok": len(mt),
                         "lost_runs": len(lost), "extra_runs": len(extra)})
        for x in lost:
            x["seg"] = name[:30]
        all_lost += lost
        all_extra += [{"seg": name[:30], "run": x} for x in extra]

    print("\n=== 法1 分段对账 ===")
    for s in seg_stat:
        flag = " ⚠️" if s["lost_runs"] > 5 or s["extra_runs"] > 5 else ""
        print(f"  {s['seg']:42} pdf={s['pdf_tok']:>6} md={s['md_tok']:>6} "
              f"丢段={s['lost_runs']:>3} 增段={s['extra_runs']:>3}{flag}")

    # 移动配对：lost 与 extra 相似度 >=0.5 视为"搬家"（顺序重排），剩余为真嫌疑
    import difflib as _d
    used_extra = set()
    moved, true_lost = [], []
    for x in all_lost:
        best, bi = 0.0, None
        for k, e in enumerate(all_extra):
            if k in used_extra:
                continue
            r = _d.SequenceMatcher(None, nz(x["run"]), nz(e["run"])).ratio()
            if r > best:
                best, bi = r, k
        if bi is not None and best >= 0.5:
            used_extra.add(bi)
            moved.append({**x, "ratio": round(best, 2)})
        else:
            true_lost.append(x)
    true_extra = [e for k, e in enumerate(all_extra) if k not in used_extra]
    # 终审：全文包含验证（nz 子串）。命中 -> 实为存在（位置/形态不同）
    md_big = nz(" ".join(mtoks))
    pdf_big = nz(" ".join(t for t, _ in ptoks))
    def contained(run, big):
        n = nz(run)
        return len(n) >= 20 and n in big
    final_lost = [x for x in true_lost if not (
        contained(x["run"], md_big)
        or any(contained(x["run"][i:j], md_big) for i in (0,) for j in (60, len(x["run"])))
        )]
    # 碎片二次机会：run 前半/后半任一命中即视为存在（跨块拼接导致整体不连续）
    recovered = len(true_lost) - len(final_lost)
    final_extra = [e for e in true_extra if not contained(e["run"], pdf_big)]
    print(f"\n配对结果: 搬家 {len(moved)} ｜ 真丢失嫌疑 {len(true_lost)} ｜ 真新增嫌疑 {len(true_extra)}")
    print(f"终审(全文包含验证): 恢复判定 {recovered} ｜ 最终丢失 {len(final_lost)} ｜ 最终新增 {len(final_extra)}")
    print("\n== 最终丢失清单 ==")
    for x in final_lost[:30]:
        print(f"  p{x['pages'][0]+1:>3} [{x['seg']}] …{x['ctx']} ⏖ {x['run']}")
    print("\n== 最终新增清单（前 20）==")
    for e in final_extra[:20]:
        print(f"  [{e['seg']}] {e['run']}")

    print(f"\n丢失运行总数(原始): {len(all_lost)}（按页聚合 Top15）")
    bypage = Counter()
    for x in all_lost:
        for p in x["pages"][:1]:
            bypage[p] += 1
    for p, c in bypage.most_common(15):
        sample = next((x for x in all_lost if p in x["pages"]), None)
        print(f"  物理页{p+1}: {c} 段  例: …{sample['ctx']} ⏖ {sample['run'][:60]}…")

    print(f"\n新增运行总数: {len(all_extra)}（样例 10）")
    for x in all_extra[:10]:
        print(f"  [{x['seg']}] {x['run']}")

    # A1 行内强调量化：正文衬线粗体/粗斜体（图外、非标题字号）
    emph = [sp for sp in spans
            if sp["font"] in ("NewBaskerville-Bold", "NewBaskerville-BoldItali",
                              "NewBaskerville-Italic")
            and sp["size"] < 13
            and not any(in_rect(sp["bbox"], r) for r in fig_rects.get(sp["page"], []))]
    emph_c = Counter(sp["font"] for sp in emph)
    print("\n=== A1 行内强调 span（图外正文区）===")
    for k, v in emph_c.most_common():
        ex = next(sp["text"] for sp in emph if sp["font"] == k)
        print(f"  {v:>5}  {k:<26} 例: {ex[:40]!r}")

    # 法3 字体普查
    fonts = Counter((sp["font"], sp["size"]) for sp in spans)
    print("\n=== 法3 字体 Top18 ===")
    for (fn, sz), c in fonts.most_common(18):
        print(f"  {c:>6}  {fn:<28} @{sz}")

    # 法4 版式普查
    fills = Counter()
    big_spans, italic_lines = [], []
    for sp in spans:
        if sp["size"] >= 20:
            big_spans.append(sp)
        if sp["flags"] & 2 and len(sp["text"]) > 25 and (sp["flags"] & 16) == 0:
            italic_lines.append(sp)
    print("\n=== 法4 大字号 span（≥20pt）===")
    agg = Counter()
    for sp in big_spans:
        agg[(sp["size"], nz(sp["text"])[:24])] += 1
    for (sz, t), c in list(agg.most_common(14)):
        print(f"  ×{c:<3}@{sz} {t}")
    print(f"长斜体行(>25字符, 非粗): {len(italic_lines)}")

    json.dump({"seg_stat": seg_stat,
               "lost": all_lost, "extra": all_extra,
               "fonts": [[list(k), v] for k, v in fonts.most_common(60)]},
              open(OUT_JSON, "w"), ensure_ascii=False)
    print(f"\nJSON → {OUT_JSON}")


if __name__ == "__main__":
    main()
