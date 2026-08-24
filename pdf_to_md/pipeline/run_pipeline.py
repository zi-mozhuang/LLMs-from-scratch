#!/usr/bin/env python3
"""pipeline.run_pipeline — 单一入口：PDF → 最终 Markdown。

数据流：extract_book → classify_pages → merge_all → render → run_global_asserts → 写出。
支持 --out（默认 llms-from-scratch.md）。
"""

import argparse
import sys
from pathlib import Path

# 允许以 `python pipeline/run_pipeline.py` 直接运行（根目录加入 sys.path）。
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mdlib import config
from mdlib.asserts import run_global_asserts
from pipeline.extract import extract_book
from pipeline.classify import classify_pages
from pipeline.merge import (
    merge_box_fragments,
    merge_box_code,
    merge_summary_items,
    merge_listing_callouts,
    merge_numbered_lists,
    merge_pending_prose,
    merge_pending_code,
    merge_two_line_headings,
    strip_box_continuation_markers,
)


def merge_all(blocks: list) -> list:
    """按顺序合并块（保持与旧链等价的行为）。
    注：断词处理已移至 render 行级（搬运 p2_clean 第 351-352 行的位置语义）。"""
    # URL scheme 空格前置清理：merge 阶段的 URL 断裂判定（_URL_TAIL_RE）
    # 依赖 'https://' 后无空格；render 层的同名清理保留作兜底（幂等）。
    import re as _re
    for b in blocks:
        if b.text and "http" in b.text:
            b.text = _re.sub(r"(https?://)\s+", r"\1", b.text)
    # 同矩形概念框碎片合并（须先于 prose 合并：框内段落不得被框外段落吸收，
    # 框内碎片也不得吸收框外正文）
    blocks = merge_box_fragments(blocks)
    # 章末 Summary 列表重组（先于 prose 合并：summary_list 的折行续块
    # 须并入列表项，不得被 merge_pending_prose 吸收成独立段落）
    blocks = merge_summary_items(blocks)
    # 编号列表重组（先于 prose 合并：连续递增编号的 prose 块须成 ol_list，
    # 不得被段落合并链吸收成裸段落）
    blocks = merge_numbered_lists(blocks)
    # 跨页概念框 "(continued)" 排版残标剔除（盒碎片合并后执行）
    blocks = strip_box_continuation_markers(blocks)
    # Listing 旁注归位（先于 prose 合并：旁注块从块流中删除并入代码围栏；
    # 先于 pending_code：定位基于 classify 原始行表，与 meta 对齐可靠）
    blocks = merge_listing_callouts(blocks)
    print(f"[pipeline] listing callouts 归位: {merge_listing_callouts.last_attached} 块")
    # 两行标题合并
    blocks = merge_two_line_headings(blocks)
    # 跨块段落合并
    blocks = merge_pending_prose(blocks)
    # 代码清单合并
    blocks = merge_pending_code(blocks)
    # 概念框内代码合成复合引用框（须在 pending_code 后：PDF 拆碎的框内
    # 代码块先并回单块，才能整体作为一条 code body 入链）
    blocks = merge_box_code(blocks)
    return blocks


def run(out_path: Path, run_verify: bool = False) -> int:
    print(f"[pipeline] extract_book({config.PDF_PATH.name}) ...")
    pages = extract_book(str(config.PDF_PATH))
    print(f"[pipeline] classify_pages ({sum(len(p.blocks) for p in pages)} blocks) ...")
    blocks = classify_pages(pages)
    # Listing 旁注对账快照（须在 merge 消费前提取，供 verify 第 9 步校验；
    # 行级单元制下短语按 callout_lines 的行文本计，块文本可能跨组混排）
    callout_texts = []
    for b in blocks:
        if not b.meta.get("listing_callout"):
            continue
        for items in (b.meta.get("callout_lines") or {}).values():
            callout_texts.extend(t for t, _cy in items if t.strip())
    callout_texts = [" ".join(t.split()) for t in callout_texts]
    print(f"[pipeline] merge_all ({len(blocks)} blocks) ...")
    blocks = merge_all(blocks)
    # 框内代码对账（verify 第 10 步）：取 merge 实际合成的 code body 数
    # （classify 物理块数会因 pending_code 合并而大于逻辑围栏数）
    in_box_codes = merge_box_code.last_codes
    print(f"[pipeline] render ...")
    text = render_blocks(blocks)
    run_global_asserts(text)
    out_path.write_text(text, encoding="utf-8")
    # 渲染语义 lint：--verify 时由 verify 第 6 步做权威校验（避免同进程跑两遍）
    if not run_verify:
        from pipeline.md_lint import main as lint_main
        lint_main([str(out_path)])
    print(f"[pipeline] wrote {out_path} ({len(text.splitlines())} lines)")
    # 图片 manifest 元数据增强（source/md5/caption，增量幂等）
    from pipeline.manifest_enrich import enrich_manifest
    if enrich_manifest(config.MANIFEST_PATH, text):
        print("[pipeline] manifest enriched (source/md5/bytes/caption)")
    if not run_verify:
        return 0
    # 进程内完备性对账：复用 pages（配合 extract 缓存，sh 全程只解析一次 PDF）
    from pipeline.verify import main as verify_main
    return verify_main(out_path, pages, blocks, callout_texts, in_box_codes)


def render_blocks(blocks: list) -> str:
    from pipeline.render import render
    return render(blocks, config.MANIFEST_PATH)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=str(config.MD_PATH),
                    help="输出 Markdown 路径（默认 llms-from-scratch.md）")
    ap.add_argument("--verify", action="store_true",
                    help="落盘后进程内执行 verify 完备性对账（复用页模型）")
    args = ap.parse_args()
    code = run(Path(args.out), run_verify=args.verify)
    return code if isinstance(code, int) else 0


if __name__ == "__main__":
    raise SystemExit(main())
