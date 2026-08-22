# 图内文字删除详细方案

> 返回主方案 §8

## P1 几何法（`figure_text_detect.py`，被 P1 调用）

- 复用 P0 的 `collect_element_rects` 取每页所有绘图路径 rect + 位图放置 rect。
- 文本块判为图内（→删除）当满足任一：
  - **(HIGH)** bbox 与任一元素 rect 直接相交。
  - **(MED)** 中心落在元素 union 内、且位于 union 顶部 30% 区、且 ≤2 行短标签。
- **护栏（避免误删正文，优先级最高）**：字号 > 13pt → 保留（封面标题/作者/章节标题）；以句号/问号/感叹号结尾的完整句子 → 保留；纯字母短语 ≤4 词无数字 → 保留（图1.1 术语表靠此保命）。
- 集成：P1 每页预计算 `page_element_regions`，在页眉剔除后、代码块判定前对文本块调用 `is_figure_text` 跳过。

### 图内字符串数组被误当代码块的根因与修复（Fig 2.13 token 列表）

**现象**：`llms-from-scratch.md` L1148-1155 出现 ```` ```text ```` 代码块，内容是图 2.13 里展示的 token 字符串数组 `[ "city", "stood", "the", "old" ], [ "library", … ]`。该段是**图内文字**（L1146 原文："the figure shows the tokens in string format for illustration purposes"），且含损坏的中文引号 `""`（图内提取乱码），却被当成正文代码块输出。

**根因链**（在 `p1_text_stream.py` 主循环）：
1. P1 对每个 block 先调 `is_figure_text` 决定是否删除图内文字（L335）。
2. Fig 2.13 的 token 数组在 PDF 里被切成**多个独立 Courier text block**（如一个 6 行 block：`[ "city", … "relic"], [ … ]])`）。其中心落在图 union 内，但 `is_figure_text` 对它返回 **False** → 逃过图内删除。
3. 回到主循环 L340-344：该 block `courier_frac ≥ 0.75` → 判为代码块 → `flush_code()` 输出 ```` ```text ````。

**`is_figure_text` 为何放行**（旧逻辑 L107-116 Courier guard）：该守卫本意是保护正文代码清单（如 P49 `SimpleTokenizerV1`），规则为"多行(≥5) Courier 且 raw>50 且含代码字符 `[ ] ( ) = : { } import class def` → `return False`（保留）"。但 token 数组含 `[` `]` 和数组结尾的 `)`（`])` / `...]));`），触发该字符集 → 被当作"代码"放行 → 图内删除失效。

**修复**：
- **Courier guard 收窄**：字符集去掉 `(` `)` `[` `]`（圆括号/方括号在数据数组中也出现，不是代码标志），仅保留真正代码语法 `=` `:` `{` `}` `import` `class` `def`。真实代码清单仍含 `=`/`:`/`def`/`class` → 仍被该守卫保留；token 数组不含这些 → 不再被早返回。
- **新增"图内字符串数组"规则**（位于 term-glossary 之后、HIGH 相交之前）：块中心落在图 union 内 **且** 形状为带引号字符串数组（含 `[` 且 `"` 计数 ≥2 且含 `,`）**且** 无代码关键字（`=` `:` `def ` `class ` `import ` `return ` `self.`）→ 判为图内 `return True`。该规则专门匹配图内 token/字符串数组（`[ "city", … ]`），因强制要求 `"` 而**不会**误删 tensor 输出（无引号）或代码清单。
- 验证：Fig 2.13 的 6 行 token block 现 `is_figure_text=True`（删除）；`SimpleTokenizerV1`、`MultiHeadAttention`、`TransformerBlock`、`GPTModel`、`print_gradients` 等真实代码块仍 `False`（保留）；`Table 1.1` 仍 `False`（保留）。端到端重跑后该段图内文字归零、代码块数量稳定、无正文/代码误删。

## P5 导出后二次清理（`clean_figure_text.py`）

- 复用 P1 的 `page_element_regions` 取每页图区域，构建**每页几何图内文字集合**（`is_figure_text` 判定，非宽松的 `_block_in_figure`——后者把与图 union 重叠的代码块也误判为图内，见下）。
- **核心修复（双因子 + 标题窗口）**：旧实现只扫描 `![Fig]` 链接的上下**第一组**短行，而图内标签在 PDF 提取中位于 `**Figure X.Y**` 标题**之上**（P3 把 `![Fig]` 插到标题之后），向上扫描遇到 `**Figure**` 标题（hard stop）即停，**漏删了标题之上的所有标签**。新版改为**以 `**Figure X.Y**` 标题为中心、±`WINDOW`(40) 行窗口扫描**：
  - 窗口内某行被删 **当且仅当** 它**同时满足**：(1) `_norm(行)` 存在于几何图内文字集合（几何确认）；(2) `_is_label_candidate(行)` 为真（内容确认）。
  - 这样图标签无论位于标题上/下都能删，且任何一因子单独失效都不会误删正文/代码。
- **`_is_label_candidate` 内容护栏（强）**：拒绝任何像正文或代码的行——终端标点、章节引用、`Figure X.Y` 引用、代码语法（`()[]{}`/`=`/`.method(`/反引号/代码关键词）、逗号、行尾 dash、>6 词、含数字或非常规标点、以及动词词（`the`/`is`/`this`/`output` 例外作名词标签/`prints`/`show`/`every`…）。仅短名词短语（"Input text"、"Decoder"、"Token IDs:"、"FROM SCRATCH"）通过。
- **整页插画页（封面/封底/全页术语表）**：`figure_text_by_page` 据"图 union 面积 ≥ 90% 页面"标记 `fullpage_pages`（仅封面 p1、扉页 p2、封底 p370；阈值 0.55 曾误把内容页 p60/p80 当整页而全局误删）。这些页无 `**Figure**` 标题，直接删除其几何集合内所有行（作者/书名受 `COVER_KEEP` 保护）。
- **PDF 源选择**：默认优先选用**英文文件名 PDF**（即 P1 生成 MD 所用的同一源），避免 CJK 文件名副本文本层差异导致几何集合错位。
- 迭代执行至收敛（`while True` 到 0 匹配），删除后折叠连续空行，并在 `![Fig]` 后补空行。

**用法**：
```bash
.venv/bin/python clean_figure_text.py            # 默认 dry-run 预览将被删除的行
.venv/bin/python clean_figure_text.py --apply    # 执行清理并覆盖 llms-from-scratch.md
```

**验证与效果**：
- P1 删除 1149 个图内文字块；流程图标注全清，术语表 5/5 保留，正文零误删。
- P5 精确删除 **11 行**真正的图标签残留（封面 `FROM SCRATCH`/`BUILD A`/`effort` + 图轴标注 `Inputs:`/`Token IDs:`/`Outputs`/`Inputs`），**零误删**正文/代码（幂等：每次重跑管线后 P5 稳定命中同一 11 行）。
- 独立校验（`_verify_figtext.py` 行级精确匹配）确认：所有真图标签已清零；校验脚本报告的其余"残留"均为几何误判的伪阳性（正文训练语料 `Every effort moves you`、代码 `Logits shape:`、索引字母 `N` 等），P5 正确保留。
