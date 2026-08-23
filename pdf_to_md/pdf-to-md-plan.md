# PDF → Markdown 转换方案

> **目标**：将 `Build a Large Language Model (From Scratch) (Sebastian Raschka).pdf` 转为可阅读 Markdown。
> **路线**：图片独立提取 + 文本流直接转换（PyMuPDF 直提，不复用 pymupdf4llm，不 import 旧补丁链）。
> 本文件是方案汇总与经验依据，与下方脚本一一对应。详细子方案见 `attachments/` 目录。

## 流水线总览

> **2025-08 重构后**：单一入口 `bash run_pipeline.sh` 一次产出最终 MD。
> 数据流固定为 **P0 → extract → classify → merge → render → verify**；
> 旧补丁链脚本（p1/p2/p3/fix_*/clean_*/rebuild_toc/check_*/audit 等）已归档至
> `.backup/legacy_scripts/`（只读留档，管道不再依赖）；`figure_text_detect.py`
> 与 `p0_extract_images.py` 按重构决定保留在根目录原位。
> 回归基准与差异分诊见 `golden/`（含 `diff_triage.md`）。

| 阶段 | 脚本 | 输入 → 输出 |
|------|------|------------|
| P0 图片提取 | `p0_extract_images.py` | PDF → `extracted_images/{embedded,figures}/` + `manifest.json` |
| 提取 | `pipeline/extract.py` | PDF → `Page/Block` 页模型（一次拿全字体/颜色/绘图/TOC） |
| 分类 | `pipeline/classify.py` | 页模型 → 语义块（code/heading/concept_box/note/bullet/callout 等，规则前移到提取期） |
| 合并 | `pipeline/merge.py` | 块级合并（段落/代码/两行标题/断词，词表唯一来源 `mdlib/config.py`） |
| 渲染 | `pipeline/render.py` (+`patches.py`/`index.py`) | 语义块 → 最终 `llms-from-scratch.md`（图文合成、数学清洗、加粗、TOC+锚点、内容锚点补丁、索引重排） |
| 校验 | `mdlib/asserts.py` + `pipeline/verify.py` | 围栏成对/无 PUA/无 sup-mark/TOC⊆锚点断言 + TOC/Figure/概念框/covers 完备性对账 + 图片文件完整性 |
| manifest 增强 | `pipeline/manifest_enrich.py` | 图片条目补 source/md5/bytes/caption（幂等，render 后自动执行） |
| 子模块 图内文字 | `figure_text_detect.py` | 被 pipeline/extract 调用，几何法剔除图内文字 |
| 归档 验证套件 | `.backup/legacy_scripts/check_*.py`、`audit_md_quality.py` | 外部回归套件（不属管道），baseline 存于 `golden/baseline_*.txt` |

<details>
<summary>旧补丁链总览（已归档，仅供考古）</summary>

| 阶段 | 脚本 | 输入 → 输出 |
|------|------|------------|
| P1 文本流 | `p1_text_stream.py` | PDF → 文本流 Markdown（页眉剔除、代码块还原、段落合并） |
| P2 清洗合成 | `p2_clean.py` | P1 输出 → 图文合成 + 断词/概念框/数学/加粗清洗 |
| P3 TOC 校验 | `p3_toc.py` | P2 输出 → 最终 `llms-from-scratch.md`（TOC + 锚点 + 断言） |
| 修复 round1 | `fix_structure.py --phase round1` | 标题级别 + 章节标题/本章涵盖（headings + chapters） |
| 修复 gaps | `fix_special_blocks.py` / `clean_figure_text.py` / `fix_line_continuity.py` / `fix_missing_sections.py` / `fix_structure.py --phase pseudo` / `fix_index.py` / `render_appendix_figures.py` | 异形块、图内残留、行连续性、缺失节、伪标题/损坏标题、索引、附录图 |
| 修复 round2 | `fix_structure.py --phase round2` + `rebuild_toc.py` | 格式一致性 + 章节封面 + TOC 重写 |
</details>


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

## 4. P0 图片提取（`p0_extract_images.py`）

双通道：
- **A. 嵌入位图** → `extracted_images/embedded/`（原始字节，按 xref）
- **B. 矢量图** → `extracted_images/figures/`（区域渲染 @3x）

三阶段（每阶段独立自测）：
1. `find_caption_candidates`：行级 `Figure X.Y` 扫描（含正文引用）
2. `validate_candidates`：几何校验剔除引用，保留真正图注
3. `build_figure_region`：从种子 + 标签块生长图区域

校验：V1 位图完整性（33 xref）、V2 图表覆盖率（143 标题逐一配对 PNG）、V3 内容有效性（像素方差/最小边）。
用法：`python3 p0_extract_images.py`（全量 + V1-V3）、`test-candidates` / `test-validate`（单阶段自测）。

## 5. P1 文本流转换（`p1_text_stream.py`）

**设计**：`block → line → span` 三级流式重建，替代 pymupdf4llm。

