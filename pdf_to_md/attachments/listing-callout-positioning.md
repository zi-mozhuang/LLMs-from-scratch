# Listing 旁注行级定位归位（2026-08）

对应 pdf-to-md-plan.md「Listing 旁注」条目。前置：[listing-callouts.md](listing-callouts.md)
（2025-08 尾插方案）。本次升级动机：尾插丢失"指向哪一行"的语义，且排查
发现旁注在 extract 阶段被整条误删。

## 排查结论（改造前遗漏面）

| 缺陷 | 根因 | 实测 |
|------|------|------|
| 旁注短语全书蒸发 | 无 manifest clip 页走启发式图区检测，**旁注箭头（黑三角+细肘线）被当作图形要素长成 fig_rects**，邻近旁注文本被 `is_figure_text` 判图内文字删除——extract 阶段即丢，verify 第 9 步只对账已标记块故不可见 | p48 Listing 2.3 八块旁注五块被删（"Processes input text into token IDs" 等三条短语消失） |
| 幸存者错位 | merge 一律折叠追加围栏尾部 | 三条幸存注全堆 fence 尾 |
| 折行短语碎片化 | Manning 把一条注拆多个 PDF 块；跨栏混排块（p222："sequence"+"Adds batch/dimension" 并进一块且行序颠倒） | "original text tokens" 孤尾入注 |

## 修复结构链

1. **figure_text_detect 种子剔除**：`callout_marker_rects` 判定旁注箭头
   （黑填充 ∧ 小三角 ≤12×12 ∨ 细线厚 ≤2.5 长 ≤70 ∧ 距 annot 字体 span
   ≤60pt ∧ 中心距 Courier 行 ±15pt）；`listing_bar_rects` 把 Listing 标题
   色条一并剔出种子（其 <30pt 近邻规则曾杀条上方旁注）。真图形内细线
   不邻近 annot 文本，零扰动；manifest clip 权威页不走此路径。
2. **classify 行级单元分组**：annot 块拆行单元（治跨栏混排块），单元按
   (y0,x0) 排序后多开放组链接——同列（x 重叠 ≥50% 较窄者）+ 纵向相邻
   ≤12pt + **前单元未收句守卫**（p99 实测两条独立注 gap 仅 9.6pt，真折行
   ≤1pt 且无句末标点）。组 ↔ 三角箭头簇贪心最近唯一配对（间隙欧氏距离
   ≤70pt/纵向 ≤40pt）；成员资格 = 箭头中心落进某 code block ±15pt。
   产出 `meta["callout_lines"]={gid:[(行文本,箭头cy),...]}`（一块可向
   多组贡献行）。
3. **merge 行级定位插入**：组文本按目标行插入——owner 代码块的
   `meta["lines"]` 行 cy 与箭头 cy 最近匹配，注释插目标行上方、缩进随
   目标行。归属两级：流序（≤6 块且**同页**）/ 几何兜底（同页 y-overlap>5，
   否则间隙 ≤60pt）。定位失败回退尾插。
4. **审计**：`audit_listing_callouts.py`（离线）PDF 组短语 ↔ MD 围栏
   整行精确对账，MISSING/OUTSIDE/MULTI/SUBSTR 分诊。

## 结果

- **240/240 组**全部整行精确归位（围栏内）；MISSING=0、OUTSIDE=0、MULTI=0。
- 位置分布（终态）：224 同行(≤8pt) + 10 相邻行(9–15pt) + 6 尾插兜底；
  行内错位(>15pt)=0；缩进异常=0。
- verify 第 9 步升级为行文本快照对账（行级单元 ↔ 0 未归位）。
- Listing 2.3 五条注全部按书版箭头落点插入（含两条曾蒸发的短语）。

## 位置正确性专项校验（2026-08 第二轮审计·终态）

对全部 234 组做「MD 注释下邻行 ↔ PDF 行几何距离」独立校验
（从 MD 渲染结果反推，不经 merge 内部状态）：

