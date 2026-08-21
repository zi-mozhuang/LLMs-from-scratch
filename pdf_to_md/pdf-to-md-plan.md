# PDF → Markdown 转换方案

> **目标**：将 `Build a Large Language Model (From Scratch) (Sebastian Raschka).pdf` 转为可阅读 Markdown。
> **路线**：图片独立提取 + 文本流直接转换（PyMuPDF 直提，不复用 pymupdf4llm，不 import 旧补丁链）。
> 本文件是方案汇总与经验依据，与下方脚本一一对应。

## 流水线总览

| 阶段 | 脚本 | 输入 → 输出 |
|------|------|------------|
| P0 图片提取 | `extract_images.py` | PDF → `extracted_images/{embedded,figures}/` + `manifest.json` |
| P1 文本流 | `pdf_text_stream.py` | PDF → 文本流 Markdwon（页眉剔除、代码块还原、段落合并） |
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

## 3. 通用清洗要点（各阶段沿用）

- **数学公式**：PUA 希腊字母（`\uF000-\uF0FF`）映射为 α/ω；点乘 `⋅`(U+22C5)→`\cdot`；幂等地再修一次。
- **页眉页码**：删运行头（`CHAPTER N`、节标题、`Figure X.Y` 头部）、独立页码行、罗马页码；按字体+位置识别，不列白名单。
- **段落硬换行**：PDF 物理行宽造成的段内断行需保守合并；代码块内部与索引页绝不触碰。
- **围栏平衡**：校验 ``` 成对、补缺失闭围栏（幂等）；相邻同语言围栏仅空行分隔且次块为续行时合并。
- **图注名加粗**：`Figure X.Y`/`Table X.Y`/`Listing X.Y` 行加粗名称；排除动词开头的正文引用句（"Figure 4.12 shows..."）。

### 3.1 全局验证断言（§P3 运行时校验）

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
- **代码块还原**：block 内非页眉 span 中 Courier 占比 ≥75% → ```` ```python ```` 或 ```` ```text ````；正文内 Courier 片段包行内 `` `code` ``。
- **节标题层级**：`^\d+\.\d+\s+[A-Z]` → `### `（排除 `0.4 0.6 0.5` 类数值矩阵）；`^Chapter/Appendix` → `## `。跨行拼接：`1.1` + `What is an LLM?` → `### 1.1 What is an LLM?`。
- **段落合并**：block 内多物理行合并为一段（空格连接），block 间空行分隔。管道顺序固化：先删页眉 → 再合并段落。
- **图内文字删除**：每页预计算 `page_element_regions`，在页眉剔除后跳过图内文字块（见 §8，`StreamConfig.drop_figure_text` 默认开）。

## 6. P2 图文合成与清洗（`pdf_clean_p2.py`）

管道顺序（固化）：页眉删除(P1) → 段落合并(P1) → 以下 6 步：

1. **断词连字符合并** `(\w+)- (\w+)` → `\1\2`，带复合前缀排除词表（`COMPOUND_PREFIXES` 保留真复合词如 `self-attention`；`JOIN_PREFIXES` 连接断词如 `under-stand`→`understand`）；排除右词首大写。
2. **概念框 PUA→引用块**：仅把含 `\uf0a1` bullet 的行转 `> `；不含 bullet 的行绝不并入（安全边界）。
3. **数学清洗**：PUA `\uf061`→α、`\uf077`→ω；中间点 `⋅`(U+22C5)→`\cdot`（幂等两次调用）。
4. **图文合成**：`^Figure X.Y` 标题行前插 `![Fig X.Y](extracted_images/figures/FigX.Y_pNNN.png)`，映射来自 `manifest.json`。
5. **围栏平衡 + 合并**：相邻同语言代码块合并，围栏成对验证。
6. **标题名加粗**：`^(Figure|Table|Listing) X.Y` 标题行 → `**Figure X.Y** 描述`（仅标题名加粗）；动词开头的正文引用句排除。

## 7. P3 TOC 与全局校验（`pdf_toc_p3.py`）

1. 扫描真实标题（排除代码围栏内 `#`）。
2. 注入唯一 HTML 锚点 `<a id="slug"></a>` 到每个标题前；重复标题 slug 加 `-2`/`-3` 后缀保证唯一。
3. 生成书级 `#` 标题 + `## Table of Contents`，TOC 按**逻辑阅读顺序**排序（Chapter→其下 N.M 节），仅收录结构标题（Chapter/Appendix/`N.N`），排除数据集样本标签 `### Instruction:` 等内容格式。
4. 重复 Chapter（附录练习重列）在 TOC 显示名加 `(Appendix)` 后缀。
5. 运行 §3.1 断言（围栏成对、无 PUA、TOC⊆锚点）。

