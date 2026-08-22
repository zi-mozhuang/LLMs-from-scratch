# 跨 block 段落拆分与代码块合并修复详细方案

> 返回主方案 §10、§14

## §10 跨 block 段落/标题拆分修复（已整合进 `p1_text_stream.py`）

### 问题根因

P1 原本只在**单个 text block 内**合并物理行。当 PDF 把一个逻辑段落或章节标题拆成多个 text block（中间夹图/公式，或标题本身两行排版）时，产物里会出现"本该一行却多行"的硬换行：同句被切成两段、章节大标题被拆成两行、小结标题 `Summary` 未被识别为标题。

### 修复（均在 `p1_text_stream.py` 主转换里实现，非独立脚本）

| 类型 | 模式 | 修复 | 效果 |
|------|------|------|------|
| 句子跨块拆行 | 前段末无 `.!?` + 后段首小写 + 两段均 ≥40 字 | 缓冲 `pending_prose` 折叠为同一段（`_should_merge_prose`） | 修复 ~121 处 |
| 两行章节标题 | `Understanding large` + `language models`（两 block，均 <50 字、后首小写、前非引导动词结尾、前非 `)` 结尾） | 合并为单行 `### ` 标题（`_is_two_line_heading`） | 修复 "Understanding large language models" / "Coding attention mechanisms" 等 |
| Summary 未识别 | 独立行 `Summary` | `SUMMARY_RE` → `### Summary` | 各章小结标题统一 |

### 设计要点与护栏

- 长度下限（≥40 / <50）用于区分"句子续行/两行标题"与"章节提纲短列表项"（如 "This chapter covers" 后的 `tokenizing text` / `approach`，PDF 原生多行，保留为列表，不合并）。
- 引导动词黑名单（covers/include/chapter/derived/follows…）与 `)` 结尾排除，确保不把 "This chapter covers … (LLMs)" 引导句后的列表项误并为标题。
- 残留约 35 处"疑似多行"经核对均为 PDF 原生多行内容：参考文献/URL 断行、索引条目（Symbols/B/C/V…）、封底宣传语/Feynman 引言——非误拆，不处理。

---

## §14 代码清单被拆成多个代码块修复（`p1_text_stream.py`）

### 问题根因

PDF 文本提取中，**一个逻辑代码清单（listing）常被 PyMuPDF 切成多个独立的 text block**。例如"下载 the-verdict.txt"那段在 PDF 里是 2~3 个 Courier block。

原实现对每个代码 block 独立输出一个 fenced block，依赖 P2 的 `rebalance_fences` 在文本层合并"紧邻同语言"的块。这有两个致命缺口：

1. P1 每 block 后加空行，且连续代码 block 之间若夹了被 `drop_figure_text`/页眉剔除跳过的中性 block，或直接相邻，合并只能靠"无 prose 间隔"判断；
2. 更糟的是：一旦某个代码行因字体/字号判定偏差（如某 span 混了非 Courier 字体）被归入 prose，就会在该处插入正文行，**打断 run**，P2 无法再合并两侧的代码块。

实测产物：PDF 共 1012 个代码 block、本应组成 **648 个逻辑清单**（493 单块 + 155 个多块 run），但 P2 只合并到 **828 个 fenced block**——即多生成了约 180 个本不该存在的代码块（一页里同一清单裂成几段，每个都带 ` ``` ` 围栏）。

### 方案（在 P1 物理布局层合并）

不再逐 block 输出围栏，改为**基于 PDF 物理布局的连续代码 run 合并**：

- 新增 `pending_code: list[str] | None` 与 `pending_code_lang` 缓冲。
- 主循环遇到代码 block 时：先 `flush_pending()`（代码前不能有挂起 prose），再把该 block 的代码行 `extend` 进 `pending_code`，**不立即输出围栏**。
- 遇到**非代码 block** 时，先在分支开头调用 `flush_code()`，把累积的代码行作为**一个** fenced block 输出。判定"非代码"的只有 prose/标题块；图片/绘图-only block（无 text）不调用 `flush_code`，因此图插在代码中间不会打断 run（图内文字也已被 §8 删除，物理上不占据正文流）。
- 文档/页末统一 `flush_code()` 收尾。

合并的"连续性"由 block 遍历顺序保证：连续 Courier block 之间若只有无正文的中性 block，则仍属同一 run。`detect_code_lang` 对每个 block 调用但只取首个语言（同 run 语言一致），避免混入不同语言导致错配。

### 执行结果

- 重跑 P1→P2→P3 全管线，§3.3 断言全过（围栏成对、无 PUA、TOC⊆anchors）。
- fenced block 数：**828 → 605**（理想 648，差值 43 来自跨页 run 在页末被强制 flush 拆分，以及字体判定边界处的个别单块）。
- 验证示例：Ch2"下载 the-verdict.txt"原裂为 2 段（606-613、619-624），现合并为 1 个完整块（含 `url = (...)` 续行）；而"读取 the-verdict.txt"（Listing 2.1，中间有 "Next, we can load..." 说明 + `**Listing 2.1**` 标题）**正确地保持为独立清单**——证明只合并了真正的物理连续 run，未误并书里真实分开的多个清单。

### 护栏

- 图片/绘图-only block **不** flush 代码，故图内插入不会切断代码清单。
- 仅在进入 prose/标题块时才 flush，避免把书里真实的两个相邻清单（中间有说明文字）误并。
- 跨页代码 run 在页末 flush（代码清单不会跨页），安全。
