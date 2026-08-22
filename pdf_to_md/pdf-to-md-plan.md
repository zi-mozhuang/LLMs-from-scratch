# PDF → Markdown 转换方案

> **目标**：将 `Build a Large Language Model (From Scratch) (Sebastian Raschka).pdf` 转为可阅读 Markdown。
> **路线**：图片独立提取 + 文本流直接转换（PyMuPDF 直提，不复用 pymupdf4llm，不 import 旧补丁链）。
> 本文件是方案汇总与经验依据，与下方脚本一一对应。

## 流水线总览

| 阶段 | 脚本 | 输入 → 输出 |
|------|------|------------|
| P0 图片提取 | `extract_images.py` | PDF → `extracted_images/{embedded,figures}/` + `manifest.json` |
| P1 文本流 | `pdf_text_stream.py` | PDF → 文本流 Markdown（页眉剔除、代码块还原、段落合并） |
| P2 清洗合成 | `pdf_clean_p2.py` | P1 输出 → 图文合成 + 断词/概念框/数学/加粗清洗 |
| P3 TOC 校验 | `pdf_toc_p3.py` | P2 输出 → 最终 `llms-from-scratch.md`（TOC + 锚点 + 断言） |
| 子模块 图内文字 | `figure_text_detect.py` | 被 P1 调用，几何法剔除图内文字 |
| 验证 图内文字 | `verify_figure_text.py` | 独立校验图内文字删除（V1-V4） |

## 1. 方案决策依据（PDF 实测事实）

| 事实 | 对策 |
|------|------|
| 内嵌位图极少：全书仅 33 个唯一 xref | 按 xref 直接提取原始字节，无损 |
| 正文图表是矢量绘图（143 个 `Figure X.Y`），非位图 | 无法抽取，定位区域后高分辨率渲染（3x ≈ 216 DPI） |
| 版式规律固定：图在上、`Figure X.Y` 标题紧贴其下 | 以标题 bbox 为锚点反推图形区域，标题充当完备性校验基准 |
| Courier 字体 = 代码块（阈值 ≥75%） | 文本流中按字体识别代码块，保留原始行结构与缩进 |
| 边注 bold-italic；侧注字体 `HumanistMann521-BoldCond` | 按字体 + 位置聚类（`y±15 x±80`）自动收集 |
| 概念框固定填充色 (0.969,0.961,0.910)，宽≥100pt 高≥40pt，全书约 70 处 | 按颜色+尺寸识别 callout box |

## 2. 核心经验（思想复用，代码重写）

1. **不要事后修补，要在提取时就拿对数据**：直接用 PyMuPDF 拿文本流/绘图路径/图片对象，而非对 LLM 转换输出做补丁链。
2. **不做全局行移动**：封面/肖像图会导致错位；图注类转换一律原地处理。
3. **小范围测试优先**：改任何函数先针对单页/单函数最小验证，通过后再全量生成做全局校验。
4. **每个修复函数对应一个独立缺陷**，互不依赖，带标记注释防误删。
5. **一切判定基于字体、颜色、位置聚类、跨页频次等结构信号，无硬码字符串/页号/坐标**。

## 3. 全局验证断言（P3 运行时校验）

