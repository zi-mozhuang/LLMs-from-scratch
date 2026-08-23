#!/bin/bash
# PDF → Markdown 单一入口（阶段 4 编排层）。
# 数据流：P0 图片提取 → pipeline/extract → classify → merge → render → verify。
set -e; cd "$(dirname "$0")"
# 方案原定 PY=.venv/bin/python；当前环境 .venv 无 pymupdf，实际使用系统 python3。
PY=python3

# P0：已有产物（manifest.json）则跳过，避免全量重渲染 128+ 图。
# 注：p0_extract_images.py 本身无跳过守卫（重跑会 rmtree 后全量提取），守卫在此编排层实现。
if [ -f extracted_images/manifest.json ]; then
    echo "=== P0: extracted_images/ 已存在，跳过 ==="
else
    echo "=== P0: 提取插图 ==="
    $PY p0_extract_images.py
fi

echo "=== pipeline: extract → classify → merge → render → verify ==="
# --verify：进程内对账（复用页模型 + extract 磁盘缓存，全程只解析一次 PDF）
$PY pipeline/run_pipeline.py --verify
