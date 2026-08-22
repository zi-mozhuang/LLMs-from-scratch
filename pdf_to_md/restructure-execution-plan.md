# PDF 转 MD 脚本系统精简重组 —— 生产执行方案

> 本文件自包含，供新模型直接执行。目标：将 21 个混杂脚本合并重组为 13 个命名统一、职责清晰的脚本 + 1 个全流程编排入口 `run_all.py`，重写方案文档结构，**不损坏任何脚本功能**。

## 0. 环境关键事实（已实测，务必遵守）

| 事实 | 值 |
|------|-----|
| Python 解释器 | `.venv/bin/python`（Python 3.14） |
| 源 PDF（唯一有效） | `Build a Large Language Model (From Scratch) (Sebastian Raschka).pdf`；**禁止**用中文文件名副本做几何类操作（文本层不同会错位） |
| 当前 `llms-from-scratch.md` | 纯 P3 输出，md5 `56ee202b4a0b9c08ee5b12a2fd85c3aa`，10932 行；**12 个修复脚本一个都未应用** |
| `text_stream_full.md`（P1 输出） | md5 `1458b4191c9e622feb21d02181c354a1` |
| `text_stream_p2.md`（P2 输出） | md5 `5a4c6c7754f946503f98bc71d38303f0` |
| 确定性 | P0-P3 纯文本/渲染处理，重跑输出逐字节一致，可随时再生基线 |
| git | 仓库根在上级目录 `/home/zmz/LLMs-from-scratch`（分支 `zmz`）；git 命令需沙箱外权限 |
| `/tmp` 不可靠 | 会话间会被清空，一切备份放工作区 `.backup/` |

**已知漂移（必须处理）**：`fix_pseudo_headings.py` 第 242 行断言在干净 P3 输出上失败——它期望损坏标题为 `### .bz/EZJR appendix C Exercise solutions`（同一行），但当前 P1 输出里 `.bz/EZJR` 是独立行（llms-from-scratch.md 第 9181 行），下方紧跟正常的 `## Appendix C Exercise solutions`。修复方式：将该规则改为"删除孤立的 `.bz/EZJR` 行"，断言保留。这是**唯一允许的脚本逻辑调整**，其余代码一律原样搬运。

## 1. 目标结构（13 脚本 + run_all，扁平，统一命名）

```
pdf_to_md/
├── run_all.py              # 新增：全流程一键编排（见 §4）
├── p0_extract_images.py    # ← extract_images.py（逻辑不动）
├── p1_text_stream.py       # ← pdf_text_stream.py
├── p2_clean.py             # ← pdf_clean_p2.py
├── p3_toc.py               # ← pdf_toc_p3.py
├── figure_text_detect.py   # 不变（更新 import：from p0_extract_images import collect_element_rects）
├── fix_structure.py        # ← fix_headings + fix_chapters + fix_pseudo_headings + fix_format_consistency + fix_chapter_covers
├── fix_content.py          # ← fix_special_blocks + fix_missing_sections + fix_index + render_appendix_figures
├── fix_flow.py             # ← clean_figure_text + fix_line_continuity
├── rebuild_toc.py          # 不变（链条最后一步）
├── check_figures.py        # ← audit_figures.py（更新 import：from p0_extract_images import ...）
├── check_figure_text.py    # ← verify_figure_text.py
├── check_md_quality.py     # ← audit_md_quality.py
└── attachments/            # 附件（补入 format-fix-round2.md 索引、line-continuity.md）
```

依赖链：`p0_extract_images.collect_element_rects` ← `figure_text_detect` ← {`p1_text_stream`, `fix_flow`, `check_figure_text`}；`p0_extract_images` ← `check_figures`。改名后须同步这些 import。

## 2. 合并细则

合并原则：**旧脚本主体原样搬入新文件成为独立函数，不重写逻辑**；每个新脚本可独立运行（默认按正确内部顺序跑全部 stage），同时支持 `--stage` 只跑指定阶段；文件头 docstring 写明来源脚本与 stage 顺序。

### fix_structure.py（约 1160 行）
| stage | 函数来源 | 原脚本调用方式 |
|-------|----------|----------------|
| A-headings | `fix_headings.py` | `--apply`（argparse：--md --pdf --apply） |
| A-chapters | `fix_chapters.py` | `--apply`（sys.argv 判断） |
| B-pseudo | `fix_pseudo_headings.py` | 直接运行；含 §0 的漂移修正 |
| B-format | `fix_format_consistency.py` | 直接运行 |
| B-covers | `fix_chapter_covers.py` | 直接运行（**必须在 B-format 之后**，撤销其误改） |