```python
fences = [l for l in text.split("\n") if l.strip().startswith("```")]
assert len(fences) % 2 == 0                      # 围栏成对
assert not re.search(r"[\uF000-\uF0FF]", text)   # 无 PUA 希腊字母
assert "<sup>" not in text and "<mark>" not in text
anchors = set(re.findall(r'<a id="([^"]+)">', text))
links = set(re.findall(r'\]\(#([^)]+)\)', text))
assert links <= anchors                          # TOC 全部可跳转
```

## 4. P0 图片提取（`extract_images.py`）

双通道：
- **A. 嵌入位图** → `extracted_images/embedded/`（原始字节，按 xref）
- **B. 矢量图** → `extracted_images/figures/`（区域渲染 @3x）

三阶段（每阶段独立自测）：
1. `find_caption_candidates`：行级 `Figure X.Y` 扫描（含正文引用）
2. `validate_candidates`：几何校验剔除引用，保留真正图注
3. `build_figure_region`：从种子 + 标签块生长图区域

校验：V1 位图完整性（33 xref）、V2 图表覆盖率（143 标题逐一配对 PNG）、V3 内容有效性（像素方差/最小边）。
用法：`python3 extract_images.py`（全量 + V1-V3）、`test-candidates` / `test-validate`（单阶段自测）。

## 5. P1 文本流转换（`pdf_text_stream.py`）

**设计**：`block → line → span` 三级流式重建，替代 pymupdf4llm。

- **页眉/页脚删除（span 级）**：字体∈`HEADER_FONTS` 且位于顶部/底部边距（top<35pt / bottom>页高-75pt）且字号<10.5pt → 剔除该 span。
  PyMuPDF 会把运行头 span 与正文第一行合并进同一 block，故改用 span 级 + 整行页眉测试（`is_header_line` 处理纯数字/罗马页码前缀）。
- **代码块还原（含跨 block 合并）**：block 内非页眉 span 中 Courier 占比 ≥75% → ```` ```python ```` 或 ```` ```text ````；正文内 Courier 片段包行内 `` `code` ``。**关键修复（见 §14）**：PDF 常把**一个逻辑代码清单拆成多个 Courier text block**，P1 不再逐 block 输出独立 fenced block，而是用 `pending_code` 缓冲累积**连续代码 block** 的代码行，在遇到非代码 block 时由 `flush_code()` 一次性输出为**一个** fenced block。连续判定基于物理布局（中间只有图片/页眉/图内文字等无正文块），比文本启发式合并可靠——一行被误判为 prose 也不会打断 run。P2 的合并仅作兜底。
- **节标题层级**：`^\d+\.\d+\s+[A-Z]` → `### `（排除 `0.4 0.6 0.5` 类数值矩阵）；`^Chapter/Appendix` → `## `；`^Part\s+[IVX]+` → `## `；`^Summary$` → `### `（章节小结标题）。跨行拼接：`1.1` + `What is an LLM?` → `### 1.1 What is an LLM?`。
- **段落合并（block 内）**：block 内多物理行合并为一段（空格连接）。
- **跨 block 段落合并**：PDF 常把一个逻辑段落拆成多个 text block（中间夹图/公式），P1 原本只合并 block 内行，导致这类段落被切成多段、出现硬换行。新增缓冲式 `pending_prose`：当相邻 prose block 满足"前段末无句末标点、后段首字母小写、两段均 ≥40 字符"时，折叠为同一段（`_should_merge_prose`）。
- **两行章节标题合并**：章节大标题（如 `Understanding large` / `language models`）被 PDF 拆成两个 block，需合并为单行 `### ` 标题（`_is_two_line_heading`）：两片段均 <50 字符、后段首字母小写、前段不以引导动词（covers/include/chapter…）结尾、不以 `)` 结尾（排除已合并的 "This chapter covers …" 引导句后的列表项）。该规则刻意不合并 "This chapter covers" 后的短短语列表（PDF 原生多行，保留为列表）。
- **图内文字删除**：每页预计算 `page_element_regions`，在页眉剔除后跳过图内文字块（见 §8，`StreamConfig.drop_figure_text` 默认开）。

## 6. P2 图文合成与清洗（`pdf_clean_p2.py`）

管道顺序（固化）：页眉删除(P1) → 段落合并(P1) → 以下 7 步：