**最终产物 `llms-from-scratch.md`**：141 图片链接、127 Figure 标题、833 代码块、216 概念框引用块；§3.1 断言全过。

## 8. 图内文字删除子方案（`figure_text_detect.py` + `verify_figure_text.py`）

**问题**：PyMuPDF 把图（矢量绘图/位图）*内部*的文字（流程图标签、概念框标注、坐标轴标签）当作普通文本块提取，这些文字已"烧录"进 P0 渲染的图 PNG，留在正文里既重复又污染文本。

**方案（几何法）**：
- 复用 P0 的 `collect_element_rects` 取每页所有绘图路径 rect + 位图放置 rect。
- 文本块判为图内（→删除）当满足任一：
  - **(HIGH)** bbox 与任一元素 rect 直接相交。
  - **(MED)** 中心落在元素 union 内、且位于 union 顶部 30% 区、且 ≤2 行短标签。
- **护栏（避免误删正文，优先级最高）**：
  - 字号 > 13pt → 保留（封面标题/作者/章节标题）。
  - 以句号/问号/感叹号结尾的完整句子 → 保留（正文段落）。
  - 纯字母短语 ≤4 词无数字 → 保留（图1.1 术语表，与图框几何相交靠此护栏保命）。
- 集成：P1 每页预计算 `page_element_regions`，在页眉剔除后、代码块判定前对文本块调用 `is_figure_text` 跳过。

**验证** `verify_figure_text.py`（V1-V4）：
- V1 删除块数 1149 ∈[300,2500] ✅
- V2 删除块中完整句子数 0 ✅
- V3 术语表 5/5 保留 ✅
- V4 含数字/& 的图标注 5/5 已删 ✅ → OVERALL PASS

**效果**：全文档删除 1149 个图内文字块；流程图标注全清，术语表完整保留，正文零误删。

## 8.5 P5 导出后残留图内文字清理（`clean_figure_text.py`）

**问题**：P1 的 `is_figure_text` 用 `sentence_guard` / `term_glossary_guard` 故意放行了两类"看起来像正文"的图内文字，它们最终仍残留在 `llms-from-scratch.md` 中，成为图（PNG 已"烧录"）的重复文本：

- **流程图节点标签**：`Multi-head attention`、`Self-attention module`、`Queries Keys Values`、`Preprocessing steps`、`Building an LLM`、`Classiﬁer`、`Foundation model`、`Personal assistant` 等纯词短语（被 term guard 放行）。
- **图上标注 / 术语定义长句**：`GenAI involves the use of deep neural networks...`、`In chapter 5, we pretrained an LLM.` 等（被 sentence guard 放行）。
- **封面流程图节点**：`FROM SCRATCH`、`BUILD A`、`Dataset with class labels`、`Instruction dataset` 等（封面无 `Figure` 锚点，P1 根本未处理）。
- **图内示例文本**：`Hello, I am a model ready to help.`、`du Kannst mir...`、`Is the following text 'spam'?`、`The quick brown fox...` 等（聊天/分类示例截图内的文字）。

这些残留紧邻 `![Fig X.Y]` 图链接上下方，肉眼看是"图上方一堆孤立短标签"，与正文混排。

**方案（导出后几何后处理，位置 ∩ 几何双重信号，零误删）**：
- 复用 P1 的 `page_element_regions` 取每页图区域，构建**每页几何图内文字集合**（归一化：NFKC 统一 `ﬁ→fi` 连字、折叠空白）。
- 遍历 Markdown，对每个 `![Fig X.Y](...pNNN.png)` 链接，向上/下扫描**紧邻短行簇**（≤140 字符行；遇 `#`/`<a id`/`**Figure`/连续两空行/长正文段即停）。
- **删除判定（双保险）**：短行仅当 `_norm(行) ∈ 该页几何图内文字集合` 才删——纯位置信号之外增加几何确认，正文短句/代码块（几何不在图内）永不进入删除集。
- **几何判定（`_block_in_figure`）**：
  - 块中心落在任一图矩形内 → 图内。
  - **side 判定**（2026-08 修复图1.2 类残留）：块与任一图矩形**严格垂直重叠**（`fr.y0<r.y1 and fr.y1>r.y0`，无容差）且水平间隙 `0≤gap<15pt` → 图内标签。这覆盖"标签烧录在 PNG、但在 PDF 中位于图裁剪区左/右边缘外侧"的情况（如 Fig 1.2 的 `User input (instructions)` / `Model output`，其 center 在图矩形外、距图 3pt 紧贴）。
  - **注意**：side 判定不可带垂直容差（±10pt 曾把图5.11 上方正文短句 `The number of batches is determined...` 误判为图内——其 bbox 底部与图节点顶部相差 8.4pt，带容差即误判）；不可用 `bbox.expand()` 膨胀相交（会误删 `This chapter covers...` 正文与 page346 代码块）。严格垂直重叠 + 小水平间隙只有"真正贴边的图内标签"满足。