- **页眉/页脚删除（span 级）**：字体∈`HEADER_FONTS` 且位于顶部/底部边距（top<35pt / bottom>页高-75pt）且字号<10.5pt → 剔除该 span。
- **代码块还原（含跨 block 合并）**：block 内非页眉 span 中 Courier 占比 ≥75% → fenced block；正文内 Courier 片段包行内 `` `code` ``。连续代码 block 用 `pending_code` 缓冲合并（详见 §14）。
- **节标题层级**：`^\d+\.\d+\s+[A-Z]` → `### `；`^Chapter/Appendix` → `## `；`^Part\s+[IVX]+` → `## `；`^Summary$` → `### `。跨行拼接标题。
- **段落合并**：block 内多行合并为一段；跨 block 段落用 `pending_prose` 缓冲合并（详见 §10）。
- **图内文字删除**：每页预计算 `page_element_regions`，跳过图内文字块（见 §8）。

## 6. P2 图文合成与清洗（`p2_clean.py`）

管道顺序（固化）：页眉删除(P1) → 段落合并(P1) → 以下 7 步：

1. **断词连字符合并**（复合前缀排除词表保留真复合词如 `self-attention`）
2. **概念框 PUA→引用块**（仅含 `\uf0a1` bullet 的行转 `> `）
3. **数学清洗**（PUA→希腊字母、中间点→`\cdot`）
4. **图文合成**（`^Figure X.Y` 前插 `![Fig]` 链接）
5. **围栏平衡 + 合并（兜底）**
6. **标题名加粗**（`^(Figure|Table|Listing) X.Y` → `**...**`）
7. **Exercise 引用块化**（`^Exercise N.N` → `> **Exercise N.N**` + `> body`）

## 7. P3 TOC 与全局校验（`p3_toc.py`）

1. 扫描真实标题（排除代码围栏内 `#`）
2. 注入唯一 HTML 锚点（重复标题加后缀保证唯一）
3. 生成书级 `#` 标题 + `## Table of Contents`（按逻辑阅读顺序）
4. 重复 Chapter（附录练习重列）加 `(Appendix)` 后缀
5. 运行 §3 断言

**最终产物**：141 图片链接、127 Figure 标题、833 代码块、216 概念框引用块；§3 断言全过。

## 8. 图内文字删除（`figure_text_detect.py` + `clean_figure_text.py`）

**问题**：PyMuPDF 把图内文字（流程图标签、概念框标注、坐标轴标签）当普通文本提取，已"烧录"进 P0 渲染的 PNG，留在正文里重复且污染。

**两级处理**：
- **P1 几何法**：文本块与图区域相交 → 删除；护栏保留正文（字号>13pt、完整句子、术语表短语）。
- **P5 导出后清理**：以 `**Figure X.Y**` 标题为中心 ±40 行窗口扫描，双因子（几何确认 + 内容确认）删除残留标签。迭代至收敛。

**效果**：P1 删除 1149 块；P5 删除 11 行残留；零误删正文/代码。

> 详细方案（含 Fig 2.13 token 数组根因分析、`_is_label_candidate` 护栏设计）：[attachments/figure-text-removal.md](attachments/figure-text-removal.md)

## 9. 标题级别修复（`fix_structure.py`）

**问题**：P1 仅识别 `Chapter N` / `N.N Title` 模式，遗漏无编号小节、三级子节、练习、索引子标题。

**方案**：以 PDF TOC（193 条）为 ground truth，NFKC 归一化匹配 MD 独立行，"下一行为空"启发式排除交叉引用。

**效果**：修复 **72 个缺失标题** + 1 合并行拆分；幂等；TOC 覆盖率 164/166。

> 详细方案（含关键函数、迭代过程）：[attachments/heading-and-chapter-fixes.md](attachments/heading-and-chapter-fixes.md)

## 10. 跨 block 段落拆分修复（已整合进 `p1_text_stream.py`）

**问题**：P1 原本只在单个 block 内合并行，跨 block 段落/标题被拆分。

**修复**：缓冲 `pending_prose` 合并句子跨块拆行（~121 处）；`_is_two_line_heading` 合并两行章节标题；`SUMMARY_RE` 识别 Summary 标题。

> 详细方案（含护栏设计）：[attachments/paragraph-and-code-split.md](attachments/paragraph-and-code-split.md)

## 11. 章节标题与"本章涵盖"修复（`fix_structure.py`）

**问题**：章首页大号斜体标题被 P1 拆为多 block 碎片；"This chapter covers" 的 Wingdings bullet 列表几乎全部丢失。

**方案**：从 PDF TOC 取正确标题、Wingdings 字体检测 bullet 起始块合并续行、从 PDF 提取完整 bullet 列表替换碎片。

**效果**：7 个章标题修复（`## 1` 至 `## 7`）；7 个"本章涵盖"区域替换为完整 bullet 列表（共 34 条，格式为 `**This chapter covers**` + `- ` 列表）。

> 详细方案：[attachments/heading-and-chapter-fixes.md](attachments/heading-and-chapter-fixes.md)