| 偏差带 | 组数 | 判定 |
|--------|------|------|
| ≤8pt（同行） | **224** | 正确落位（95.7%） |
| 9–15pt（相邻行） | 10 | 三角对齐行顶/底所致，语义等价 |
| 尾插兜底 | 6 | fence 末尾保留，目标行跨候选不可达的边缘组 |

**>15pt 行内错位：0**。缩进行内插入异常：0（2 条初报为校验脚本把
围栏闭合行当下邻的假阳性；PDF 原生缩进完好）。

**第三轮审计补漏（用户指出 L2607 'Combines heads…' 散段）——检查方案
的三处盲区与修复**：
1. audit 对账样本空间只含"已标记组"，未标记组仅走 STRAY 分诊；
2. STRAY 判据是"相对修复前基线零新增"——历史存量散段天然通过，
   "没有变坏"被误当"没有问题"；
3. 三级成员资格对"注在清单正下方"形态双双失效：配对 dy≤40 容不下
   尾注形态，重叠 ≥50% 判据在组位于 code 下方时恒为负。
修复：新增第四级成员资格「清单尾贴邻」——组紧贴某 code block 下缘
（间隙 ≤15pt）且横向与代码列重叠 ≥50% 组宽，target_y 取清单末行。
收编 6 组（p108:t1/t7、p145:t4、p186:t1、p222:t6、p340:t0），逐组
语义核验全为真尾注，零误收。教训：STRAY 存量必须按"贴邻代码的可
收编 / 真边注"二分甄别，不能以"存量"为由豁免。

**归属机制（最终形态）——候选链逐个尝试**：
tier1 流序贴近门给首选；同页域全部 code 按「重叠降序、间隙升序」构成
有序候选链；插入阶段以「页级行索引中箭头 cy 最近行文本」逐候选试挂，
命中即插（缩进随目标行），全链未命中才尾插兜底。该设计使清单被 PDF
拆块、注群悬空、跨页拼接等形态下目标行仍可达。
配套修复：pending_code 对 callout 块透明占位（不打断合并）并同步拼接
raw_lines/lines/page_span；pending_prose 与 two_line_headings 跳过
listing_callout/annot 块（防吞块丢组元数据）。

**实验记录**：曾两度尝试「定位延后到 pending_code 合并后」的时序方案，
均因块流消费时序与元数据一致性问题回归废弃；最终采用"时序不动 + 候选
链容错"，以最小改动达成同等效果。

**遗留（5 组尾插）**：p108:x6 / p136:0 / p198:1 / p245:0 / p296:x1 ——
目标行为重建补丁区/悬空注群边缘，几何锚定缺失，fence 尾忠实保留。

## 全面排查结论（2026-08 审计，PDF↔MD 双向）

| 审计项 | 方法 | 结论 |
|--------|------|------|
| 图内文字回流 | 逐页 exclude 前后双种子 `is_figure_text` 差异 | 回流 77 块 = 67 annot 短语 + 10 Courier（7 条属 clip 页假阳性；p132 `loss.backward()`、p143 输出行 3 条为修复性回流，落点正确）；反流 0 |
| 定位正确性 | audit 整行精确对账 ×2 轮 | 216→234 组全归位，零散段零重复 |
| 配对正确性 | p132 案例诊断 | 纯欧氏贪心被横跨块抢占 → 排序键改 `(dy, dist)` 纵向包含优先，七组全对 |
| 收编完备性 | 未标记组几何资格（与 code 重叠 ≥50% 组高） | 13 组真旁注收编；历史边注/图标签零越界 |
| 前后净 diff | old_rebuild 截断基准 vs 现版多重集合差 | 净新增=195 注释+回流代码/段落行（全部 PDF 已有）；净删除=旧尾插注释+散段+围栏合并符；无真丢失 |
| 引用块空行→`>` ×38 | 抽查定位 | 符合 plan §4"空行用 `>` 保持盒子连续"不变量 |
| 全局对账 | verify 十一项 / check_line_integrity / audit_pdf_md 法1 | ALL PASS / strict 0 / 丢失 22→17（净恢复 8 条），新增 21 条均为注释↔caption 分段口径噪声 |
| 确定性 | 双渲染哈希 | 一致 |

