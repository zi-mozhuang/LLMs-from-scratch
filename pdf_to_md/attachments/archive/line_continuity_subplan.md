# 子方案：行连续问题（Line Continuity）统一处理

本子方案汇总 PDF→Markdown 转换中所有"本应属于同一行/同一段落，却被拆成多行或多段"的问题，并提供一个统一脚本 `fix_line_continuity.py` 作为流水线的最终兜底。

## 1. 问题定义

"行连续问题"指：PDF 中一个逻辑单元（句子、行内代码词、标题、列表项、引用块、连字符单词）在 Markdown 输出里被切成了**两段或更多段**（中间出现空行），破坏阅读连贯性。

它与"图内文字泄漏"（另一子方案）正交：图内文字是**不该出现**的文本；行连续问题是**该连在一起却分开**的合法文本。

## 2. 问题分类与根因

| 编号 | 类型 | PDF 根因 | 是否应合并 |
|------|------|----------|-----------|
| A | 同一 text block 内多物理行 | 块含多 `line` | 合并为一段（正确） |
| B | 跨 block 正文续接 | 一句被切成多 block | 合并 |
| C | 行内代码跨 block | `` `create_` `` / `` `dataloader_v1` `` | 合并 |
| D | 连接词续接 | 句末 `with`/`such as`/`the` 后接续句 | 合并 |
| E | 连字符断词 | `pre- viously` | 去连字符合并 |
| F | 章节标题两行缝合 | `2.4` / `Title` 跨 block | 缝合为 `### 2.4 Title` |
| G | 图注保护 | `Figure X.Y` 必须独立行 | **不合并**（供 P2 插图） |
| H | 引用块续接 | `> text` + 空行 + 续行 | **不处理**（本书 concept box 内行独立，合并会破坏排版） |
| I | 破折号列表项续接 | `- bullet` + 空行 + 续行 | **不处理**（本书无此拆行；保留函数但 0 改动） |

## 3. 当前处理分布（改进前）

| 类型 | 处理位置 | 状态 |
|------|----------|------|
| A | P1 `merge_paragraphs=True` 的块内 join | ✓ 已覆盖 |
| B/C/D/F/G | P1 `_should_merge_prose` + `_is_two_line_heading` | ✓ 已覆盖（本轮增强） |
| E | P2 `dehyphenate`（`COMPOUND_PREFIXES`） | △ 部分：`pre` 等同前缀误保留，漏 `pre-viously`（由子脚本精确字典补） |
| H/I | `merge_paragraphs.py`（**独立工具，未接入流水线**） | ✗ 本书不适用（见 §4），已废弃 |
| 行内代码断词兜底 | 无（P1 合并但保留双反引号） | ✗ 由子脚本 `fix_inline_code_breaks` 补 |

## 4. 统一脚本 `fix_line_continuity.py`

作为流水线 P5 之后的**最终后处理兜底**，仅修复 P1/P2 源头**漏掉的两个边角**（经全量验证，其余类型已由 P1 源头覆盖或本书不存在）：

1. **行内代码断词兜底（C'）**：相邻两个行内代码 `` `word-` `` + `` `rest)` `` → `` `wordrest` ``（如 `` `Multi-` `` + `` `HeadAttention` `` → `` `MultiHeadAttention` ``，`` `torch.arange(con-` `` + `` `text_length)` `` → `` `torch.arange(context_length)` ``）。代码块内行受 `code_fence_mask` 保护，绝不改动。
2. **精确连字符断词（E）**：用**已知断词字典**（`KNOWN_BREAKS`，如 `pre- viously`→`previously`）修复 P2 的 `COMPOUND_PREFIXES` 漏；**不**做全局 `(\w+)- (\w+)` join，以避免把短语（如 `in- progress`）误合成 `inprogress`。