## 12. 异形文本块修复（`fix_special_blocks.py`）

**问题**：Note/Exercise/Listing/Table 等 callout 框与正文混同，Listing 标题被图内文字误删。

**方案**：按颜色+尺寸+字体+位置识别 6 类异形块（信息 callout 32、Exercise 25、NOTE 29、Listing 67、Table 1、This chapter covers 7），分别重建为引用块/粗体标题/markdown 表格。

> 详细方案（含 PDF 分类表、审计数据、V1-V6 验证）：[attachments/special-blocks.md](attachments/special-blocks.md)

## 13. 已知遗留（非阻断）

- **Fig 7.8（p236）曾缺失于 MD，已修复**：其图注标签 "Figure 7.8" 用 `FranklinGothic-Demi`（即 `chapter_label` 页眉字体）设置，且位于底部边距（行 `y1≈591.7 > 页高-75`），被 §5 的 span 级页脚剔除误判为页脚 span 而整行删除，导致该块文本只剩 "The padding…"、不再以 "Figure 7.8" 起头，既无法被判为 `figure_caption`，`insert_figures` 的 `^Figure (\d+\.\d+)` 也不命中，MD 缺 `![Fig 7.8]`、破坏 PDF↔MD 一一对应。已在 `pipeline/extract.py` 对图注块（`_FIGURE_CAPTION_RE` 起头的块）整体豁免页眉/页脚剔除（逐 span 也保留），修复后 MD 图链 **128↔PDF 128** 完全一一对应。`_FIGURE_CAPTION_RE` 与 `pipeline/classify.py` 共用同一 `^Figure X.Y` 结构模式，未硬码页号/坐标。
- **页边注（side notes）曾 86% 丢失于 MD，已修复**：全书 `HumanistMann` 字体的右侧页边注共 522 句，原 `figure_text_detect.is_figure_text` 的 `HumanistMann` 邻近守卫（距图元 <30pt 即判为图注标签丢弃）把紧邻图的右侧边注（如 p24 "Algorithms that learn rules…"，x0≈377 仅比图 union.x1=373 右移 4pt）误删。已在 `is_figure_text` 增加 `page_width` 参数，并在 `HumanistMann` 分支对**右侧页边栏**（`bbox.x0 > page_width*0.62`）块豁免丢弃（真实图内标签不在此列，HIGH 相交规则不受影响，无图内文字回漏；Fig 2.13 token 数组仍正确丢弃）。修复后 MD 边注缺失由 449/522(86%) 降至 23/522(4%)；MD 由 11409→12359 行。`format_scan.py`+`format_audit.py` 为差分审计工具（Phase 0/1）。

- §5.4 和 §7.4 整节内容在 P1 提取中缺失（PDF 提取遗漏，需人工补录或重新提取）。
- 正文上标在 P1 直提中已平化，未做 LaTeX 还原。
- 重复 Chapter 标题（附录练习）TOC 以 `(Appendix)` 区分，但正文标题文本相同。
- 个别矢量图 PNG 在图注上方多截了相邻正文代码片段（Fig 3.12 p84、Fig 6.5 p195）：该代码已在正文流中以围栏块出现，图中重复属可接受冗余。`build_figure_region` 的 code relay 分支按"图形绘制矩形/标签 + 空白走廊"生长图区域；正文代码紧邻图上方且与图形绘制间距极小（如 3.12 代码距流程图仅 ~4pt），与纯文本图（Fig 7.7 的示例代码即属图内内容）无法靠局部几何可靠区分，故保留现状。曾尝试"代码须与图形绘制矩形纵向重叠才中继纳入"的约束，虽修掉 3.12/6.5 过度包含，却把 7.7 的示例代码（延伸到绘制矩形之外）截断，因得不偿失而回退。

## 14. 代码清单跨 block 合并（已整合进 `p1_text_stream.py`）

**问题**：一个逻辑代码清单被 PyMuPDF 切成多个独立 block，原实现逐 block 输出围栏导致代码块碎裂。

**方案**：基于 PDF 物理布局的连续代码 run 合并（`pending_code` 缓冲），图片/绘图 block 不 flush 代码。

**效果**：fenced block 828 → 605（理想 648）。

> 详细方案（含护栏、验证示例）：[attachments/paragraph-and-code-split.md](attachments/paragraph-and-code-split.md)

## 附件索引

| 附件 | 对应章节 | 内容 |
|------|----------|------|
| `attachments/figure-text-removal.md` | §8 | P1 几何法详细规则、Fig 2.13 根因分析、P5 清理设计 |
| `attachments/heading-and-chapter-fixes.md` | §9, §11 | 标题级别修复方案、章节标题与 bullet 列表修复方案 |
| `attachments/paragraph-and-code-split.md` | §10, §14 | 跨 block 段落拆分修复、代码清单合并修复 |
| `attachments/special-blocks.md` | §12 | 异形块分类表、审计数据、处理规则、V1-V6 验证 |
