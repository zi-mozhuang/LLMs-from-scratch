# PDF → Markdown 转换与清洗方案

本目录存放将《Build a Large Language Model (From Scratch)》PDF 转换为可阅读 Markdown 的工具和产物。

> **修改脚本前请以 `fix_code_blocks.py` 的实际代码为准**。每个修复函数上方均有
> `⚠️ DO NOT REMOVE — 对应缺陷 #X` 标记，不要删除带标记的函数；删掉任一函数都会让对应缺陷重现。

## 工具脚本

| 文件 | 作用 |
|------|------|
| `convert.py` | 统一入口：调用 `fix_code_blocks.process_pdf_to_markdown()` 生成最终 Markdown。 |
| `fix_code_blocks.py` | 核心后处理库：代码块提取恢复 + 格式清洗管道。 |
| `output/Build-a-LLM-from-scratch.md` | 最终 Markdown 文件。 |
| `output/images/` | 从 PDF 提取的图片。 |

## 使用方式

```bash
python convert.py
```

`convert.py` 是唯一入口，内部调用 `fix_code_blocks.process_pdf_to_markdown(pdf, md, img_dir)`。

## 修复流程规范（提速）

对 `fix_code_blocks.py` 中任一修复函数的改动，**必须先做小范围测试，验证通过后再重新完整生成 md 做全量校验**，避免每次改动都跑完整管道浪费时间。

1. **小范围测试**：直接在脚本内或交互式 Python 中，针对受影响的单页/单函数做最小验证——例如改动 `fix_split_figures` 时，只对该函数涉及的页面（如第27/50/74页）渲染裁剪并肉眼/像素检查尺寸与内容，不调用 `process_pdf_to_markdown()` 全管道。
2. **全量校验**：小范围测试无误后，再运行 `python convert.py`（或 `python fix_code_blocks.py`）完整重新生成 `output/Build-a-LLM-from-scratch.md`，检查 lint、图片尺寸、图注与正文位置等全局指标。
3. **原则**：能用单函数/单页复现的缺陷，绝不在全管道上反复试错；全管道仅用于最终确认，不用于探索性调试。

## 处理管道

`fix_code_blocks.py` 的实际后处理顺序（与脚本中 `process_pdf_to_markdown` 末尾管道一致）：

1. **代码块提取**：遍历 PDF 每页，用字体信息识别 Courier 代码块（保留原始行结构与缩进）；边注（bold-italic 文字）单独收集。
2. **代码块恢复**：逐行流匹配，用 anchor key 定位代码在 `pymupdf4llm` 输出中的位置，替换为 ` ```python `/` ```bash ` 围栏块；边注转成 blockquote 附在代码块后（`try_match` 带 `protected` 守卫，避免吞掉图注 / picture-text 行）。
3. **图片路径替换**：`output/images/` → `images/`。
4. **删独立页码**：删除 `**27**` 类页码行。
5. **无效语言标记**：` ```py ` → ` ```python `。
6. **数学公式修复**（第一次）：`fix_math_superscripts` —— `<sup>`→LaTeX、PUA 希腊字母映射、点乘 `⋅`/`·`→`\cdot` 并包 `$...$`；断裂上标片段修复。
7. **排版残留清理**：`clean_annotations` 删除 `<mark>` 标签、`(continued)` 分页提示。
8. **围栏平衡**：`ensure_fences_balanced` 校验 ``` 成对，补缺失闭围栏（幂等）。
9. **页眉清理**：`strip_page_headers` 删孤立页眉词（PREFACE/CONTENTS/APPENDIX…）、`CHAPTER N **_标题_**` / `APPENDIX X **_标题_**` 运行头、`**_N.M 小节_**` 粗斜体运行头、罗马页码 `**xi**`。须在硬换行合并前。
10. **图注归位**：`pair_figures_captions` 将 `Figure X.Y ...` 行**原地**转引用块 `> **Figure X.Y** ...`（不做移动；对应由 pymupdf4llm 输出布局保证）。
10b. **图片碎片合并**：`fix_split_figures` 双策略合并被 pymupdf4llm 拆分的同页图片组为单张 `figure-<页>.png`：(A) 若页面存在 PDF 栅格图片对象（如第27页 Figure 1.2），取图片对象 union bbox 并向右扩展纳入紧邻右侧文字标注（如 "User input"/"Model output"），从整页渲染裁剪——精确不吞正文、不丢侧标；(B) 纯矢量页面（如第50/74页）用 numpy FFT NCC 模板匹配定位碎片并 union 裁剪。跳过封面页，单图页不动。
11. **段落硬换行合并**：`merge_prose_hard_breaks` 保守合并 PDF 物理行宽造成的段落内硬换行（含跨页空行隔断的散文续行），跳过代码块内部与索引页。
10c. **旁注转引用块**：`fix_margin_notes` 把以 `NOTE` 开头的独立段落转成 Markdown 引用块 `> NOTE ...`（多行 NOTE 逐行加 `>`），与代码块边注 blockquote 风格统一。仅在段首为 `NOTE ` 且为独立段时处理。
10d. **矢量图顶部标注恢复**：`fix_figure_label_headings` 检测"数字. 描述"格式误判标题（如 `#### **8. The complete output (translation)**`）且其后紧邻 pymupdf4llm 页面图时：删除标题行（图标注非结构标题），用**矢量绘制边界为锚、并入与其重叠或紧邻(<25pt)的文本标注块**从整页渲染裁剪完整 Figure（含顶部标注，自动排除页眉/图注/下方正文），覆盖为 `figure-<页>.png` 并删除旧碎片。
10e. **概念框转引用块**：`fix_callout_blocks` 把书中带浅黄色填充背景 (0.969,0.961,0.910) 的侧边栏概念框（"This chapter covers"、"Transformers vs. LLMs"、"Cross entropy loss"、各 "Exercise X.Y" 等，全书 70 处）转成 blockquote：用框首行文本定位 md 中标题行（跳过已被占用的行），收集其后到下一个标题行，标题 `> **Xxx**`、内容逐行加 `>`。须在 `add_toc` 之后、`fix_faux_headings` 之前调用。
12. **数学公式修复**（第二次，幂等）：重排后暴露的断裂上标。
13. **目录可跳转**：`add_toc` 把目录页替换为嵌套 Markdown 目录、标题加 `<a id>` 锚点、升格缺失章标题为 `##`。**必须放最后（在所有清洗之后）**。
14. **标题分级**：`relevel_headings` 将 `######` 小节标题按结构升为 `###`/`####`/`#####`（必须在 `add_toc` 之后，依赖其将章标题升格为 `##` 以重置上下文）。