## 已知遗留（非阻断）

- p108 左栏孤 fragment（"we unroll the"、"head_dim)."）：配对文本未被
  PyMuPDF 提取为 annot 块（疑烘进图或被页眉过滤），按现状忠实入注。
- 同文旁注合法重复（如 "Adds batch dimension" 两处）由审计的 PDF 组数
  豁免逻辑放行。
- 历史 STRAY 散段 116 组（图标签/无箭头边注）维持既有形态，
  本轮零新增（before/after 对比验证）。

## 图链锚定错位修复（2026-08 第三轮，L3219 型）

**现象**：Fig 4.12 图链被拉到正文引用段 "Figure 4.12 shows…" 之前，
真图注孤悬段后；全书同型错插 9 处（Fig 3.23/3.24/4.12/4.13/4.16/5.4/
6.5/6.16/6.17）。

**三层根因**：
1. classify `FIGURE_CAPTION_RE = ^Figure X.Y` 无内容判别——以图号开头的
   **正文引用句**被误判为 figure_caption 块（旧链即有）；
2. render 行级锚定 `FIG_RE` 同歧义——斜体真图注 `*Figure X.Y*` 反不匹配；
3. md_lint R7 只查图文数量与距离，不查"图链下邻是否图注"→ 漏报。

**修复（三层同治）**：
- classify：新增 `FIGURE_PROSE_REF_RE` 正文引用句排除
  （shows/illustrates/plots/graphs/depicts/summarizes/displays/presents/
  outlines/demonstrates/compares/visualizes/captures 黑名单，真图注恒为
  名词短语开头）；
- render：图链插入改为 **figure_caption 块级合成**（caption 块即精确
  锚点），行级 pass 降为缺口兜底（斜体形态 + 动词黑名单）；
- md_lint：新增 R12「图链跳空行后首非空行必须是斜体图注」。

**结果**：133 条图链下邻图注贴合率 100%（128 紧邻 + 5 附录 E 隔空行
的 patches 通道形态）；R12 上线零违例。

## 行内代码碎片合并（2026-08 第四轮，L4885 型）

**现象**：PDF 把 Courier 引文按词/符号切 span，逐 span 包反引号产生
逐词碎片——`"Every` `effort` `moves"`、`context_vec` `=` `attn_weights`、
`[2,` `4,` `50257]`、断词残段 `num_` `tokens`。全书 48 行受影响。

**修复**：render 新增 `merge_fragmented_inline_code(lines, mask)` pass
（步骤 6.4，fix_inline_code_breaks 之后）：相邻 `` `a` `b` `` 片段迭代
合并至不动点（整链合一为 `` `a b c` ``）；围栏内跳过；引用块剥前缀
处理后还原。被 and/逗号等分隔的独立引用不受影响。

**结果**：≥3 连续片段链全书清零；抽查语义正确
（`context_vec = attn_weights @ values`、`[2, 4, 50257]` 等）。

## 清单断裂修复（2026-08 第五轮，L5106 型 leader 仲裁）

**现象**：Listing 5.5 被拆成 ```python / ```text 两围栏，中间夹未收编
旁注散段 'Iterates over each transformer block…'，尾块语言误判。

**根因**：该注的箭头是「三角 + 长竖线 leader」形态——三角在代码区
（y340/350）指向目标行，竖线（高 258/232pt）向下连到页底注文本。
配对器 dy≤40 容不下此形态（dy≈240）；三/四级资格也因组位于 code 尾
下方 37pt 失效 → 未标记 → 打断 pending_code 合并。全书同形态共 4 组
（p59×3 + p186×1），其中 p59 三条正是被"存量豁免"误放过的历史 STRAY。

**修复（第五级成员资格：leader 仲裁，仅兜底前四级失败组）**：
黑色细长竖线（w≤3, h≥60）底端触组（容差 [-5,+12]，横向远离排除）+
顶端邻三角簇（|cx-L.x|≤65, |cy-L.y0|≤12，簇未被占用）→ 直接配对，
target_y = 三角 cy；membership gate（箭头指入 code ±15pt）保留。
首次实现因 leader 先占坑破坏常规配对而回归（240→169），改为兜底顺序
后收敛——教训：强证据仲裁必须后置为兜底，不得抢占常规配对。

