#!/bin/bash
# PDF → Markdown 单一入口（阶段 4 编排层）。
# 数据流：P0 图片提取 → pipeline/extract → classify → merge → render → verify。
set -e; cd "$(dirname "$0")"
# 方案原定 PY=.venv/bin/python；当前环境 .venv 无 pymupdf，实际使用系统 python3。
PY=python3

# P0：已有产物（manifest.json）则跳过，避免全量重渲染 128+ 图。
if [ -f extracted_images/manifest.json ]; then
    echo "=== P0: extracted_images/ 已存在，跳过 ==="
else
    echo "=== P0: 提取插图 ==="
    $PY p0_extract_images.py
fi

echo "=== pipeline: extract → classify → merge → render ==="
$PY pipeline/run_pipeline.py

echo "=== verify: 完备性对账 ==="
$PY pipeline/verify.py
