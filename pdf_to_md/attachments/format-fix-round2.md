# 第二轮格式修复子方案（审计驱动）

> 依据 `audit_md_quality.py` 全面审计结果，对 `llms-from-scratch.md` 的残留格式问题做专项修复。
> 每个缺陷一个独立脚本，按顺序执行，全部幂等。修完后重跑审计回归。

## 执行顺序

| 脚本 | 缺陷 | 状态 |
|------|------|------|
| `fix_missing_sections.py` | §5.4/§7.4 标题缺失、残段、丢失代码块 | 子方案 3 |
| `fix_structure.py` | 伪标题、损坏标题、输出块漏围栏、封底垃圾 | 子方案 1 |
| `fix_index.py` | 索引区多栏合并错乱 | 子方案 2 |
| `render_appendix_figures.py` | 附录 E 的 Figure E.1–E.5 未提取 | 子方案 2 附属 |
| `fix_structure.py` | 标题/加粗/符号一致性 | 子方案 4 |
| `rebuild_toc.py` | TOC 重建与锚点重注入（最后执行） | 子方案 5 |

## 子方案 1：伪标题与损坏标题（`fix_structure.py`）

**根因**：
- 含内联 Courier 片段的引用块/输出块被 P1 误切，清理后碎片被误判为标题。
- 封底/版权区营销文本进入标题扫描。

**修复项**（全部按内容特征匹配，无行号硬编码）：
1. `### to to take advantage of this chip.` + 前导锚点 + 截断引用块 →
   按物理页 304 原文重建完整 `> **PyTorch on macOS**` 引用块（含两段行内代码）。
2. `### .bz/EZJR appendix C Exercise solutions` → `## Appendix C Exercise solutions`。
3. `appendix B References and further reading`（普通行）→ `## Appendix B References and further reading`。
4. `### appendix D ...` → `## Appendix D ...`；附录 D/E 各节 `### D.x/E.x` 降为 `####`。
5. `### W d` 及相邻图内文字碎片（孤立 `ΔW`/`W`/`d` 行）→ 删除；
   `Figure E.1` 普通标题行 → `**Figure E.1**` 加粗并在前插图片链接。
6. `### Instruction:` / `### Input:`（围栏外 4 处）→ 整段模型输出包进 ```` ```text ```` 围栏，
   删除 P3 注入的 `<a id="instruction*">`/`<a id="input">` 锚点。
7. 封底营销区：从封面描述段（"A view of the text processing steps"）到文件末尾 → 删除
   （`M A N N I N G`、`### For print book owners...` 等）；
   liveProjects 推广页保留但 `• ` → `- `、剔除 `\x07`。
8. 重复的 Exercise 7.2 空壳引用块 → 删除（保留完整版）。

## 子方案 2：索引区重排（`fix_index.py`）

**根因**：索引页为三栏版式，P1 按行 y 坐标合并，三栏内容交叉拼接。

**方案**：物理页 359–365（0 基 358–364）重新提取：
1. 每页按 block bbox x 中点聚为 3 栏（边界约 230/385）。
2. 栏内按 y 排序；每个索引条目为"术语 + 尾部页码"，续行（无页码）并入前一条目。
3. 输出格式：`## Index` + `### Symbols/Numerics/A..Z` 分节 + 每行 `- 术语, 页码`。
4. 替换 md 中从 `index` 行到索引结尾（liveProjects 区之前）的乱码区。

**附属**：`render_appendix_figures.py` 以 `Figure E.N` caption bbox 为锚点，
向上取绘图/图片区域并集 @3x 渲染，保存 `FigE.N_pXXX.png`，在标题前插图片链接。

## 子方案 3：§5.4/§7.4 修复（`fix_missing_sections.py`）

**根因**：
- §5.4：节标题（FranklinGothic-DemiI 大字）被页眉过滤丢弃，正文实际存在。
- §7.4：开头段落所在 block 混有 Courier 行内代码 → 被误判代码块后在清理中丢失；
  `device` 初始化代码块同样丢失；Exercise 7.2 留下空壳重复项。

**方案**：
1. 在 `### 5.5` 前按锚点段落定位，插入 `### 5.4 Loading and saving model weights in PyTorch`；
   顺带合并该节内残留断词（`pre- train`）。
2. 在 "As of this writing, researchers are divided..." 前插入
   `### 7.4 Creating data loaders for an instruction dataset`。
3. 用 PDF 物理页 245 原文重建开头段，替换残留碎片
   `` `custom_collate_fn` function for the instruction dataset. As shown in figure 7.14...``。
4. 在 "The following code initializes the `device` variable:" 后补入丢失的代码块（物理页 246）。

## 子方案 4：格式一致性（`fix_structure.py`）

1. `## Chapter 1` → `## Chapter 1 Understanding large language models`，
   并删除其后重复的 `### Understanding large language models`。
2. 附录练习区第二个 `## Chapter N` → `## Chapter N (Appendix)`（与 TOC 显示一致）。
3. 前置部分标题大写化：`preface → Preface` 等 6 处。
4. `#### Exercise A.2/A.3/A.4` → `### `，消除 `## → ####` 级别跳变。
5. 附录 A/E 普通 `Listing X.Y 标题` → `**Listing X.Y** 标题`；
   删除 Listing E.1 相邻重复标题（保留加粗版并对齐章节样式）。
6. `E.2 Preparing the dataset` 从段落中拆出为 `#### E.2 ...` 标题。
7. 断词残留：`onedimensional → one-dimensional`、`shortstory → short-story`、
   `finetuning → fine-tuning`（仅散文，跳过围栏内）、`classifi cation` 类空格断词。
8. 其余 `• ` → `- `、`\x07` 控制字符清理（兜底，主战场在子方案 1）。

## 子方案 5：TOC 重建（`rebuild_toc.py`）

1. 删除旧 `# 书名` + `## Table of Contents` 区与全文所有 `<a id>` 锚点。
2. 复用 `p3_toc.py` 的 slugify/锚点唯一化逻辑重新注入锚点。
3. TOC 覆盖面扩展：前置部分（Preface 等）、Chapter/Appendix、全部编号节、
   Summary 跳过（重复 8 次，无导航价值）、附录练习章以 `(Appendix)` 后缀、
   末尾附 Index 条目。排序键支持字母附录（A–E）排在数字章之后。
4. 运行 §3 断言：围栏成对、无 PUA、TOC ⊆ 锚点。

## 验收

`python3 audit_md_quality.py` 回归：伪标题 0、损坏标题 0、TOC 缺失项 ≤ 少量豁免、
索引区可检索、围栏成对、无控制字符。
