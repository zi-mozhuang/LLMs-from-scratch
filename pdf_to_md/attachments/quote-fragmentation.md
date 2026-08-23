# 同矩形概念框碎片合并（2025-08）

对应 pdf-to-md-plan.md「引用块碎片」条目。结构性修复：`classify._tag_box_membership` + `merge.merge_box_fragments`。

## 问题

一个视觉概念框（CALLOUT_FILL 矩形）内的文字被 PyMuPDF 切成多个 text block；classify 按单 block 独立判定、render 逐块输出引用 + 尾部空行，导致 MD 中同一框碎成 N 个分离 blockquote（典型：§1.5 "GPT-3 dataset details" 一框三段 → 三个独立引用块）。审计确认全书 62 个概念框矩形中 24 个含多块，产生 **28 对真碎片**（含 Exercise 框标题|正文分离、macOS callout 碎片）。

## 排查工具 `audit_quote_fragmentation.py`（只读）

两层比对：
- PDF ground truth：逐页枚举 CALLOUT_FILL 矩形（与 `_is_concept_box` 同源判定），统计矩形内文本块；
- MD 启发式：扫"空行分隔的相邻引用块对"，归一化前缀/包含回查矩形归属。

分三类：
| 类目 | 含义 | 处置 |
|------|------|------|
| TRUE FRAGMENT | 前后块同属一矩形 | 结构性缺陷，须修复 |
| STACKED | 跨矩形堆叠 / 单侧命中 | 合法相邻（练习框叠放等） |
| UNMATCHED | 无法回查 PDF | 另册：章末 Summary、[BOS]/[EOS]/[PAD] 等无框 Wingdings 列表（PUA→callout 遗留语义，非同框问题） |

用法：`python3 audit_quote_fragmentation.py [--md PATH] [--report PATH]`；退出码 = 有真碎片。verify 第 7 步复用其 `scan()`。

## 修复（提取期拿对数据，非事后补丁）

1. classify `_tag_box_membership`：落入同矩形（重叠 ≥0.20，covers 整框跳过同源排除）的相邻块打 `meta["box_key"]`；heading/code/图注不入组（出现即打断，保护围栏/图链/标题）。
2. merge `merge_box_fragments`（先于 prose 合并）：连续同 key 块并回单块——首块 exercise 则吸收多段文本，否则统一转 concept_box（title 取首个非空，bodies 按 `_should_merge_prose` 句连续性拼接）；跨页不归组。
   - **标题去重守卫**：`^Exercise N.N` 块在分类顺序上先命中 concept_box（Demi span 进 meta），leader 文本会与标题重复（实测 9 处），合并后按 norm 相等丢弃首段。
3. render：concept_box 段间以 `>` 延续引用（不再输出空行）；exercise 支持多段渲染。
4. 附带修复：`fix_macos_callout` 升级触发条件（覆盖碎片合并后缺内联代码跨度的新形态），恢复丢失的两个 device 代码跨度；幂等键改为 mps 跨度存在性。

## 效果与遗留

- 真碎片 28 对 → **0**；标题重复 9 处 → 0；MD 引用组 345 → 314。
- 已知遗留：跨页截断框 3 处（Information leakage p97-98、Layer norm vs batch norm p126-127、Perplexity "(continued)" p161-162）在分页处仍呈两个引用块——PDF 两页各画一矩形，跨页归组需语义启发式（风险大于收益），维持现状。