## 功能地图（修改脚本前必读）

每个函数对应一个独立的 PDF 缺陷修复，互不依赖。

| 功能 | 函数 | 解决什么 | 边界（别误删） |
|------|------|----------|----------------|
| 字体判定 | `is_code_font` | Courier=代码 | 前缀匹配即可 |
| 代码提取 | `block_keys`/`extract_notes`/`make_replacement` | 从 PDF 提取代码块与边注 | 边注阈值 len≥15 且字母≥8 |
| 流匹配 | `try_match` | 在 md 流中定位代码块 | `protected` 守卫：span 跨越图注/picture-text 则拒绝，试下一处锚点 |
| 数学修复 | `fix_math_superscripts` | `<sup>`→LaTeX、PUA 希腊字母、点乘→`\cdot` | **管道中调用两次**，勿只留一次 |
| 图片内文字 | `strip_picture_text` | 删 `<!-- picture text -->` 内嵌重复文字 | 只认 start/end 两标记 |
| 封面整页图提取 | `extract_full_cover_image` | 绕过 pymupdf4llm 的截断，用 PyMuPDF 重提封面整页图 | pymupdf4llm 会把整页封面图截成 278×278 方形，必须用此函数 |
| 封面归一化 | `fix_cover_page` | 封面碎片块→单张完整整页封面图 | 依赖 `extract_full_cover_image` 提供的图 |
| 排版清理 | `clean_annotations` | 删 `<mark>`、`(continued)` | 只删这两类 |
| 围栏平衡 | `ensure_fences_balanced` | 补缺失闭围栏 | 不移动围栏、不删代码内容 |
| 页眉清理 | `strip_page_headers` | 删 CHAPTER/APPENDIX/小节运行头、罗马页码 | 小节标题本体保留（有 `##`/`###` 对应） |
| 图片碎片合并 | `fix_split_figures` | 同页碎片图合并为单张 `figure-<页>.png`：栅格图页用 PDF 图片对象 union bbox + 右侧文字标注扩展裁剪；纯矢量页用 numpy FFT NCC 模板匹配 union 裁剪 | 跳过封面页；单图页不动；NCC 阈值 0.25 |
| 旁注转引用块 | `fix_margin_notes` | 独立 `NOTE ...` 段→`> NOTE ...` blockquote | 仅在段首 `NOTE ` 且前一行空行时处理；不误伤内文 "NOTE" |
| 矢量图标注恢复 | `fix_figure_label_headings` | "数字. 描述"误判标题+紧邻页面图→删标题、整页渲染裁剪完整 Figure（含顶部标注） | 仅匹配 `数字.` 开头标题且其后≤3行有图片；跳过封面页 |
| 概念框转引用块 | `fix_callout_blocks` | 浅黄色侧边栏概念框→`> **标题**` + 内容 blockquote | 仅匹配填充色 (0.969,0.961,0.91)、宽≥100pt 高≥40pt 的框；须在 `add_toc` 后、`fix_faux_headings` 前 |
| 图注归位 | `pair_figures_captions` | 图注行原地转 `> **Figure X.Y** ...`，不移动 | 不做全局配对（封面/肖像图会错位） |
| 段落合并 | `merge_prose_hard_breaks` | 物理行宽断行→连续段落 | 代码块内 / 索引页绝不触碰 |
| 目录生成 | `add_toc` | 可点击 TOC + `<a id>` 锚点 + 缺失章标题升格 | **必须最后调用**；只改 TOC/标题 |
| 伪标题→粗体 | `fix_faux_headings` | `### _Xxx_` 粗斜体小标签→`**_Xxx_**` 粗体 | 仅匹配 `_..._` 包裹的标题行；**`add_toc` 之后调用**（避免影响目录定位标记） |
| 标题分级 | `relevel_headings` | `######`→`###`/`####`/`#####` | **`add_toc` 之后调用** |