1. **断词连字符合并** `(\w+)- (\w+)` → `\1\2`，带复合前缀排除词表（`COMPOUND_PREFIXES` 保留真复合词如 `self-attention`；`JOIN_PREFIXES` 连接断词如 `under-stand`→`understand`）；排除右词首大写。
2. **概念框 PUA→引用块**：仅把含 `\uf0a1` bullet 的行转 `> `；不含 bullet 的行绝不并入（安全边界）。
3. **数学清洗**：PUA `\uf061`→α、`\uf077`→ω；中间点 `⋅`(U+22C5)→`\cdot`（幂等两次调用）。
4. **图文合成**：`^Figure X.Y` 标题行前插 `![Fig X.Y](extracted_images/figures/FigX.Y_pNNN.png)`，映射来自 `manifest.json`。
5. **围栏平衡 + 合并（兜底）**：P1 已按物理布局把连续代码 block 合并为单一 fenced block，此处 `rebalance_fences` 仅兜底合并 P1 因跨页/图内文字断开而残留的相邻同语言块，并做围栏成对验证。
6. **标题名加粗**：`^(Figure|Table|Listing) X.Y` 标题行 → `**Figure X.Y** 描述`（仅标题名加粗）；动词开头的正文引用句排除。
7. **Exercise 引用块化**：`^Exercise N.N ...` 段落（PDF 中标题 FranklinGothic-Demi 10.5pt、说明 FranklinGothic-Book 9.5pt，被 P1 合并为一段）渲染为引用块以区别于正文：
   ```
   > **Exercise N.N**
   >
   > <原说明文本>
   ```
   短标题短语保留在说明段首，无信息丢失。因不再是 Markdown 标题，P3 的锚点/TOC 扫描忽略它，不破坏 TOC⊆anchors 不变量。

## 7. P3 TOC 与全局校验（`pdf_toc_p3.py`）

1. 扫描真实标题（排除代码围栏内 `#`）。
2. 注入唯一 HTML 锚点 `<a id="slug"></a>` 到每个标题前；重复标题 slug 加 `-2`/`-3` 后缀保证唯一。
3. 生成书级 `#` 标题 + `## Table of Contents`，TOC 按**逻辑阅读顺序**排序（Chapter→其下 N.M 节），仅收录结构标题（Chapter/Appendix/`N.N`），排除数据集样本标签 `### Instruction:` 等内容格式。
4. 重复 Chapter（附录练习重列）在 TOC 显示名加 `(Appendix)` 后缀。
5. 运行 §3 断言（围栏成对、无 PUA、TOC⊆锚点）。

**最终产物 `llms-from-scratch.md`**：141 图片链接、127 Figure 标题、833 代码块、216 概念框引用块；§3 断言全过。

## 8. 图内文字删除（`figure_text_detect.py` + `verify_figure_text.py` + `clean_figure_text.py`）

**问题**：PyMuPDF 把图（矢量绘图/位图）*内部*的文字（流程图标签、概念框标注、坐标轴标签、示例截图文字）当作普通文本块提取，而这些文字已"烧录"进 P0 渲染的 PNG，留在正文里既重复又污染文本。分两级处理：
- P1 提取时几何剔除大部分，但护栏故意放行了"像正文"的类（术语表短语、完整句标注）。
- 导出后 P5 再清紧邻 `![Fig X.Y]` 链接、P1 漏掉的残留（封面流程图节点、图内示例文本等）。

### 8.1 P1 几何法（`figure_text_detect.py`，被 P1 调用）

- 复用 P0 的 `collect_element_rects` 取每页所有绘图路径 rect + 位图放置 rect。
- 文本块判为图内（→删除）当满足任一：
  - **(HIGH)** bbox 与任一元素 rect 直接相交。
  - **(MED)** 中心落在元素 union 内、且位于 union 顶部 30% 区、且 ≤2 行短标签。
- **护栏（避免误删正文，优先级最高）**：字号 > 13pt → 保留（封面标题/作者/章节标题）；以句号/问号/感叹号结尾的完整句子 → 保留；纯字母短语 ≤4 词无数字 → 保留（图1.1 术语表靠此保命）。
- 集成：P1 每页预计算 `page_element_regions`，在页眉剔除后、代码块判定前对文本块调用 `is_figure_text` 跳过。

#### 8.1.1 图内字符串数组被误当代码块的根因与修复（Fig 2.13 token 列表）

**现象**：`llms-from-scratch.md` L1148-1155 出现 ```` ```text ```` 代码块，内容是图 2.13 里展示的 token 字符串数组 `[ "city", "stood", "the", "old" ], [ "library", … ]`。该段是**图内文字**（L1146 原文："the figure shows the tokens in string format for illustration purposes"），且含损坏的中文引号 `""`（图内提取乱码），却被当成正文代码块输出。

**根因链**（在 `pdf_text_stream.py` 主循环）：
1. P1 对每个 block 先调 `is_figure_text` 决定是否删除图内文字（L335）。
2. Fig 2.13 的 token 数组在 PDF 里被切成**多个独立 Courier text block**（如一个 6 行 block：`[ "city", … "relic"], [ … ]])`）。其中心落在图 union 内，但 `is_figure_text` 对它返回 **False** → 逃过图内删除。
3. 回到主循环 L340-344：该 block `courier_frac ≥ 0.75` → 判为代码块 → `flush_code()` 输出 ```` ```text ````。

