# Diff 分诊记录（阶段 3.2）

基准：`golden/llms-from-scratch.md`（旧链产出，权威）
新输出：`/tmp/new_pipeline.md`（pipeline/run_pipeline.py 产出）

最后统计：全量 diff ≈ 9107 行；规范化（去 `<a id>`）后 ≈ 7580 差异行，分类如下：
- anchor（slug 后缀）：45
- toc_link：0（已修复，无差异）
- inline_code（反引号）：179（已大幅修复，从缺 1334 降到缺 179）
- heading（标题）：70
- prose_text：5696（绝大多数是下面若干系统性差异造成的整段错位 + 折行）
- other：1590

## 已完成修复（已通过 run_global_asserts + verify 全 PASS + 3 验证脚本不劣于 baseline）

1. **概念框重复标题**：`_is_concept_box` 的 bodies 未排除标题行，导致 `> **X**` 后重复 `> X`。已修（排除首行）。
2. **NOTE 渲染格式**：note 分支原输出 `> NOTE...`，改为 `> **NOTE** body`（搬运 fix_notes）。数量 29 已对齐 golden。
3. **This chapter covers 的 bullets 误判为概念框**：这些 bullet 落在含 "This chapter covers" 的 CALLOUT_FILL drawing 内，classify 只在单块级跳过，整框未跳过。已加 `skip_rects`（搬运 collect_concept_boxes 的整框跳过）。
4. **附录标题缺失/格式**：TOC 条目用 em dash（`appendix A—Introduction to PyTorch`）而 PDF 块用换行；heading 匹配折叠换行 + em dash→空格；render heading 分支加 `Appendix` 首字母大写。5 个附录主标题已对齐 golden。
5. **标题 TOC 匹配前缀回退**：子标题块（如 `appendix D\nAdding bells and whistles`）是 TOC 条目的前缀，新增 `_match_heading` 前缀回退。
6. **内联代码反引号（prose 块）**：新增 `_apply_inline_code`，对 prose 块中 Courier 字体 span 包反引号。反引号数 1208→2382（golden 2542，仍差 179）。

## 剩余回归（必须回到阶段 2 修复，禁止改 golden）

### R1. 连字符/断词合并过度（确定性回归）
- `in-progress`(golden) → `inprogress`(new)
- `human-like` 被拆成 `human-`/`like` 跨行
- `live- Book`(golden，含空格) → `liveBook`(new)
- 根因：`merge.dehyphenate_text` 把真连字符词也合并了；golden 的 dehyphenate 保留真连字符词（KEEP_HYPHEN_PREFIXES 应覆盖）。需核对 `fix_line_continuity.dehyphenate_line` 与 `KEEP_HYPHEN_PREFIXES` 搬运是否完整。

### R2. 撇号/右单引号丢失（提取层）
- `book’s`(golden, U+2019) → `books`(new)
- 根因：extract 拼接 span 文本时撇号丢失，或 norm/dehyphenate 把它清掉。需核对 `p1_text_stream` 与 `fix_special_blocks.norm` 对 U+2019 的处理（golden 保留 ’）。

### R3. Figure 标题渲染格式（render 回归）
- golden：`**Figure 1.1** As this hierarchical...`（加粗标题 + 正文同行）
- new：`Figure 1.1\nAs this hierarchical...`（无加粗、换行）
- 根因：`render.figure_caption` 分支未实现 `bold_captions`（p2_clean.py:288 搬运遗漏）+ 标题/正文同行合并。任务 2.5 第 4 项 `bold_captions` 未搬运。

### R4. callout 误判为 bullet（golden `>` → new `-`）
- `> Check out my blog...` → `- Check out my blog...`
- `> Visit the book’s GitHub...` → `- Visit the books GitHub...`
- 根因："About the author" 区块的链接列表在 golden 里是 callout（>），新管道判为 bullet。需核对旧链对这些块的处理（可能是 concept_box 或 note 的字体/颜色判定，或 fix_special_blocks 的某规则）。

### R5. TOC 子条目缺失（§5.4 / §7.4 / E.2 等）
- golden TOC 含 `5.4 Loading and saving model weights in PyTorch`、`7.4 Creating data loaders...`、`E.2 Preparing the dataset` 等 level-3 子标题条目；new 正文里**完全没有这些标题**（连 `### 5.4` 都不存在）。
- 根因：这些子标题块（字体 FranklinGothic-DemiItal 12.5）在 extract 阶段就未被放入 `page.blocks`（疑似被 `is_figure_text` 或某种过滤剔除，或与 figure 区域重叠被丢弃）。需核对 extract 对这些子标题块的提取，确保不被图区/页眉过滤误杀。
- 注：heading:70 差异中大部分因此类子标题 + slug 后缀导致。

### R6. slug 去重后缀不一致（anchor 45 差异）
- golden 附录 slug 为 `#appendix-a`（无后缀），new 为 `#appendix-a-2`（因正文有两个 "Appendix A" 标题块：level-1 主标题 + TOC level-2 的 "Appendix A" 320页条目）。
- 需核对 `rebuild_toc.slugify`/`assign_anchors` 的重复处理（golden 首次 slug 优先、重复项复用，而非加 -2）。

## 验证状态（接手前确认）
- `run_global_asserts`：PASS
- `verify.py`：ALL PASS（含概念框容差已修正为 golden 基准 111 ±15）
- `check_figure_text`/`check_figures`/`audit_md_quality` 对新输出结果 = baseline（不劣）
- 3 个验证脚本 stdout 已与 `golden/baseline_*.txt` 对比一致

## 接手建议
按方案任务 2.5 补齐未搬运的 render 逻辑（R3 bold_captions、R1 dehyphenate 词表、R2 撇号），
按任务 2.3/2.2 修正 classify/extract（R4 callout 判定、R5 子标题提取遗漏），
按任务 2.5-7 修正 slug 去重（R6）。每修一处重跑 `run_pipeline.py --out /tmp/new_pipeline.md` + diff 分诊。
