#!/bin/bash
# 旧链端到端：按历史执行顺序运行全部修复脚本，失败即停
cd /home/zmz/LLMs-from-scratch/pdf_to_md || exit 1
PY=.venv/bin/python
set -e
echo "=== 1 fix_headings ===";      $PY fix_headings.py --apply
echo "=== 2 fix_chapters ===";      $PY fix_chapters.py --apply
echo "=== 3 fix_special_blocks ==="; $PY fix_special_blocks.py --apply
echo "=== 4 clean_figure_text ==="; $PY clean_figure_text.py --apply
echo "=== 5 fix_line_continuity ==="; $PY fix_line_continuity.py --apply
echo "=== 6 fix_missing_sections ==="; $PY fix_missing_sections.py
echo "=== 7 fix_pseudo_headings ==="; $PY fix_pseudo_headings.py
echo "=== 8 fix_index ===";         $PY fix_index.py
echo "=== 9 render_appendix_figures ==="; $PY render_appendix_figures.py
echo "=== 10 fix_format_consistency ==="; $PY fix_format_consistency.py
echo "=== 11 fix_chapter_covers ==="; $PY fix_chapter_covers.py
echo "=== 12 rebuild_toc ===";      $PY rebuild_toc.py
echo "=== OLD CHAIN DONE ==="