**`is_figure_text` 为何放行**（旧逻辑 L107-116 Courier guard）：该守卫本意是保护正文代码清单（如 P49 `SimpleTokenizerV1`），规则为"多行(≥5) Courier 且 raw>50 且含代码字符 `[ ] ( ) = : { } import class def` → `return False`（保留）"。但 token 数组含 `[` `]` 和数组结尾的 `)`（`])` / `...]));`），触发该字符集 → 被当作"代码"放行 → 图内删除失效。

**修复**：
- **Courier guard 收窄**：字符集去掉 `(` `)` `[` `]`（圆括号/方括号在数据数组中也出现，不是代码标志），仅保留真正代码语法 `=` `:` `{` `}` `import` `class` `def`。真实代码清单仍含 `=`/`:`/`def`/`class` → 仍被该守卫保留；token 数组不含这些 → 不再被早返回。
- **新增"图内字符串数组"规则**（位于 term-glossary 之后、HIGH 相交之前）：块中心落在图 union 内 **且** 形状为带引号字符串数组（含 `[` 且 `"` 计数 ≥2 且含 `,`）**且** 无代码关键字（`=` `:` `def ` `class ` `import ` `return ` `self.`）→ 判为图内 `return True`。该规则专门匹配图内 token/字符串数组（`[ "city", … ]`），因强制要求 `"` 而**不会**误删 tensor 输出（无引号）或代码清单。
- 验证：Fig 2.13 的 6 行 token block 现 `is_figure_text=True`（删除）；`SimpleTokenizerV1`、`MultiHeadAttention`、`TransformerBlock`、`GPTModel`、`print_gradients` 等真实代码块仍 `False`（保留）；`Table 1.1` 仍 `False`（保留）。端到端重跑后该段图内文字归零、代码块数量稳定、无正文/代码误删。

### 8.2 P5 导出后二次清理（`clean_figure_text.py`）

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

## 9. 标题级别修复子方案（`fix_headings.py`）

### 9.1 问题根因

`pdf_text_stream.py`（P1）仅识别 `Chapter N` / `N.N Title` 模式的标题。以下类型标题被遗漏：

- 无编号小节标题（"Who should read this book"、"Summary"）
- 三级子节标题（"3.3.1 A simple self-attention mechanism..."）
- 练习标题（"Exercise 2.1"）
- 索引子标题（"Symbols"、"Numerics"）

### 9.2 方案

以 PDF 内置 TOC（193 条）为 ground truth，交叉匹配 MD 中的纯文本行：

1. 提取 PDF TOC，跳过结构性条目（brief contents、contents、index、书名）
2. 对每个 TOC 条目，在 MD 中搜索 NFKC 归一化精确匹配的独立行
3. 匹配条件：该行不以 `#` 开头、不在代码围栏内、**下一行为空行**（排除正文中的交叉引用）
4. 合并行检测：若行文本以标题文本开头但后续还有正文，拆分为标题 + 空行 + 正文
5. 重复标题处理（如 8 个 "Summary"）：返回所有匹配位置，逐一修复
6. 级别映射：PDF L1→`##`、L2→`###`、L3→`####`

### 9.3 执行结果

- 修复 **72 个缺失标题** + 1 合并行拆分；幂等（重跑 0 修复）。
- TOC 覆盖率 164/166（仅 §5.4、§7.4 整节内容提取缺失，非标题问题）。
- §3 断言通过（围栏 1308 成对、无 PUA、TOC⊆anchors）。

## 10. 跨 block 段落/标题拆分修复（已整合进 `pdf_text_stream.py`）

### 10.1 问题根因

P1 原本只在**单个 text block 内**合并物理行。当 PDF 把一个逻辑段落或章节标题拆成多个 text block（中间夹图/公式，或标题本身两行排版）时，产物里会出现"本该一行却多行"的硬换行：同句被切成两段、章节大标题被拆成两行、小结标题 `Summary` 未被识别为标题。

