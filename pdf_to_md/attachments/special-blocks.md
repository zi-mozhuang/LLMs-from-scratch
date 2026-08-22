# 异形文本块修复子方案（`fix_special_blocks.py`）

> 返回主方案 §12

覆盖"Note/概率解释/Exercise/Listing"等与正文格式不同的对象，确保 md 中有可视觉区分的格式（引用块/加粗标题），且无遗漏、无多加。

## 范围与动因

本书中大量知识点以**带底色 callout 框**承载（浅黄 `(0.969,0.961,0.91)`、蓝头 Listing、灰表头等），字体与正文不同：
FranklinGothic-Demi 10.5（标题）/ FranklinGothic-Book 9.5（正文），与正文 NewBaskerville-Roman 10pt 显著区分。
P1 直提按 block 合并且 P2 仅处理 `\uf0a1` bullet，导致**段落式 callout（无 bullet）整体与正文合并为普通段落**，
Listing 白字蓝底条被 `figure_text_detect` 误判为图内文字而整条丢失（67 处）。

## PDF 异形块分类与判定信号

| 类型 | PDF 信号 | 数量 | 目标 md 格式 | 示例标题 |
|------|----------|------|--------------|----------|
| A. 信息 callout（概率/概念解释） | 填充 `(0.969,0.961,0.91)` 且 `w≥100 h≥40` + 标题 `FranklinGothic-Demi 10.5` + 正文 `Book 9.5`，bbox 与填色 rect 重叠 ≥20% | 32 | `> **Title**` + `> body`（每段前 `> `，段间 `>` 空行） | `The "self" in self-attention`、`Cross entropy loss`、`Perplexity`、`Biased variance` |
| B. Exercise | 同上，且标题 `^Exercise` | 25 | 同上（`> **Exercise X.Y Title**`） | `Exercise 2.1` … `Exercise 7.4` |
| C. This chapter covers | 同上 + 标题 `This chapter covers` + Wingdings bullet | 7 | `**This chapter covers**`（已在 §11 修复为粗体引导语，无冒号）+ 普通 `-` 列表（单行 `- `，无拆行） | — |
| D. NOTE admonition | 首 span `NOTE` `FranklinGothic-Demi 8.5` #4680581 + 后续 `Roman 10`，无填色 | 29 | `> **NOTE** body`（全段 `> `，与 `Note that` 区分） | `NOTE Most LLMs today…`（L395） |
| E. Listing 标题 | 填充 `(0.438,0.652,0.801)` + 白字 `Demi 9` + 文本 `^Listing \d+\.\d+` | 67 | `**Listing X.Y Title**`（单独粗体行，位于代码围栏前） | `Listing 2.1 Reading…` |
| F. Table 1.1 数据表 | 4 列网格（`Dataset name`/`Number of tokens`/`Proportion` 等 `FranklinGothic 8pt`，`y 73-171 P33`，11 画线网格） | 1（5 行+表头） | `| Dataset name | … |` markdown 表格（`L458`） | `CommonCrawl (filtered) | Web crawl data | 410 billion | 60%` |

判定全部基于**颜色+尺寸+字体+位置重叠**，无硬码字符串/页号。

## 现状审计（修复前）

- 57 个带标题 callout 框（不含 7 个 chapter covers；32 信息 +25 Exercise，另有 5 个续页无标题体作为同一框续段）中：**0 个为引用块**，44 个标题与正文合并为普通段落（标题与首句同行），13 个标题整行丢失（仅存 body 融入正文，含跨页续段）。
- 29 个 `NOTE` admonition（`NOTE` Demi 8.5）：**0 个为引用块**，全部以 `NOTE ` 普通段落与正文混同（L395 等）。
- 67 个 Listing 标题：**0 个**在 md 中存在（被图内文字几何删除）。
- 1 个数据表 `Table 1.1`（5 行+表头，`P33 y73-171`）：**0 行**在 md 中存在（`Dataset name`/`CommonCrawl` 等 `FranklinGothic 8pt` 网格文字被 `figure_text_detect` HIGH 误判为图内而整表丢失，`L458` 仅剩 `**Table 1.1**` 标题）。
- Figure/Table 标题：179/3，已正确加粗；无需改动。
- 行内 `Note that…`（约 40 处，新罗马字体无底色）保持正文，未误判。

## 处理规则

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
   - `This chapter covers` bullet 保持 `- ` 单行，禁 `> ` 拆行。
   - 修复后迭代至收敛，`figure_text_detect` 新增护栏：`^Listing` 白字块、`FranklinGothic-Book/Demi 9-11` callout、`NOTE` Demi 8.5、`Table` 关键字永不判为图内（防回归）。

## 实现与验证

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
