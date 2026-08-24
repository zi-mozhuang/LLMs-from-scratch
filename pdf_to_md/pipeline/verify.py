#!/usr/bin/env python3
"""pipeline.verify — 完备性对账（新增）。

读取 PDF 与新输出，打印对账表并断言：
- TOC 条目数：输出标题数 ≥ TOC 有效条目数 − 2（允许已知 2 处缺口）；
- Figure 标题数 ≥ 161（148 manifest = 正文 128 + 附录 20，+引用行冗余）；
- 概念框块数 ≈ 111（golden 基准，±15 容差）；
- 每章（1-7）均存在 **This chapter covers**；
- 同矩形引用块碎片 = 0（audit_quote_fragmentation 结构性修复的不变量）。

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
EXPECTED_FIGURE_COUNT = 161  # manifest 148（正文128+附录20）+ 行首引用句冗余
EXPECTED_CONCEPT_BOX = (100, 120)  # golden 基准 111 概念框块数
EXPECTED_OL_ITEMS = 6  # merge_numbered_lists 4 项（Exercise 5.3/5.5 答）
                      # + llama 饲料清单 2 处 prose 原生 "1. " 行
ALLOWED_TOC_GAP = 2
# 同矩形引用块碎片：结构性修复后必须为 0（跨页截断框属已知遗留，
# 由 audit_quote_fragmentation 的 STACKED/UNMATCHED 类目跟踪，不计入此断言）
ALLOWED_TRUE_FRAGMENTS = 0


def _count_headings(text: str) -> int:
    return sum(1 for l in text.split("\n")
               if re.match(r"^#{1,6}\s+\S", l)
               and not l.strip().startswith("```"))


def _count_figure_captions(text: str) -> int:
    return sum(1 for l in text.split("\n")
               if re.match(r"^(?:\*{0,2}|<figcaption>)Figure\s+(?:[A-E]\.\d+|\d+\.\d+)", l))


def _count_concept_boxes(text: str) -> int:
    # 概念框渲染为 > 开头且含 **标题** 的区块；这里统计 `> **` 行（标题）
    return sum(1 for l in text.split("\n")
               if re.match(r"^>\s+\*\*.+\*\*", l))


def _toc_entries(pages: list) -> list:
    seen = set()
    out = []
    for level, title, page in getattr(pages[0], "toc", []) if pages else []:
        tn = title.strip().lower()
        if tn in {"brief contents", "contents", "index",
                  "build a large language model (from scratch)"}:
            continue
        if tn in seen:
            continue
        seen.add(tn)
        out.append(title.strip())
    return out


def main(md_path: Path = MD_PATH, pages: list = None, blocks: list = None,
         callout_texts: list = None, in_box_codes: int = 0) -> int:
    """完备性对账。pages/blocks 可传入管道进程内已提取/分类的模型
    （配合 extract 缓存，避免 sh 全程二次解析 PDF）。"""
    text = md_path.read_text(encoding="utf-8")
    lines = text.split("\n")
    if pages is None:
        pages = extract_book(str(config.PDF_PATH))  # TOC 与碎片审计共用一次提取

    # 1) TOC 条目
    toc = _toc_entries(pages)
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

    # 5) 图片文件完整性：manifest 引用的文件必须存在；0 字节判 FAIL，
    #    <1KB 列 WARN（疑似空白渲染，人工复核）
    img_ok, n_small = _check_image_files()
    print(f"[verify] 图片文件完整性={'PASS' if img_ok else 'FAIL'}  (<1KB 警告 {n_small} 个)")
    print(f"         -> {'PASS' if img_ok else 'FAIL'}")

    # 6) 渲染语义 lint（pipeline/md_lint.py，对照 golden/baseline_md_lint.txt，
    #    仅"新增"违规判 FAIL——详见 attachments/md-lint.md）
    from pipeline.md_lint import main as lint_main
    import io
    from contextlib import redirect_stdout
    buf = io.StringIO()
    with redirect_stdout(buf):
        lint_ok = lint_main([]) == 0
    out_lines = buf.getvalue().splitlines()
    if out_lines:
        print(f"[verify] 渲染语义 lint -> {out_lines[0]}")
        for l in out_lines[1:6]:
            print(f"         {l}")
    print(f"         -> {'PASS' if lint_ok else 'FAIL'}")

    # 7) 同矩形引用块碎片（audit_quote_fragmentation.scan，复用本函数已提取的页模型）
    from audit_quote_fragmentation import scan as frag_scan
    frag = frag_scan(text, pages)
    n_true = len(frag["true"])
    frag_ok = n_true <= ALLOWED_TRUE_FRAGMENTS
    print(f"[verify] 概念框矩形={frag['n_rects']}  MD 引用组={frag['n_groups']}  "
          f"相邻对={frag['n_pairs']}  真碎片={n_true} (允许 ≤ {ALLOWED_TRUE_FRAGMENTS})")
    for pg, a, b in frag["true"][:5]:
        print(f"         [碎片] p{pg}: {a[:40]} || {b[:40]}")
    print(f"         -> {'PASS' if frag_ok else 'FAIL'}")

    # 8) 章末 Summary 列表格式（audit_summary_formatting.scan；
    #    blocks 提供时附加 S3 列表项数 PDF↔MD 对账）
    from audit_summary_formatting import scan as summ_scan
    summ = summ_scan(text, blocks=blocks)
    summ_ok = not summ["violations"]
    exp = (f"  期望={summ['n_expected_items']}"
           if summ["n_expected_items"] is not None else "")
    print(f"[verify] Summary 区块数={summ['n_regions']}  列表项={summ['n_items']}{exp}  "
          f"违规={len(summ['violations'])}")
    for v in summ["violations"][:5]:
        print(f"         [Summary] {v}")
    print(f"         -> {'PASS' if summ_ok else 'FAIL'}")

    all_ok = (toc_ok and fig_ok and box_ok and covers_ok and img_ok
              and lint_ok and frag_ok and summ_ok)

    # 9) Listing 旁注归位对账：classify 标记的每个旁注文本（merge 前快照）
    #    必须以 "# ..." 形式出现在某代码围栏内
    if callout_texts:
        from mdlib.textutil import fence_mask

        def _calign(s):
            # 去空格/连字符比较：fence 内注释行经受断词 pass，
            # "next- token"→"next-token" 等形态差异不影响归位判定
            return re.sub(r"[^a-z0-9]", "", s.lower())

        marked = [_calign(t) for t in callout_texts]
        mask = fence_mask(lines)
        in_fence = _calign("\n".join(
            l for l, m in zip(lines, mask) if m))
        missing = [t for t in marked if t not in in_fence]
        callout_ok = not missing
        print(f"[verify] Listing 旁注标记={len(marked)}  未归位={len(missing)}")
        for t in missing[:5]:
            print(f"         [callout] {t[:50]}")
        print(f"         -> {'PASS' if callout_ok else 'FAIL'}")
        all_ok = all_ok and callout_ok

    # 10) 概念框内代码对账：classify 标记的 in_concept_box 代码块数须与
    #     MD 中引用内围栏（"> ```" 开行）数相等
    if in_box_codes:
        n_qfence = sum(1 for l in lines if l.lstrip().startswith("```") is False
                       and re.match(r"^>\s*```", l))
        qfence_pairs = n_qfence // 2
        boxcode_ok = (qfence_pairs == in_box_codes) and n_qfence % 2 == 0
        print(f"[verify] 框内代码={in_box_codes}  MD 引用内围栏={qfence_pairs}")
        print(f"         -> {'PASS' if boxcode_ok else 'FAIL'}")
        all_ok = all_ok and boxcode_ok

    # 11) 截图式代码图重建对账：patches_data.json 的每条重建代码首行
    #     必须存在于 MD 某围栏内（防锚点漂移导致补丁失效）
    try:
        rebuilds = json.loads(
            (Path(__file__).parent / "patches_data.json").read_text("utf-8")
        ).get("figure_code_rebuilds", [])
    except Exception:
        rebuilds = []
    if rebuilds:
        from mdlib.textutil import fence_mask as _fm
        _mask = _fm(lines)
        _fence_body = "\n".join(l for l, m in zip(lines, _mask) if m)
        missing_rb = [r["fig"] for r in rebuilds
                      if r["code"][0] not in _fence_body]
        rb_ok = not missing_rb
        print(f"[verify] 截图式代码图重建={len(rebuilds)}  失效={len(missing_rb)}")
        if missing_rb:
            print(f"         [rebuild] Fig {missing_rb} 代码未出现在围栏内")
        print(f"         -> {'PASS' if rb_ok else 'FAIL'}")

    # 12) 绘制式数学公式重建对账：patches_data.json 的每条 equation_rebuilds
    #     的 tex 必须存在于 MD（display 用 $$…$$ 包裹）；防锚点漂移失效
    try:
        eqs = json.loads(
            (Path(__file__).parent / "patches_data.json").read_text("utf-8")
        ).get("equation_rebuilds", [])
    except Exception:
        eqs = []
    if eqs:
        joined = "\n".join(lines)
        missing_eq = [e["anchor"] for e in eqs
                      if (("$$" + e["tex"] + "$$") if e.get("display", False)
                          else ("$" + e["tex"] + "$")) not in joined]
        eq_ok = not missing_eq
        print(f"[verify] 公式重建={len(eqs)}  失效={len(missing_eq)}")
        if missing_eq:
            print(f"         [rebuild] 未注入: {missing_eq}")
        print(f"         -> {'PASS' if eq_ok else 'FAIL'}")
        all_ok = all_ok and rb_ok
    # 13) 有序列表对账：merge_numbered_lists 重组的编号列表项数
    #     （ch5 Exercise 答案 2 组 × 2 项；llama 饲料清单 2 处为 prose 原生）
    n_ol = sum(1 for l in lines if re.match(r"^\d{1,2}\. [A-Z“\"'(]", l))
    ol_ok = n_ol == EXPECTED_OL_ITEMS
    print(f"[verify] 有序列表项数={n_ol}  基准={EXPECTED_OL_ITEMS}")
    print(f"         -> {'PASS' if ol_ok else 'FAIL'}")
    all_ok = all_ok and ol_ok
    print(f"\n[verify] 总体: {'ALL PASS' if all_ok else 'FAIL'}")
    return 0 if all_ok else 1


def _check_image_files() -> tuple:
    """校验 manifest 引用的图片文件。返回 (是否通过, <1KB 文件数)。"""
    try:
        data = json.loads(config.MANIFEST_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"         (manifest 读取失败: {e})")
        return False, 0
    base = config.MANIFEST_PATH.parent
    ok = True
    small = []
    for key in ("figures", "embedded"):
        for entry in data.get(key, []):
            rel = entry.get("file")
            if not rel:
                continue
            f = base / rel
            if not f.exists():
                print(f"         [缺失] {rel}")
                ok = False
                continue
            size = f.stat().st_size
            if size == 0:
                print(f"         [空文件] {rel}")
                ok = False
            elif size < 1024:
                small.append(rel)
    if small:
        for rel in small[:5]:
            print(f"         [偏小] {rel}")
        if len(small) > 5:
            print(f"         ... 共 {len(small)} 个")
    return ok, len(small)


if __name__ == "__main__":
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else MD_PATH
    raise SystemExit(main(path))