### 10.2 修复（均在 `pdf_text_stream.py` 主转换里实现，非独立脚本）

| 类型 | 模式 | 修复 | 效果 |
|------|------|------|------|
| 句子跨块拆行 | 前段末无 `.!?` + 后段首小写 + 两段均 ≥40 字 | 缓冲 `pending_prose` 折叠为同一段（`_should_merge_prose`） | 修复 ~121 处 |
| 两行章节标题 | `Understanding large` + `language models`（两 block，均 <50 字、后首小写、前非引导动词结尾、前非 `)` 结尾） | 合并为单行 `### ` 标题（`_is_two_line_heading`） | 修复 "Understanding large language models" / "Coding attention mechanisms" 等 |
| Summary 未识别 | 独立行 `Summary` | `SUMMARY_RE` → `### Summary` | 各章小结标题统一 |

### 10.3 设计要点与护栏

- 长度下限（≥40 / <50）用于区分"句子续行/两行标题"与"章节提纲短列表项"（如 "This chapter covers" 后的 `tokenizing text` / `approach`，PDF 原生多行，保留为列表，不合并）。
- 引导动词黑名单（covers/include/chapter/derived/follows…）与 `)` 结尾排除，确保不把 "This chapter covers … (LLMs)" 引导句后的列表项误并为标题。
- 残留约 35 处"疑似多行"经核对均为 PDF 原生多行内容：参考文献/URL 断行、索引条目（Symbols/B/C/V…）、封底宣传语/Feynman 引言——非误拆，不处理。

## 11. 章节标题与“本章涵盖”修复子方案（`fix_chapters.py`）

### 11.1 问题根因

P1 pipeline 的标题识别仅支持 `Chapter N` / `N.N Title` 模式，无法识别章首页的大号斜体标题（如 `Understanding large` / `language models` 分属两个 PDF block）。同时，"This chapter covers" 的 bullet 列表使用 Wingdings 字体标记，P1 无法处理，导致 bullet 内容几乎全部丢失。

### 11.2 异常类型

| 类型 | 模式 | 修复 | 数量 |
|------|------|------|------|
| 章标题碎片 | 多行碎片（如 Ch1/3/7）或单行无 `##` 前缀（如 Ch2/4/5/6） | 合并碎片 + 添加 `## N` 前缀 | 7 |
| “本章涵盖” bullet 丢失 | Wingdings bullet 被 P1 忽略，仅残留尾部碎片 | 从 PDF 提取完整 bullet 列表替换 | 7（共 34 条 bullet） |

### 11.3 执行结果

- 7 个章标题全部修复（`## 1` 至 `## 7`）；7 个"本章涵盖"区域替换为完整 bullet 列表（共 34 条）。
- 设计要点：从 PDF TOC 取正确标题文本、Wingdings 字体检测 bullet 起始块合并续行、碎片检测（短行 <80 字、无终端标点、无特殊前缀）；脚本幂等。
- TOC 覆盖率 164/166（仅 §5.4、§7.4 内容缺失）。

## 12. 异形文本块修复子方案（`fix_special_blocks.py`）

> 覆盖“Note/概率解释/Exercise/Listing”等与正文格式不同的对象，确保 md 中有可视觉区分的格式（引用块/加粗标题），且无遗漏、无多加。

### 12.1 范围与动因

本书中大量知识点以**带底色 callout 框**承载（浅黄 `(0.969,0.961,0.91)`、蓝头 Listing、灰表头等），字体与正文不同：
FranklinGothic-Demi 10.5（标题）/ FranklinGothic-Book 9.5（正文），与正文 NewBaskerville-Roman 10pt 显著区分。
P1 直提按 block 合并且 P2 仅处理 `\uf0a1` bullet，导致**段落式 callout（无 bullet）整体与正文合并为普通段落**，
Listing 白字蓝底条被 `figure_text_detect` 误判为图内文字而整条丢失（67 处）。

### 12.2 PDF 异形块分类与判定信号