### fix_content.py（约 1000 行）
| stage | 函数来源 | 调用方式 |
|-------|----------|----------|
| blocks | `fix_special_blocks.py` | `--apply` |
| gaps-sections | `fix_missing_sections.py` | 直接运行 |
| gaps-index | `fix_index.py` | 直接运行 |
| gaps-figures | `render_appendix_figures.py` | 直接运行 |

### fix_flow.py（约 820 行）
| 顺序 | 函数来源 | 调用方式 |
|------|----------|----------|
| 1 | `clean_figure_text.py`（P5 图内文字残留） | `--apply`；PDF 自动定位英文名文件（原逻辑已含） |
| 2 | `fix_line_continuity.py` | `--apply`（迭代至收敛） |

## 3. 历史执行顺序（合并后必须严格保持）

```
p0 → p1 → p2 → p3
→ fix_headings、fix_chapters                （round 1 标题）
→ fix_special_blocks                        （round 1 异形块）
→ clean_figure_text（P5）→ fix_line_continuity（P5 之后）
→ fix_missing_sections → fix_pseudo_headings → fix_index → render_appendix_figures
→ fix_format_consistency → fix_chapter_covers   （round 2；covers 必须在 format 后）
→ rebuild_toc（最后，重建 TOC + 锚点 + §3 断言）
```

依据：各脚本 docstring 与 `attachments/format-fix-round2.md` 的执行顺序表。

## 4. run_all.py 编排

用 `subprocess` 依次调用（失败即停、打印各步修复计数）：

```
p0_extract_images.py → p1_text_stream.py → p2_clean.py → p3_toc.py
→ fix_structure.py --stage A
→ fix_content.py --stage blocks
→ fix_flow.py
→ fix_content.py --stage gaps
→ fix_structure.py --stage B
→ rebuild_toc.py
→ check_md_quality.py（收尾报告，非阻断）
```

## 5. 执行步骤与验证（按序执行，每步留痕）

**Step 0 备份**：`mkdir .backup && cp llms-from-scratch.md text_stream_full.md text_stream_p2.md .backup/`；校验三个 md5 与 §0 表一致。

**Step 1 旧链端到端基线 A**（在干净 P3 输出上）：
1. 先按 §0 修正 `fix_pseudo_headings.py` 漂移，单独验证：运行后断言通过、`.bz/EZJR` 行消失、`## Appendix C Exercise solutions` 保留。
2. 按 §3 顺序逐一运行旧脚本（需要 `--apply` 的都加上）。记录每步修复计数。
3. `cp llms-from-scratch.md .backup/result_A.md`。
4. 再生恢复基线：`.venv/bin/python pdf_toc_p3.py text_stream_p2.md llms-from-scratch.md`，md5 必须回到 `56ee202b…`。

**Step 2 合并**：按 §1/§2 创建新脚本（同时做改名与 import 更新），随后删除 12 个旧修复脚本与 4 个旧流水线/校验脚本原名文件。

**Step 3 新链等价验证**：按 §4 顺序运行新链（等价于 run_all.py，可直接写好后跑 run_all），产物与 `.backup/result_A.md` diff **必须为空**。若不等：只允许调整"搬运接线"错误（函数调用顺序/参数），不允许改修复逻辑。

**Step 4 流水线回归**：重跑 `p1_text_stream.py → p2_clean.py`，输出 md5 分别对照 `1458b419…` / `5a4c6c77…`；`p0_extract_images.py` 全量跑一次确认 V1-V3 通过。

**Step 5 校验脚本回归**：运行 `check_md_quality.py`、`check_figure_text.py <pdf> llms-from-scratch.md`、`check_figures.py`，确认无新增异常（与报告内容人工速览）。

**Step 6 文档与收尾**：
1. 重写 `pdf-to-md-plan.md`：流水线总览表换新文件名并标注 `run_all.py` 一键运行；按流水线顺序重排章节；新增"脚本结构总览"节（三类脚本分组 + 依赖链）；附件索引补全 `format-fix-round2.md`，`line_continuity_subplan.md` 移为 `attachments/line-continuity.md` 并入索引。
2. 删除 `__pycache__/`、本文件（restructure-execution-plan.md）。
3. 建议 `git add -A && git commit`（需用户同意；在仓库根目录执行）。

## 6. 红线（不可违反）

- 除 §0 声明的 `fix_pseudo_headings` 漂移修正外，**不改任何脚本的修复/提取逻辑**。
- 不改动两份 PDF、`extracted_images/` 下已有图片内容（p0/render 脚本按原逻辑覆写渲染属正常）。
- 中间产物 `text_stream_full.md`、`text_stream_p2.md` 由流水线生成，不手改。
- 每个验证步骤的 md5/计数记录在回复中留痕，失败即停并报告，不得跳过。
