# Listing 旁注归位为代码注释（2025-08）

> **[2026-08 已升级]** 尾插方案被行级定位归位取代（按箭头落点插入目标
> 行上方），并修复旁注在 extract 阶段被图区种子误删的遗漏——见
> [listing-callout-positioning.md](listing-callout-positioning.md)。以下为历史记录。

对应 pdf-to-md-plan.md「Listing 旁注」条目。结构信号链：`classify._mark_listing_callouts` 打标 → `merge.merge_listing_callouts` 归位。

## 问题（改造前）

Manning 排版中 Listing 旁有注释短语（HumanistMann 9pt + 黑色箭头 drawing 指向对应代码行）。PyMuPDF 把短语提取为独立文本块，MD 中渲染为散落在代码围栏后的无主段落（如 Listing 2.3 后的 "original text tokens" 等 4 段），与正文段落无法区分、丢失"属于该代码"的语义。

## 判定（三条件缺一不可，防边注误收）

1. 已是 annot 块（HumanistMann/Arial ≤12pt 短文本，`_mark_annot_blocks`）；
2. bbox 与同页某 code block 纵向重叠 >5pt；
3. 页面存在小黑 drawing（箭头三角/连线 ≤12pt）且 y 中心距块 ≤40pt——**组级几何锚定**，边注（无指示符）天然排除。

验证数据：全书标记 229 块；44 条含句末标点的样本逐一核验全为真旁注（callout 本就常是完整句）；6 条"长句疑似边注"复核中 5 条距箭头 0–15pt 确凿归位、1 条（p187 边注，距 86pt）未标记且完好保留于正文流。约 10 块漏标（箭头形态特殊）维持散段落，无害。

## 归位（两级归属）

- 一级流序：紧跟 code block 之后 ≤6 个块内直接挂；
- 二级几何兜底：间隔超限者按同页 y-overlap 最大的 code block 归属（旁注与代码间可能隔图注/段落块）。
- 文本折叠为单行 `# ...` 追加到 fence 内部尾部；两级都失败维持散段落。

## 连带修复与校验

- 断词损坏顺手修复：`nexttoken`→`next-token`、`jsonformatted`→`json-formatted` 入 KNOWN_BREAKS（改造前散段落时代即已损坏，非本次引入）。
- verify 第 9 步：classify 后 merge 前快照 callout_texts（run_pipeline 传参），逐条以字母数字归一形式校验其出现在围栏内——229↔229 全对账。
- 效果：散段落清零、围栏平衡保持（1222 行偶数）、fence 内 `# ` 注释 239 行。