| 类型 | PDF 信号 | 数量 | 目标 md 格式 | 示例标题 |
|------|----------|------|--------------|----------|
| A. 信息 callout（概率/概念解释） | 填充 `(0.969,0.961,0.91)` 且 `w≥100 h≥40` + 标题 `FranklinGothic-Demi 10.5` + 正文 `Book 9.5`，bbox 与填色 rect 重叠 ≥20% | 32 | `> **Title**` + `> body`（每段前 `> `，段间 `>` 空行） | `The “self” in self-attention`、`Cross entropy loss`、`Perplexity`、`Biased variance` |
| B. Exercise | 同上，且标题 `^Exercise` | 25 | 同上（`> **Exercise X.Y Title**`） | `Exercise 2.1` … `Exercise 7.4` |
| C. This chapter covers | 同上 + 标题 `This chapter covers` + Wingdings bullet | 7 | `**This chapter covers**`（已在 §11 修复为粗体引导语，无冒号）+ 普通 `-` 列表（单行 `- `，无拆行） | — |
| D. NOTE  admonition | 首 span `NOTE` `FranklinGothic-Demi 8.5` #4680581 + 后续 `Roman 10`，无填色 | 29 | `> **NOTE** body`（全段 `> `，与 `Note that` 区分） | `NOTE Most LLMs today…`（L395） |
| E. Listing 标题 | 填充 `(0.438,0.652,0.801)` + 白字 `Demi 9` + 文本 `^Listing \d+\.\d+` | 67 | `**Listing X.Y Title**`（单独粗体行，位于代码围栏前） | `Listing 2.1 Reading…` |
| F. Table 1.1 数据表 | 4 列网格（`Dataset name`/`Number of tokens`/`Proportion` 等 `FranklinGothic 8pt`，`y 73-171 P33`，11 画线网格） | 1（5 行+表头） | `| Dataset name | … |` markdown 表格（`L458`） | `CommonCrawl (filtered) | Web crawl data | 410 billion | 60%` |
| — | Table/Figure 标题 | 已在 §6.6 正确加粗（`^Figure\|Table`） | `**Figure X.Y**` / `**Table X.Y**` | — |
| — | 行内 `Note that…` | `NewBaskerville-Roman 10` 且**无填色**、小写 `Note` | 保持正文（护栏：不转块） | — |

判定全部基于**颜色+尺寸+字体+位置重叠**，无硬码字符串/页号。

### 12.3 现状审计（修复前）

- 57 个带标题 callout 框（不含 7 个 chapter covers；32 信息 +25 Exercise，另有 5 个续页无标题体作为同一框续段）中：**0 个为引用块**，44 个标题与正文合并为普通段落（标题与首句同行），13 个标题整行丢失（仅存 body 融入正文，含跨页续段）。
- 29 个 `NOTE` admonition（`NOTE` Demi 8.5）：**0 个为引用块**，全部以 `NOTE ` 普通段落与正文混同（L395 等）。
- 67 个 Listing 标题：**0 个**在 md 中存在（被图内文字几何删除）。
- 1 个数据表 `Table 1.1`（5 行+表头，`P33 y73-171`）：**0 行**在 md 中存在（`Dataset name`/`CommonCrawl` 等 `FranklinGothic 8pt` 网格文字被 `figure_text_detect` HIGH 误判为图内而整表丢失，`L458` 仅剩 `**Table 1.1**` 标题）。
- Figure/Table 标题：179/3，已正确加粗；无需改动。
- 行内 `Note that…`（约 40 处，新罗马字体无底色）保持正文，未误判。

### 12.4 处理规则