**经实测移除的两类（本书不适用/有害）**：
- **H. blockquote 续接**：本书 concept box 被 P2 转成 `> ` 行，box 内每个视觉行独立分隔——跨空行合并会破坏 box 排版，故不处理。
- **P. prose 跨段兜底**：P1 已从源头合并所有正文跨 block 拆行；子脚本若再做"行+空行+续行"合并，会**误并参考文献/数据集列表**（`– ` 条目、URL 行、引用块），破坏结构。故不处理。

强制保护：代码栅栏（```）、图注（`Figure X.Y`）、标题（`#`/`###`）、`![Fig]` 链接行、表格（`|`）、`>`/`-`/`–`/`*` 开头的列表/引用/参考文献行——永不参与合并。

幂等性：脚本可重复运行，输出稳定。

## 5. 已确认遗漏与改进（经实测）

### 5.1 `pre-viously` 类（E 的 COMPOUND 误判）
- 原：P2 把 `pre` 列入 `COMPOUND_PREFIXES` 保留连字符，但 `previously` 是断词。
- 改：子脚本用**精确断词字典** `KNOWN_BREAKS`（`pre- viously`→`previously` 等），**不做全局 join**——避免把短语（如 `in- progress`）误合成 `inprogress`。

### 5.2 行内代码跨 block 保留双反引号（C 的边角）
- 原：P1 合并 `` `torch.arange(con-` `` 和 `` `text_length)` `` 但保留两对反引号。
- 改：子脚本 `fix_inline_code_breaks` 合并为单一反引号词 `` `torch.arange(context_length)` ``。代码块内行受 `code_fence_mask` 保护。

### 5.3 实测移除的 H/P 类（重要教训）
- 初版子脚本含 `fix_blockquotes`（H）和 `fix_prose_splits`（P），dry-run/apply 后发现**严重误改**：
  - H 把 concept box（`> ` 行）内独立视觉行跨空行合并，破坏 box 排版；
  - P 把参考文献/数据集列表（`– ` 条目、URL 行、引用块 `> `）跨空行合并，破坏书目结构（如把 "LIMA..." 标题行与 arxiv URL 行合成一行）。
- 结论：H/P 在本书**不适用/有害**，P1 已从源头覆盖正文拆行，子脚本不应重复。已删除这两个函数。

## 6. 仍可能遗漏的边界（已知限制，记录在案）

1. **多语言/数学公式**：行间公式是 PNG 渲染，不存在文本拆行；但公式旁 `$$...$$` 行内若被切，当前不处理（书中少见）。
2. **跨页 split**：若一句恰在页末/页首断开成两个 block（跨页），P1 已合并（续行逻辑覆盖）；极端情况续行是大写专有名词且不以连接词结尾可能漏——需人工核对。
3. **三段以上拆分**：当前修复处理"行内"断词（非跨空行合并），若正文被插图/空行隔成多段，因语义应分隔，不算漏。
4. **KNOWN_BREAKS 覆盖度**：`pre- viously` 等是手工列举；若 PDF 源有其他少见的断词漏（非 `pre/post` 前缀），需补入字典。可后续用"join 后词在英文词表"的启发式扩展，但需防 `inprogress` 类误合。
5. **inline-code 误合风险**：`fix_inline_code_breaks` 仅匹配 `` `x-` `y` `` 形式（连字符断在反引号内）；若两个本应独立的行内代码恰好满足该模式（极少见），会误合。当前全书验证无此情况。

## 7. 流水线接入

```
pdf_text_stream.py  (P1: A/B/C/D/F/G 源头合并)
pdf_clean_p2.py     (P2: E dehyphenate + 插图 + PUA)
pdf_toc_p3.py       (P3: TOC/anchor)
clean_figure_text.py(P5: 图内文字删除)
fix_line_continuity.py  (最终兜底: C' 行内代码断词 + E 精确连字符)   <-- 新增
```

运行：`python fix_line_continuity.py --apply`；默认 dry-run 预览。

> 注：旧 `merge_paragraphs.py` 已被本脚本取代并删除——其 `fix_blockquote_continuations` /
> `fix_dash_bullet_continuations` 在本书会产生误改（见 §5.3），且从未接入流水线。
