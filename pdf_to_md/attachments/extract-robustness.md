# 提取健壮性修复记录（图链/边注/附录 E）

对应 pdf-to-md-plan.md「已知遗留」中已修复条目的根因与修法存档。

## Fig 7.8（p236）曾缺失于 MD

图注标签 "Figure 7.8" 用 `FranklinGothic-Demi`（即 `chapter_label` 页眉字体）设置且位于底部边距（行 `y1≈591.7 > 页高-75`），被 span 级页脚剔除误判为页脚而整行删除 → 该块不再以 "Figure 7.8" 起头，无法被判为 `figure_caption`，`insert_figures` 不命中，MD 缺 `![Fig 7.8]`、破坏 PDF↔MD 一一对应。
**修法**：`pipeline/extract.py` 对 `_FIGURE_CAPTION_RE` 起头的块整体豁免页眉/页脚剔除（逐 span 也保留）。该正则与 classify 共用同一 `^Figure X.Y` 结构模式，未硬码页号/坐标。修复后 MD 图链 128↔PDF 128 一一对应。

## Fig 6.5 / Fig 7.11 曾整体消失（静默回归）

P0 区域生长对这两页过度包含（Fig6.5 clip 吞掉图注与正文引用行、Fig7.11 clip 近整页），叠加"manifest clip 块级精判"（块内过半行中心落在 clip 内→整块剔除）把图注/引用块一并删掉；图文同灭导致 §3 断言与图文配对全部**静默通过**。
**修法**：extract 的 clip 精判处对 `_FIGURE_CAPTION_RE` 起头块豁免（与 Fig7.8 同哲学）；md_lint 增加 R10（manifest figure ↔ MD 出现性对账），此类事故今后 verify 必报。

## 附录 E 图内文字残留曾泄漏正文流

附录 E 图由 patches 渲染、不在 P0 manifest clips 内，extract 启发式此前假设"无 manifest 图=无烘焙文字"并关闭字体守卫，E.1/E.3 页图内标签（Pretrained×4、Inputs×4、"LoRA matrices…"公式行等 ~35 行）漏进正文流。
**修法**：extract 增加附录 E 图注检测（`_APPENDIX_CAP_RE` + 复用 patches `_figure_region` 生长区域），区域作为 `burned_rects` 传入 `is_figure_text`：中心在区内或近邻 <30pt 的短块无条件剔除；以 `Figure E.N` 起头的块豁免（其 bbox 常上探进区域，整块剔除会连图注一起丢）。

## 页边注（side notes）曾 86% 丢失

全书 `HumanistMann` 字体右侧页边注共 522 句。`figure_text_detect.is_figure_text` 的 HumanistMann 邻近守卫（距图元 <30pt 即判图注标签丢弃）把紧邻图的右边注误删（如 p24 "Algorithms that learn rules…"，x0≈377 仅比图 union.x1=373 右移 4pt）。
**修法**：`is_figure_text` 增加 `page_width` 参数，HumanistMann 分支对右侧页边栏（`bbox.x0 > page_width*0.62`）块豁免丢弃（真实图内标签不在此列，HIGH 相交规则不受影响；Fig 2.13 token 数组仍正确丢弃）。缺失由 449/522(86%) 降至 23/522(4%)；MD 由 11409→12359 行。差分审计工具：`format_scan.py` + `format_audit.py`。
