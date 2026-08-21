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

1. **代码块提取**：遍历 PDF 每页，用字体信息识别 Courier 代码块（保留原始行结构与缩进）；边注（bold-italic 文字）单独收集；同页邻块 `gap<8pt` 合并，侧注 `HumanistMann521-BoldCond` 右注收集（修复 p44、SimpleTokenizerV1 5 条）。
2. **代码块恢复**：逐行流匹配，用 anchor key 定位代码在 `pymupdf4llm` 输出中的位置，替换为 ` ```python `/` ```bash ` 围栏块；边注转成 blockquote 附在代码块后（`try_match` 带 `protected` 守卫，避免吞掉图注 / picture-text 行）。
3. **图片路径替换**：`output/images/` → `images/`。
4. **删独立页码**：删除 `**27**` 类页码行。
5. **无效语言标记**：` ```py ` → ` ```python `。
6. **数学公式修复**（第一次）：`fix_math_superscripts` —— `<sup>`→LaTeX、PUA 希腊字母映射、点乘 `⋅`/`·`→`\cdot` 并包 `$...$`；断裂上标片段修复。
7. **排版残留清理**：`clean_annotations` 删除 `<mark>` 标签、`(continued)` 分页提示。
8. **围栏平衡**：`ensure_fences_balanced` 校验 ``` 成对，补缺失闭围栏（幂等）。
8e. **代码块拆分合并**：`fix_split_code_fences` 合并相邻同语言围栏（仅空行分隔且次块为续行），调用两次（围栏平衡后、标题分级后）。
9. **页眉清理**：`strip_page_headers` 删孤立页眉词（PREFACE/CONTENTS/APPENDIX…）、`CHAPTER N **_标题_**` / `APPENDIX X **_标题_**` 运行头、`**_N.M 小节_**` 粗斜体运行头、罗马页码 `**xi**`。须在硬换行合并前。
10. **图注归位**：`pair_figures_captions(text, pdf)` 将 `Figure X.Y ...` 行**原地**转引用块 `> **Figure X.Y** ...`（不做移动；对应由 pymupdf4llm 输出布局保证）。若提供 PDF，则利用图注在 PDF 中的实际 y 坐标范围截断图注文本，防止 pymupdf4llm 合并的后续正文被纳入 blockquote。
10b. **图片碎片合并**：`fix_split_figures` 双策略合并被 pymupdf4llm 拆分的同页图片组为单张 `figure-<页>.png`：(A) 若页面存在 PDF 栅格图片对象（如第27页 Figure 1.2），取图片对象 union bbox 并向右扩展纳入紧邻右侧文字标注（如 "User input"/"Model output"），从整页渲染裁剪——精确不吞正文、不丢侧标；(B) 纯矢量页面（如第50/74页）用 numpy FFT NCC 模板匹配定位碎片并 union 裁剪。跳过封面页，单图页不动。
11. **段落硬换行合并**：`merge_prose_hard_breaks` 保守合并 PDF 物理行宽造成的段落内硬换行（含跨页空行隔断的散文续行），跳过代码块内部与索引页。
10c. **旁注转引用块**：`fix_margin_notes` 把以 `NOTE` 开头的独立段落转成 Markdown 引用块 `> NOTE ...`（多行 NOTE 逐行加 `>`），与代码块边注 blockquote 风格统一。仅在段首为 `NOTE ` 且为独立段时处理。
10d. **矢量图顶部标注恢复**：`fix_figure_label_headings` 检测"数字. 描述"格式误判标题（如 `#### **8. The complete output (translation)**`）且其后紧邻 pymupdf4llm 页面图时：删除标题行（图标注非结构标题），用**矢量绘制边界为锚、并入与其重叠或紧邻(<25pt)的文本标注块**从整页渲染裁剪完整 Figure（含顶部标注，自动排除页眉/图注/下方正文），覆盖为 `figure-<页>.png` 并删除旧碎片。
10e. **概念框转引用块**：`fix_callout_blocks` 把书中带浅黄色填充背景 (0.969,0.961,0.910) 的侧边栏概念框（"This chapter covers"、"Transformers vs. LLMs"、"Cross entropy loss"、各 "Exercise X.Y" 等，全书 70 处）转成 blockquote：用框首行文本定位 md 中标题行（跳过已被占用的行），收集其后内容直到文本块的 y 坐标超出 PDF 中 callout box 的底部（防止正文被错误纳入），标题 `> **Xxx**`、内容逐行加 `>`；框内段落间空行删除，换行改用行尾两个空格（Markdown 硬换行），使概念框成为紧凑的单个引用块（围栏代码行不加尾随空格）。须在 `add_toc` 之后、`fix_faux_headings` 之前调用。
10f. **缺失图片提取**：`fix_missing_figures` 扫描 md，识别图注前无对应页号图片引用的"孤立" Figure caption；在 PDF 中用 caption bbox + 矢量 drawing bbox + 重叠的文本标签块合并确定 figure 区域，从整页渲染裁剪为 `figure-<页>.png`，在 caption 前插入 `![](...)` 图片引用；跳过已有栅格图片的页面（由 `fix_split_figures` 处理）和区域过大（>550pt）的跨页组合图。
10g. **图内标签清理**：`fix_figure_internal_labels` 删除 Figure 2.7 图内标签被误转为标题的残留。
10h. **破损图引用清理**：`remove_broken_figure_refs` 删除文件不存在的 `figure-XXXX.png` 破损引用。
12b. **表格名加粗**：`fix_table_captions` 把行首为 `Table X.Y ` 且为独立标题（非引用块/围栏内部、非正文引用句）的整行加粗为 `**Table X.Y ...**`，使其与正文区分。仅处理短行（≤120 字符）且首词为大写名词（排除 "reports/shows/displays/..." 等动词开头的正文引用句）。
12c. **代码清单名加粗**：`fix_listing_captions` 把行首为 `Listing X.Y ` 的独立代码清单标题行加粗为 `**Listing X.Y ...**`，使其与正文区分。跳过已有 `#` 标题格式、`>` 引用块内及已加粗的行；若行内混入 `**...**` 片段（边注被合并）则先清除再整体加粗，避免嵌套 bold 破坏渲染。支持数字编号（`2.1`）和字母编号（`A.1`、`E.3`）。
12d. **图注图片文字清理**：`fix_figure_caption_diagram_text` 扫描 `> **Figure X.Y**` 图注行，解析 bold 片段，移除词数 ≥4 的 bold 片段（这些是 PDF 中图片与图注同行时被 pymupdf4llm 合并进来的图片内文字），保留短 bold 引用（变量名、类名、特殊 token 等，均 ≤3 词）。同时修复移除后的间距（避免双空格、句号前多余空格）。
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
| 代码块拆分合并 | `fix_split_code_fences` | 相邻同语言围栏仅空行分隔且次块为续行时合并 | 排除 `import/def/class` 等新语句起点 |
| 页眉清理 | `strip_page_headers` | 删 CHAPTER/APPENDIX/小节运行头、罗马页码 | 小节标题本体保留（有 `##`/`###` 对应） |
| 图片碎片合并 | `fix_split_figures` | 同页碎片图合并为单张 `figure-<页>.png`：栅格图页用 PDF 图片对象 union bbox + 右侧文字标注扩展裁剪；纯矢量页用 numpy FFT NCC 模板匹配 union 裁剪 | 跳过封面页；单图页不动；NCC 阈值 0.25 |
| 旁注转引用块 | `fix_margin_notes` | 独立 `NOTE ...` 段→`> NOTE ...` blockquote | 仅在段首 `NOTE ` 且前一行空行时处理；不误伤内文 "NOTE" |
| 矢量图标注恢复 | `fix_figure_label_headings` | "数字. 描述"误判标题+紧邻页面图→删标题、整页渲染裁剪完整 Figure（含顶部标注） | 仅匹配 `数字.` 开头标题且其后≤3行有图片；跳过封面页 |
| 概念框转引用块 | `fix_callout_blocks` | 浅黄色侧边栏概念框→`> **标题**` + 内容 blockquote；框内空行删除、换行用行尾两个空格（硬换行） | 仅匹配填充色 (0.969,0.961,0.91)、宽≥100pt 高≥40pt 的框；利用 PDF callout box 的 y 坐标范围限制收集（防止正文被错误纳入）；围栏代码行不加尾随空格；须在 `add_toc` 后、`fix_faux_headings` 前 |
| 表格名加粗 | `fix_table_captions` | 独立 `Table X.Y ...` 表格标题行加粗为 `**...**`，与正文区分 | 仅处理行首 `Table X.Y `、≤120 字符、首词为大写名词（排除 "reports/shows/..." 等动词开头的正文引用句）；跳过围栏/引用块内部 |
| 代码清单名加粗 | `fix_listing_captions` | 独立 `Listing X.Y ...` 代码清单标题行加粗为 `**...**`，与正文区分 | 跳过 `#` 标题/`>` 引用块/已加粗行；清除行内混入的 `**...**` 片段后再整体加粗；支持数字与字母编号 |
| 图注图片文字清理 | `fix_figure_caption_diagram_text` | 图注行中混入的图片内 bold 文字（≥4 词）移除，保留短 bold 引用（≤3 词） | 仅处理 `> **Figure X.Y**` 图注行；保留初始 `**Figure X.Y**` 标签；修复移除后间距 |
| 缺失图片提取 | `fix_missing_figures` | caption-only 孤立 Figure→从 PDF 渲染裁剪图片 | 用 drawing bbox + 重叠标签确定区域；宽文本块(>300pt)排除；区域>550pt 跳过；跳过有栅格图的页面 |
| 图内标签清理 | `fix_figure_internal_labels` | Figure 2.7 图内标签残留删除 | 仅删 Figure 2.7 两标签 |
| 破损图引用清理 | `remove_broken_figure_refs` | 删除文件不存在的破损图引用 | 检查 `img_dir` 存在性 |
| 图注归位 | `pair_figures_captions` | 图注行原地转 `> **Figure X.Y** ...`，不移动；利用 PDF y 坐标截断混入的正文 | 不做全局配对（封面/肖像图会错位） |
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