## 验证指标

```python
import re

text = open("output/Build-a-LLM-from-scratch.md", encoding="utf-8").read()

# 1. 围栏必须成对闭合
fences = [l for l in text.split("\n") if l.strip().startswith("```")]
assert len(fences) % 2 == 0
assert re.search(r"^```python\s*$", text, re.M)
assert not re.search(r"^```py\s*$", text, re.M)  # 无无效语言标记

# 2. 常见缺陷残留清零
assert "(continued)" not in text.lower()
assert not re.search(r"[\uF000-\uF0FF]", text)   # 无 PUA 希腊字母
assert "<sup>" not in text
assert "<mark>" not in text
assert not re.search(r"Start of picture text", text)  # 图片内文字已删

# 3. 目录可跳转：所有目录链接都有对应锚点（add_toc 生成）
anchors = set(re.findall(r'<a id="([^"]+)">', text))
links = set(re.findall(r'\]\(#([^)]+)\)', text))
assert links <= anchors
```

当前输出统计：1055/1092 代码块匹配（37 个未匹配均为输出内容/张量值数组，可接受）；图注 142 个全部保留原位并转为引用块；围栏 1798（成对平衡）；锚点 112，目录链接全部可解析；`<sup>`/`(continued)`/PUA/图片内文字/点乘/页眉残留均为 0。

## 已知局限

- 代码缩进来自 PDF 字体的原始空白；个别非 Courier 字体的代码片段可能漏识别（字体判断阈值 ≥75%）。
- PDF 代码块跨页 / 跨 block 拆分时，会拆成多个独立围栏，中间残留少量碎片文本（如 `nspose(1, 2)`、`u`）。无双空格且无代码标记的极短残行（如 `).`、`.`）保守保留，以免误删正文。
- 图注采用原地转引用块，不移动任何行。部分图片在 PDF 中无独立栅格图形（如 Figure 1.7、2.14、5.7、7.8），其图注在 md 中前无图片——是 PDF / 提取器局限，非脚本问题。
- 封面整页大图已由 `extract_full_cover_image` 用 PyMuPDF 重提为完整的 `images/cover-full.png`（2222×2784）；pymupdf4llm 额外提取的残缺封面文字/作者/出版社视觉；pymupdf4llm 额外提取的残缺封面文字（`# BUILD A`、作者、`**M A N N I N G**`）及装饰 logo 图（0001-03）已由 `fix_cover_page` 归一为单张整页封面图，不再提取封面文字、不再分块。扉页（独立正式页）的书名/作者/出版社文字予以保留。

## 本次修改记录（2026-08-20）

### A. 图片类问题（已完成）