1. **callout 重建**：对每个概念框 rect，收集重叠文本块（按 `y` 排序）；首行 `Demi 10.5` 为标题，其余为 body（已去连字符、NFKC 归一化）。
   - 跳过 `This chapter covers`（由 `fix_chapters` 管理，修复为单行 `- `，无 `>` 拆行；见 `L320` 样本）。
   - 在 md 中以 **NFKC+去空白归一化**查找标题行；若找到且为普通行 → 将标题及随后连续正文段（至空行+标题/`#`/` ``` `/`![`/`**Figure` 边界）整体替换为 `> **Title**` + `> body`（单行，无拆行）。
   - 若标题缺失仅 body 存在 → 在 body 首段前插入 `> **Title**`。
   - 已是 `> ` 块则幂等跳过。
2. **NOTE admonition**：首 span `NOTE` Demi 8.5 的块（29 处，无填色）与行内 `Note that`（Roman 10，小写）严格区分；md 中 `^NOTE ` → `> **NOTE** body`（全段 `> `，抽样 `L395`）。
3. **Listing 恢复**：对每个 Listing 标题 `(num, title)`，NFKC 查找 md 是否已有 `**Listing X.Y`（含 `^NOTE` 已转 `> ` 后）；若无，则取该 Listing 在 PDF 中**首个后续 Courier 块的首行代码**作为锚点（如 `with open`、`class`、`def`），在 md 中定位包含该锚点的首个围栏前插入 `**Listing …**` 空行；另将附录中 `^Listing A./E.` plain 行转为 `**Listing X.Y** Title`（仅编号粗体）。
4. **Table 1.1 重建**：`P33 y73-171` 11 画线网格的 6 个块（表头 `Dataset name…` +5 行 `CommonCrawl`/ `WebText2`/ `Books1`/ `Books2`/ `Wikipedia`）在 `figure_text_detect` 前加 **Table 护栏**（`raw` 含 `Dataset name`/`CommonCrawl`/`WebText2`/`Books1`/`Number of tokens` 即 `return False`），`P1` 后保留为 `Dataset name …` 6 行 plain；后处理以 `Dataset name` 为锚点、`Wikipedia` 为尾，用 6 块重建 `| … |` markdown 表格（`L458`），列宽 `|---|---|---|---|`。
5. **护栏**：
   - 仅处理与填色 rect 重叠≥20% 的块或 `^NOTE ` Demi 8.5 或 `Table` 网格；HumanistMann 侧注、图内标签（已由 §8 删除）不进入。
   - 行内 `Note that`（无填色、小写 `Note`）永不转块——通过 `^NOTE ` 全大写 vs `Note that` 严格区分。
   - `This chapter covers`  bullet 保持 `- ` 单行，禁 `> ` 拆行。
   - 修复后迭代至收敛，`figure_text_detect` 新增护栏：`^Listing` 白字块、`FranklinGothic-Book/Demi 9-11` callout、`NOTE` Demi 8.5、`Table` 关键字永不判为图内（防回归）。

### 12.5 实现与验证（`fix_special_blocks.py`）

**用法**：
```bash
python fix_special_blocks.py              # dry-run：预览将改动的 callout/Listing
python fix_special_blocks.py --apply      # 覆盖 llms-from-scratch.md
```

**幂等**：重复运行第二次 0 改动（已 `> ` / 已 `**Listing` 则跳过）。

**验证（V1-V6，全部自动化断言）**：
- V1 callout/NOTE/Table 覆盖率：PDF 57 callout +29 NOTE +1 Table(6 行) → md 86 个 `> **Title**`/`> **NOTE**` +1 个 `| Dataset name |…|` 表格（`L458` 6 行全命中，`CommonCrawl` 抽样）。
- V2 Listing 覆盖率：67/67 行 `**Listing X.Y`（仅编号粗体）存在且位于围栏前（含附录 `A.3`/`E.3` plain→粗体）。
- V3 无多加：40 处行内 `Note that…`（Roman 10）保持普通段落（`^Note that` 非 `^NOTE`，无 `>`）；`p33 y73-171` 表格外无 `FranklinGothic 8pt` 误留。
- V4 回归：`^\*\*Figure` 127，`^\*\*Listing` 67，`> **NOTE**` 29，`fences 1310` 成对；`This chapter covers` 7×`- ` 单行无 `>` 拆行（`L320`）；`L430-446 Fig1.4` 8 标签 0 残留。
- V5 无 PUA/无空锚：§3 断言全过（`anchors 186` / `links 81` subset True）。
- V6 无错位：每处仅 NFKC 命中处替换，body 以空行/`#`/` ``` `/`![`/`**Figure`/`**Table` 为界，不跨段；幂等二次 0 改动；逐图 143 图 `fig_by_page` meaningful 1529→36 残留仅封面/code 输出等非图内（97.6% 清除）。

## 13. 已知遗留（非阻断）

- §5.4 和 §7.4 整节内容在 P1 提取中缺失（PDF 提取遗漏，需人工补录或重新提取）。
- 概念框多行续行在 P2 未补 `>`（已由本节在修复时合并为单 body 段，避免空前缀丢失）。
- 正文上标在 P1 直提中已平化，未做 LaTeX 还原。
- 重复 Chapter 标题（附录练习）TOC 以 `(Appendix)` 区分，但正文标题文本相同。
- 个别“几何失败页”（非封面/图1.1）若其图内文字未被 P1 几何捕获，则残留为无害冗余（P5 为安全起见不在这些页退化为纯位置删除，以免误删代码）。

## 14. 代码清单被拆成多个代码块修复子方案（`pdf_text_stream.py`）

### 14.1 问题根因

PDF 文本提取中，**一个逻辑代码清单（listing）常被 PyMuPDF 切成多个独立的 text block**。例如"下载 the-verdict.txt"那段在 PDF 里是 2~3 个 Courier block。

原实现对每个代码 block 独立输出一个 fenced block，依赖 P2 的 `rebalance_fences` 在文本层合并"紧邻同语言"的块。这有两个致命缺口：

1. P1 每 block 后加空行，且连续代码 block 之间若夹了被 `drop_figure_text`/页眉剔除跳过的中性 block，或直接相邻，合并只能靠"无 prose 间隔"判断；
2. 更糟的是：一旦某个代码行因字体/字号判定偏差（如某 span 混了非 Courier 字体）被归入 prose，就会在该处插入正文行，**打断 run**，P2 无法再合并两侧的代码块。

实测产物：PDF 共 1012 个代码 block、本应组成 **648 个逻辑清单**（493 单块 + 155 个多块 run），但 P2 只合并到 **828 个 fenced block**——即多生成了约 180 个本不该存在的代码块（一页里同一清单裂成几段，每个都带 ` ``` ` 围栏）。

### 14.2 方案（在 P1 物理布局层合并）

不再逐 block 输出围栏，改为**基于 PDF 物理布局的连续代码 run 合并**：

- 新增 `pending_code: list[str] | None` 与 `pending_code_lang` 缓冲。
- 主循环遇到代码 block 时：先 `flush_pending()`（代码前不能有挂起 prose），再把该 block 的代码行 `extend` 进 `pending_code`，**不立即输出围栏**。
- 遇到**非代码 block** 时，先在分支开头调用 `flush_code()`，把累积的代码行作为**一个** fenced block 输出。判定"非代码"的只有 prose/标题块；图片/绘图-only block（无 text）不调用 `flush_code`，因此图插在代码中间不会打断 run（图内文字也已被 §8 删除，物理上不占据正文流）。
- 文档/页末统一 `flush_code()` 收尾。

合并的"连续性"由 block 遍历顺序保证：连续 Courier block 之间若只有无正文的中性 block，则仍属同一 run。`detect_code_lang` 对每个 block 调用但只取首个语言（同 run 语言一致），避免混入不同语言导致错配。

### 14.3 执行结果

- 重跑 P1→P2→P3 全管线，§3.3 断言全过（围栏成对、无 PUA、TOC⊆anchors）。
- fenced block 数：**828 → 605**（理想 648，差值 43 来自跨页 run 在页末被强制 flush 拆分，以及字体判定边界处的个别单块）。
- 验证示例：Ch2"下载 the-verdict.txt"原裂为 2 段（606-613、619-624），现合并为 1 个完整块（含 `url = (...)` 续行）；而"读取 the-verdict.txt"（Listing 2.1，中间有 "Next, we can load..." 说明 + `**Listing 2.1**` 标题）**正确地保持为独立清单**——证明只合并了真正的物理连续 run，未误并书里真实分开的多个清单。

### 14.4 护栏

- 图片/绘图-only block **不** flush 代码，故图内插入不会切断代码清单。
- 仅在进入 prose/标题块时才 flush，避免把书里真实的两个相邻清单（中间有说明文字）误并。
- 跨页代码 run 在页末 flush（代码清单不会跨页），安全。
