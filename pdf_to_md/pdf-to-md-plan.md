# PDF 图片提取方案（v2，全新开始）

> 目标 PDF：`pdf_to_md/Build a Large Language Model (From Scratch) (Sebastian Raschka).pdf`（370 页）
> 脚本：`pdf_to_md/extract_images.py`
> 输出：`pdf_to_md/extracted_images/{embedded,figures}/`
> 状态：旧方案（pymupdf4llm + fix_code_blocks.py 补丁链）效果不佳，已弃用。

## 1. PDF 结构分析结论（方案依据）

对 PDF 实测得到三个关键事实，直接决定提取策略：

| 事实 | 数据 | 结论 |
|---|---|---|
| 内嵌位图极少 | 全书仅 **33 个唯一 xref** 位图（封面、作者照、章节图标、少量图） | 直接按 xref 提取原始字节，无损、零失真 |
| 正文图表是矢量绘图 | 书中 **143 个 `Figure X.Y` 标题**，对应页面是 `get_drawings()` 矢量路径（如 p30 有 55 条路径），不是位图 | 无法"抽取"，只能**定位区域后高分辨率渲染** |
| 版式规律固定 | 图在上方、`Figure X.Y ...` 标题紧贴其下方（实测 p25/p30 等页验证） | 可用标题 bbox 反推图的区域：标题上边界以上的绘图簇即为图 |

## 2. 提取策略（两通道）

### 通道 A：内嵌位图（embedded）
- 遍历所有页 `get_page_images(full=True)`，按 xref 去重。
- `doc.extract_image(xref)` 取原始字节（保持原格式 png/jpeg，不重编码）。
- 特殊处理：
  - **SMask**（透明蒙版，如 p368 xref13567）：用 `Pixmap(pix, mask)` 合成 alpha。
  - **CMYK**（封面 xref14214）：转 RGB 后保存。
- 命名：`embedded/p{页码}_xref{xref}.{ext}`

### 通道 B：矢量图表（figures）
对每个 `Figure X.Y` 标题：
1. 用文本块定位标题 bbox（正则 `^Figure \d+\.\d+`）。
2. 收集本页所有矢量路径 rect + 图片对象 rect。
3. 取纵向上位于「上一标题下界（或页顶）」与「本标题上界」之间的全部元素，按邻近度聚类（间隙 < 15pt 合并），取并集得图形区域。
4. 区域四周加 6pt padding，`get_pixmap(clip=..., matrix=Matrix(3,3))` 以 **216 DPI** 渲染为 PNG。
5. 命名：`figures/Fig{X.Y}_p{页码}.png`

### 为什么这样设计（而非其他方案）
- **为什么不用 pymupdf4llm 直接出图**：旧方案即此路线，碎片化严重（一张图被拆成多个片段），需要大量事后修补，不可维护。
- **为什么不整页截图**：会把正文文字、页眉页码混进图片，Markdown 里图文冗余且无法检索。
- **为什么渲染 3x 而非提取原始路径**：SVG/路径数据无法在 Markdown 中展示；3x（216 DPI）屏幕与打印均清晰，文件体积可控。
- **为什么以标题为锚点**：143 个标题是全书图表的权威清单，天然提供 (a) 完备性校验基准 (b) 精确的区域分割线。

## 3. 校验（提取完成后自动执行）

| # | 校验项 | 通过标准 |
|---|---|---|
| V1 | 位图完整性 | 提取数 == 唯一 xref 数(33)，全部可被 PIL 打开，尺寸与 PDF 元数据一致 |
| V2 | 图表覆盖率 | 143 个标题逐一配对一个 PNG 文件，无遗漏、无重复 |
| V3 | 内容有效性 | 每张渲染图像素标准差 > 阈值（排除空白/纯色页），最小边 ≥ 100px |
| V4 | 人工抽检 | 主模型直接读取若干渲染图，目视确认图形完整（不缺边、不含正文） |

校验结果输出到 `extracted_images/manifest.json` 与控制台报告；任何一项失败则列出失败明细。

## 4. 执行步骤

```bash
cd pdf_to_md
python3 extract_images.py            # 提取 + 自动校验(V1-V3)
# V4 人工抽检由 AI 完成：读取代表性图片核验
```

## 5. 后续（图片就绪后的 Markdown 转换，另行实施）

1. 按 PyMuPDF 提取文本流，代码块按等宽字体块还原（复用旧管线中可靠部分需重写，不 import）。
2. 在每个 `Figure X.Y` 标题前插入 `![Fig X.Y](extracted_images/figures/FigX.Y_pNNN.png)`。
3. 表格/公式单独处理。