**结果**：
- **247/247 组**整行归位；位置分布 230 同行(≤8pt) + 11 相邻行 + 6 尾插；
  >15pt 行内错位 = 0
- Listing 5.5 合一为单个 ```python 围栏；p59 三条历史 STRAY 落位语义
  精准（for i in range(...) / def __len__ / DataLoader 参数区）
- STRAY 存量 116→110

## 概念框内无格线表重建（2026-08 第六轮，L7175 型）

**现象**：ch7 "Dealing with hardware limitations" 概念框内的训练时长
参考表被压成单段长文（表头+6 数据行连排不可读）。

**根因**：该表无网格线（区别于 Table 1.1），PDF 以每视觉行一个块提取、
块内 \n 分隔各列；concept_box 吸收后 bodies 合并丢失行列结构。
旧审计"全书唯一表格 Table 1.1"结论系漏判——无格线表不在其扫描口径内。

**类似情况排查**：按「≥4 个连续等高(<12pt)多列短块、间距 17±3pt、
同列起点」特征全书扫描，仅两处：p32=Table 1.1（已处理）、p252=本例。

**修复**：patches 新增 `fix_hardware_runtime_table`——结构信号全书定位
（页内 ≥5 连等距多列短块且首块首列为 'Model name'），列数据运行时从
PDF 提取（无硬编码内容），生成引用块内 GFM 管道表替换压行长行。
幂等可重跑。调试要点：表头→首数据行间距 19.02pt（其余 16.98），
链容差需 ±3pt。

**结果**：引用块内 GFM 表 6 数据行完整；verify ALL PASS；双哈希一致。

## 模型输出展示区围栏化（2026-08 第七轮，L7621 型）

**现象**：ch7 Ollama 评分输出（Dataset response:/Model response:/Score:
+ '>> …' + 模型解释文本）泄漏为正文段落，每行独立成段不可读。

**根因**：该展示区用 FranklinGothic 排版（Demi12 标签 + Book9.5 缩进体，
x≥117）而非 Courier → classify 判 prose。旧 wrap_leaked_output_block
以硬编码内容锚只覆盖 p256/257 的 3 组 prompt 示例，p265/266 评分输出
2 组漏网；且旧 pass 剥 '### ' 前缀造成围栏内容失真。

**影响面排查**：全书 FG-Demi12 x0≥115 种子 20 个、FG-Book9.5 x0≥115
成员 37 个，恰好组成 7 组、零噪声（x=114 为概念框 body 被阈值排除；
'This chapter covers'/概念框标题 x<115 天然排除）。组间 gap(12.9pt)
小于组内 gap(24.8pt)，纯几何不可分界——起点模式（Demi12 标签 或
'Below is an instruction' 开头 Book 体）是唯一可靠边界。

**修复**：classify._mark_showcase_groups 结构化编组（meta['showcase']）
+ render 围栏渲染；merge_pending_prose/two_line_headings 跳过 showcase
块；移除 wrap_leaked_output_block 调用。围栏内恢复 PDF 原文含
'### Instruction:' 前缀。

**连带发现与修复**：
1. merge._should_merge_prose 的 force_open kwarg 调用为遗留死代码，
   签名不匹配且语义与 golden 权威形态矛盾（会吞文献区描述句、粘连
   引文）——已移除强制合并。
2. render.fix_blockquote_continuations 把「URL/数字(ISBN)/连字符结尾的
   引文行 + 大写描述句」误判为折行续行（附录 B 文献区 4 处误吞）——
   实体结尾非断句，补 URL/isdigit/'-' 三类排除。
3. 顺带修复 golden 中 2 处引文折行断裂（Dolma Research 行、Hayden Liu
   ISBN 行现正确归入引用块）。

**结果**：7 组全部 ```text 围栏化、行序忠实 PDF；diff 对比仅 showcase
两区 + 2 处断裂修复；verify ALL PASS；audit 247/247；双哈希一致。