- **[7] 图注归位**：`pair_figures_captions(text)` 原地转引用块（不做移动），`try_match` 的 `protected` 守卫防吞图注；142 个图注全部保留原位、规范为 `> **Figure X.Y** ...`、单行。
- **[9b] 封面归一化**：`strip_picture_text(text)` 删除全部 280 处 `<!-- picture text -->` 块；`extract_full_cover_image(pdf, img_dir)` 用 PyMuPDF 重新提取封面页整页大图（pymupdf4llm 会把整页封面图错误截断/缩放成 278×278 方形，故必须绕过它，保存为 `images/cover-full.png`，尺寸 2222×2784 完整）；`fix_cover_page(text, cover_img)` 把封面碎片块（`# BUILD A` 标题 + 残缺封面图 + 作者 + 装饰 logo 图 `0001-03` + `**M A N N I N G**`）归一为单张完整整页封面图 `![](images/cover-full.png)`（alt 文本含完整书名）。封面文字不再提取、不再分块、封面完整。扉页（独立正式页）书名/作者/出版社文字保留。
- **[8b] 图片碎片合并**：`fix_split_figures(text, pdf, img_dir)` 双策略：栅格图页（第27页 Figure 1.2）用 PDF 图片对象 union bbox 并向右扩展纳入紧邻文字标注（"User input"/"Model output"），从整页渲染裁剪，精确不吞正文、不丢侧标；纯矢量页（第50/74页）用 **numpy FFT 归一化互相关模板匹配**定位 pymupdf4llm 碎片后 union 裁剪（捕获矢量标注/箭头）。替换 md 中连续同页图片组（允许组间空行）为单张 `figure-<页>.png`。已修复 3 处：Figure 1.2（第27页，760×519，含右侧文字标注）、Figure 2.8（第50页，773×369）、Figure 3.3（第74页，787×553）。跳过封面页；单图页保留 pymupdf4llm 原图；图注无损失。
- **[8c] 矢量图顶部标注恢复**：`fix_figure_label_headings(text, pdf, img_dir)` 修复纯矢量 Figure 顶部编号标注丢失（第30页 Figure 1.4）：pymupdf4llm 把图顶部标注 "8. The complete output (translation)" 误判为 `#` 标题（后降为 `####`），而渲染图本身不含该标注 → 图片顶部文本丢失。检测"数字. 描述"误判标题紧邻页面图时：删除标题行、用**矢量绘制边界为锚**并入重叠/紧邻(<25pt)的文本标注块，从整页渲染裁剪完整 Figure（含顶部标注，自动排除页眉/图注/下方正文）为 `figure-0030.png`（825×615），并删除旧碎片 `0030-03.png`。全本仅此 1 处受影响，目录链接无损失。

### B. 文字类格式修复（已完成）

| # | 缺陷 | 结论 |
|---|------|------|
| 1 | Python 代码缩进丢失 | **已解决**：缩进来自 PDF 字体原始空白。跨页长方法极短残行（如 `).`）保守保留。 |
| 2 | Callout 边注混入代码块 | **已解决**：边注被提取为代码块后的 blockquote，不混入代码。 |
| 3 | 数学符号乱码 / 畸形公式 | **已解决**：`<sup>`→LaTeX、PUA 希腊字母映射、点乘 `⋅`/`·`→`\cdot` 并包 `$...$`（如 `GELU(x) = $x \cdot \Phi(x)$`）。残留 0。 |
| 4 | 段落硬换行 | **已解决**：`merge_prose_hard_breaks` 合并段落内硬换行及跨页空行隔断的散文续行；代码块内与索引页不合并。 |
| 5 | 残留 HTML 锚点 `<a id=>` | **有意保留**：`add_toc` 为目录目标标题插入 `<a id="slug">` 锚点（112 处，目录跳转依赖），非缺陷。 |
| 6 | `(continued)` 排版残留 | **已解决**：`clean_annotations` 已删除，残留 0。 |
| 7 | 图注与图片分离 / 图注硬换行 | **已解决**：见 A 节。 |
| 8 | 代码块语言标记缺失/边界模糊 | **已解决**：` ```python `/` ```bash ` 已恢复；`ensure_fences_balanced` 校验围栏成对并补缺失闭围栏，产物 1798 个围栏、偶数、平衡。 |
| 9 | 伪标题（粗斜体小标签误判为 `###` 标题） | **已解决**：`fix_faux_headings` 把 `### _This chapter covers_`/`_Summary_`/`_About the code_` 及目录/练习页 `_Chapter 2_`/`_Exercise 2.2_` 等粗斜体小标签（原书非结构标题）转回粗体 `**_Xxx_**`；真实章节标题（无 `_` 包裹）不受影响。59 处全部转粗体，目录 112 链接未受影响。 |
| 10 | 正文旁注 NOTE 未用 `>` blockquote 区分 | **已解决**：`fix_margin_notes` 把以 `NOTE` 或 `- NOTE` 开头的独立段落（pymupdf4llm 两种输出变体）都转成 `> NOTE ...` 引用块，与代码块边注 blockquote 风格统一；全本 28 处 `NOTE` 旁注全部转换，无遗漏、无误伤内文 "NOTE" 或普通 `- ` 列表项。 |
| 11 | 概念解释框/侧边栏未与正文区分 | **已解决**：`fix_callout_blocks` 把书中浅黄色填充背景 (0.969,0.961,0.91) 的侧边栏概念框（"This chapter covers"、"Transformers vs. LLMs"、"Cross entropy loss"、各 "Exercise X.Y" 等，全书 70 处）转成 `> **标题**` + 内容的 blockquote；用框首行文本定位标题行（跳过已占用行，处理同名标题跨页），标题 `> **Xxx**`、内容逐行加 `>`；图注 142 处、目录锚点 112 处均无损失。 |
