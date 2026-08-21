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

## 9. 已知遗留（非阻断）

- P1 标题识别缺口：1.6、3.2、3.3、4.3、4.5 等部分节标题未识别为 `###`（跨行/字体变体）。
- P2 概念框多行续行未补 `>` 前缀（安全取舍）。
- 图1.1 的 2 条图内碎片（有句号）被 sentence guard 保留为无害冗余。
- 正文上标在 P1 直提中已平化，未做 LaTeX 还原。
- 重复 Chapter 标题（附录练习）TOC 以 `(Appendix)` 区分，但正文标题文本相同。