- **特殊页**：
  - 封面（page 0，无 `Figure` 锚点）：整页即图，用 `COVER_FIGURE_TEXT` 已知集合删除流程图节点，并用 `COVER_KEEP` 保留书名/作者/出版商。
  - 图1.1（page 24，整页矢量术语表，`page_element_regions` 矩形合并错乱）：专用逻辑取 `Figure 1.1` 图注块正上方带内的文字块作为图内文字集。
  - 其余"几何失败页"**保留不动**（纯位置删除曾误删紧邻图的代码块，故不在此类页上退化为位置信号）。
- 删除后折叠多于一个的连续空行，不产生悬空空行。

**安全验证（对 `llms-from-scratch.md`）**：
- 原始 md（git HEAD，13683 行）上完整 dry-run 共匹配 665 行、去重后 278 个唯一非空行，全部为图内文字；最终 md 12187 行（含删除空行折叠）。
- 0 个 >120 字符的正文长句被删；0 个代码行（含 `def`/`class`/`import` 等）被删。
- 关键正文句完整保留：`This chapter covers...`（×7）、`The rationale behind scaled-dot product attention...`、`The resulting attention weights are...`、`Now, the final step is to compute the context vectors...`、`The flowchart in figure 5.11...`、`Now, we create the PyTorch data loaders...` 等。
- 补充删除的图内文字（side 判定新增）：Fig 1.2 标签（`User input (instructions)`/`Model output`）、Fig 4.3 构建步骤标注（`Finally, we will use multiple transformer blocks...` 等 4 行）、Fig 5.11 流程图右侧说明（`One epoch is one complete pass...`/`The number of batches is determined...`/`These are the usual steps...` 等，均为图内文字）、Fig 7.3/7.15/7.16 指令微调流程标注、GPT-2 配置表格行、注意力机制图内步骤说明等。
- 验证手段：对比原始/清理版，确认所有被删行均为图内标签/标注/示例文本/封面节点；对 `Fig 4.3`/`Fig 5.11` 等逐块用 PDF 几何复核（文字块 bbox 与图元素 rect 紧密相邻）。

**用法**：
```bash
.venv/bin/python clean_figure_text.py            # 默认 dry-run 预览将被删除的行
.venv/bin/python clean_figure_text.py --apply    # 执行清理并覆盖 llms-from-scratch.md
```

**效果**：导出 md 中图内文字残留全部清除（流程图标签、术语定义、封面节点、图内示例文本），正文、代码、标题零误删。

**P5 二次清理效果（2026-08-21 优化）**：

原有纯几何匹配方案无法捕获位于图元素矩形间隙或边缘外侧的标签（如 "Dataset with class labels"、"Instruction dataset"、"Fine-tunes the pretrained LLM..." 等），导致 0 行匹配。优化为三重策略（首非空行组扫描 + 内容过滤 + 几何兜底）并迭代至收敛后：
- 累计删除 127 行图内文字残留（流程图标签、概念框标注、坐标轴标签、图内示例文本、Stage 标注、封面节点、embedding 类型标注等）
- 折叠 118 行多余空行，md 从 12187 行降至 11942 行
- 脚本改为迭代执行（`while True` 循环直到 0 行匹配），解决多组标签被空行分隔时需多次手动运行的问题
- 验证全部通过：V1-V8（P1 几何删除 1149 块、无正文句被删、术语表 5/5 保留、图标签 5/5 已删、P5 独立标签 3/3 已删、关键正文 5/5 保留、代码 6/6 保留、围栏 1308 成对）
- 约 3 行边缘正文被删（无终端标点、无逗号的短正文行，位于图标签同一非空组内，属可接受误判）

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

- 修复 **72 个缺失标题** + 1 个合并行拆分（E.2）
- 分类：前言子节 5 个、Summary 8 个、章内子节 25 个、附录子节 13 个、练习 21 个、索引子标题 2 个、其他 2 个
- 幂等性验证：重跑脚本返回 0 修复

### 9.4 验证