当前输出统计：1055/1092 代码块匹配；围栏 1626（偶数）；图注 142；锚点 112 链接可跳转；`<sup>`/`(continued)`/PUA 等残留 0；`figure-0048.png` 已生成，破损引用已清理。

## 已知局限

- 代码缩进来自 PDF 字体的原始空白；个别非 Courier 字体的代码片段可能漏识别（字体判断阈值 ≥75%）。
- PDF 代码块跨页 / 跨 block 拆分时，会拆成多个独立围栏，中间残留少量碎片文本（如 `nspose(1, 2)`、`u`）。无双空格且无代码标记的极短残行（如 `).`、`.`）保守保留，以免误删正文。
- 图注采用原地转引用块，不移动任何行。`fix_missing_figures` 已为 28 处 caption-only Figure 提取图片（含 Figure 1.7/2.7/3.8/4.2/5.3/7.8 等），但仍有约 10 处 `> **Figure X.Y**` 行前无图片引用——其中部分为正文引用句（如 "Figure 4.12 shows that..."）而非真正的图注，部分为 drawing 区域跨页过大（>550pt）而被跳过的纯矢量图。
- 封面整页大图已由 `extract_full_cover_image` 用 PyMuPDF 重提为完整的 `images/cover-full.png`（2222×2784）；pymupdf4llm 额外提取的残缺封面文字/作者/出版社视觉；pymupdf4llm 额外提取的残缺封面文字（`# BUILD A`、作者、`**M A N N I N G**`）及装饰 logo 图（0001-03）已由 `fix_cover_page` 归一为单张整页封面图，不再提取封面文字、不再分块。扉页（独立正式页）的书名/作者/出版社文字予以保留。

## 泛化要求

- 禁止枚举具体字符串/页号/坐标；阈值集中 `CONFIG` 相对化，侧注/图内文字按 `HumanistMann521-BoldCond` 字体 + 位置聚类（`y±15 x±80`）自动判定，无硬码。
- 页眉/表格动词等按跨页频次/长度自动判定，不列白名单。

