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
    merge_pending_prose,
    merge_pending_code,
    merge_two_line_headings,
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
    # 两行标题合并
    blocks = merge_two_line_headings(blocks)
    # 跨块段落合并
    blocks = merge_pending_prose(blocks)
    # 代码清单合并
    blocks = merge_pending_code(blocks)
    return blocks


def run(out_path: Path) -> None:
    print(f"[pipeline] extract_book({config.PDF_PATH.name}) ...")
    pages = extract_book(str(config.PDF_PATH))
    print(f"[pipeline] classify_pages ({sum(len(p.blocks) for p in pages)} blocks) ...")
    blocks = classify_pages(pages)
    print(f"[pipeline] merge_all ({len(blocks)} blocks) ...")
    blocks = merge_all(blocks)
    print(f"[pipeline] render ...")
    text = render_blocks(blocks)
    run_global_asserts(text)
    out_path.write_text(text, encoding="utf-8")
    print(f"[pipeline] wrote {out_path} ({len(text.splitlines())} lines)")
    # 图片 manifest 元数据增强（source/md5/caption，增量幂等）
    from pipeline.manifest_enrich import enrich_manifest
    if enrich_manifest(config.MANIFEST_PATH, text):
        print("[pipeline] manifest enriched (source/md5/bytes/caption)")


def render_blocks(blocks: list) -> str:
    from pipeline.render import render
    return render(blocks, config.MANIFEST_PATH)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=str(config.MD_PATH),
                    help="输出 Markdown 路径（默认 llms-from-scratch.md）")
    args = ap.parse_args()
    run(Path(args.out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