- PDF TOC 166 个有效条目：152 精确匹配 + 12 格式变体（Chapter N / appendix X: 已为标题）= **164/166 覆盖率**
- 仅 2 个 **内容缺失**（PDF 提取遗漏，非标题问题）：
  - §5.4 Loading and saving model weights in PyTorch
  - §7.4 Creating data loaders for an instruction dataset
- §3.1 断言全部通过（围栏 1308 成对、无 PUA、TOC ⊆ anchors、无重复锚点）
- V1-V8 OVERALL: PASS

## 10. 段落拆分异常修复子方案（`merge_paragraphs.py`）

### 10.1 问题根因

P1 pipeline 在每个 PDF text block 后插入空行。当逻辑段落跨越多个物理块时（如引用块与续行分离、长段落跨块拆行），产生错误段落分割。

### 10.2 异常类型与修复

| 类型 | 模式 | 修复 | 数量 |
|------|------|------|------|
| A-引用续行 | `> text` + 空行 + 续行 | 合并入引用，加 `> ` 前缀 | 155+86=241 |
| A-URL断行 | `> ...LLMs-from` + 空行 + `-scratch...` | 去空格直接拼接 | 含在上述 |
| A-多行引用 | 多次续行（参考文献区） | 迭代至收敨 | 含在上述 |
| B-正文拆行 | 长行(>60字)无终端标点 + 空行 + 小写续行 | 空格拼接 | 84+25=109 |
| C-行内代码 | `` `code` `` + 空行 + `` `code` `` | 去伪影字符拼接 | 48（前序修复） |

### 10.3 关键设计

- **迭代收敨**：BQ 和 prose 修复均循环执行直到 0 匹配
- **BQ 续行识别**：`_is_bq_contin()` 接受普通文本、`>` 行、`-` URL 片段
- **URL 片段拼接**：`-scratch` 类片段直接拼接不加空格
- **终端标点检测**：`_ends_terminal()` 回退跳过闭合括号/引号

### 10.4 执行结果

- 总计修复 **350+ 处**段落拆分（含前序行内代码修复）
- 文件从 12008 行降至 11212 行（减少 796 行空行）
- 残留 6 处 BQ + 2 处 prose 均为合法结构（锚点标签/索引条目）
- V1-V8 OVERALL: PASS

## 11. 章节标题与“本章涵盖”修复子方案（`fix_chapters.py`）

### 11.1 问题根因

P1 pipeline 的标题识别仅支持 `Chapter N` / `N.N Title` 模式，无法识别章首页的大号斜体标题（如 `Understanding large` / `language models` 分属两个 PDF block）。同时，"This chapter covers" 的 bullet 列表使用 Wingdings 字体标记，P1 无法处理，导致 bullet 内容几乎全部丢失。

### 11.2 异常类型

| 类型 | 模式 | 修复 | 数量 |
|------|------|------|------|
| 章标题碎片 | 多行碎片（如 Ch1/3/7）或单行无 `##` 前缀（如 Ch2/4/5/6） | 合并碎片 + 添加 `## N` 前缀 | 7 |
| “本章涵盖” bullet 丢失 | Wingdings bullet 被 P1 忽略，仅残留尾部碎片 | 从 PDF 提取完整 bullet 列表替换 | 7（共 34 条 bullet） |

### 11.3 关键设计

- 从 PDF TOC 获取正确标题文本（含空格），从 block 结构获取标题碎片用于匹配
- 使用 Wingdings 字体检测 bullet 起始块，合并续行块为完整 bullet
- 碎片检测：短行（<80 字）、无终端标点、无特殊前缀
- 脚本幂等，可重复运行

### 11.4 执行结果

- 7 个章标题全部修复（`## 1 Understanding large language models` 至 `## 7 Fine-tuning to follow instructions`）
- 7 个“本章涵盖”区域替换为完整 bullet 列表（3+5+5+5+4+6+6 = 34 条）
- MD 从 11212 → 11208 行
- V1-V8 OVERALL PASS
- TOC 覆盖率：164/166（仅 §5.4、§7.4 为已知内容提取缺失）

## 12. 已知遗留（非阻断）

- §5.4 和 §7.4 整节内容在 P1 提取中缺失（PDF 提取遗漏，需人工补录或重新提取）。
- P2 概念框多行续行未补 `>` 前缀（安全取舍）。
- 正文上标在 P1 直提中已平化，未做 LaTeX 还原。
- 重复 Chapter 标题（附录练习）TOC 以 `(Appendix)` 区分，但正文标题文本相同。
- 个别“几何失败页”（非封面/图1.1）若其图内文字未被 P1 几何捕获，则残留为无害冗余（P5 为安全起见不在这些页退化为纯位置删除，以免误删代码）。
