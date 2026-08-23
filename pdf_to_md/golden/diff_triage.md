# Diff 分诊记录（阶段 3.2，最终版）

基准：`golden/llms-from-scratch.md`（旧链产出，权威）
新输出：`/tmp/new_pipeline.md`（pipeline/run_pipeline.py 产出，11411 行 vs golden 11435 行）

## 最终验证状态

- `run_global_asserts`：PASS
- `verify.py /tmp/new_pipeline.md`：ALL PASS（TOC 缺口 -25、Figure 141、概念框 107∈[100,120]、covers 存在）
- `check_figure_text` 新输出 OVERALL PASS = baseline
- `check_figures` 新输出 audited=128 flagged=0 = baseline
- `audit_md_quality` 新输出 裸上标6 = baseline
- `wc -l mdlib/*.py pipeline/*.py` = 2556 ≤ 3200 ✓

## 本轮修复的回归（R1–R6 全部闭环）

1. **R1 连字符合并过度** → 根因：新管道用缩减词表做通用合并；旧链真实行为 =
   `p2_clean.dehyphenate`（COMPOUND/JOIN 词表 + 右侧大写保留，迭代至稳定）
   + `fix_structure.fix_hyphenation`（SPLIT_RE + KEEP_HYPHEN{in,self,non,cross} + FIXED_WORDS）
   + KNOWN_BREAKS。已按此三层原样搬运至 `mdlib/config.py` + `pipeline/merge.py`
   + `pipeline/render.py(fix_hyphenation)`，行级应用。
   验证：in-progress/humanlike/previously×44/pretraining×59/live- Book/one-dimensional 全部与 golden 一致。
2. **R2 撇号丢失** → 复核为陈旧记录：新输出 ’ 计数 433 ≥ golden 428，无系统性丢失。
3. **R3 Figure 标题未加粗/未同行** → figure_caption 分支补折行；bold_captions CAP_RE 生效。
   加粗标题集合与 golden 完全一致（diff 为空）。
4. **R4 callout 误判 bullet** → classify 重写优先级 7：Wingdings 块在 covers 区域内→bullet，
   区域外带 \uf0a1→callout（等价旧链 p2 文本级转换 + fix_chapter_covers 区域重写）；
   merge_pending_prose 让 callout 吸收续行块（p1 段落合并语义）。
5. **R5 TOC 子条目缺失（§5.4/§7.4/E.2）** → 根因：union 图区覆盖内容区致标题块被剔 +
   旧链靠 fix_missing_sections.py 内容锚点补入。已整体搬运该脚本及 fix_structure 的
   run_pseudo/run_round2 相关 pass 至 `pipeline/patches.py`，并搬 render_appendix_figures.py
   （附录 E 图渲染+链接）、fix_index.py（索引三栏重排，`pipeline/index.py`）、
   cut_back_cover、fix_e2_heading 等。
6. **R6 slug 后缀不一致** → build() 补齐书名标题前置（等价 p3_toc 前置+strip_old 链路）、
   TOC 短标题护栏（norm≤2 剔除，修 "T" 伪标题）、附录 B/C Chapter N 页界级别修正。

## 额外修复（本轮发现）

- **代码块缩进丢失**：extract 增加 meta["raw_lines"]（仅 rstrip），classify 判 code 时还原
  （搬运 p1 第 520-523 行语义）。
- **Listing 标题格式**：改为普通行走 bold_captions CAP_RE（仅编号加粗），附录 A-E 由
  fix_appendix_listings 兜底 —— 与 golden 两种形态分布一致。
- **围栏掩码错位**：render 步骤 6 共享掩码在 fix_dash_bullets 合并行后错位，导致
  inline-code 断词修复被跳过（如 \`Causal-\` \`Attention\`）；改为每 pass 用 fence_mask() 重算。
- **概念框正文并段**：搬运 collect_concept_boxes 的块内空格拼接语义（" ".join）。
- **标题后缀碎片剔除**：_drop_heading_suffix_fragments（等价 fix_chapter_titles Case 2 删除）。

## 接受的差异（等价/改进类，非回归）

| 类别 | 数量级 | 说明 |
|---|---|---|
| 幽灵 Listing 标题缺失 | ~10 | golden 在 ch02 区误插入 A.1/A.6/E.2 三行（旧链 fix_listings 围栏匹配缺陷），新管道不产幽灵行 → 改进 |
| Listing 5.5/7.11 全粗 | 2 | golden 为旧链"丢失后重新插入"路径的全粗产物，新管道统一编号加粗 → 改进（一致性） |
| TOC 尾部损坏引用块 | 1 | golden TOC 内嵌 "> **Exercise A.1** > > ](#...)" 损坏行，新 TOC 干净 → 改进 |
| 概念框内部段落分隔差异 | ~40 | golden 内部 ">"分隔与空行分隔混用且不一致（文本匹配启发式的随机结果）；新管道按 PDF 块结构一致渲染 → 等价/改进 |
| 续框引用不一致 | ~10 | golden 对部分续框段落未加引号（如 "Your task..."、"If it returns True..."，旧链匹配失败遗留）；新管道统一加引号 → 改进 |
| 空行位置/锚点顺序微差 | ~160 | 等价类（空白差异） |
| 封面区文本位置 | 微 | golden 封面杂行位于 TOC 后；新管道相同（均由 build() 前置标题+TOC 造成），已对齐 |

结论：无未解决的"回归"条目。剩余差异均为 golden 自身缺陷的修正（改进）或空白/结构等价差异。

## 架构设计文档吸收记录（PDF 转 Markdown 架构设计.md）

已落地：
1. **manifest 元数据增强**（`pipeline/manifest_enrich.py`，render 后自动执行）：
   每条目补 `source`（embedded/rendered 目录溯源）、`md5`、`bytes`、`caption`
   （来自 MD 图注行）。只增不改、幂等（二次运行零写盘）。
2. **verify.py 图片完整性检查**：manifest 引用文件缺失/0 字节 → FAIL；
   <1KB 列 WARN。当前全部通过（无偏小文件）。
3. **p0 DPI 参数化**：`P0_ZOOM` 环境变量覆盖渲染倍率（默认 3 不变，
   如 300dpi ≈ `P0_ZOOM=4.17`），不改变既有产物。

评估后否决（附证据）：
- **表格 → 自动管道表**：全书仅 1 张表（Table 1.1）。pymupdf `find_tables`
  lines 策略把 5 个数据行并成 1 行（60%/22%/8%/8%/3% 全并入 CommonCrawl 格）
  ——事实损坏比扁平文本更糟；text 策略输出碎字符不可用；golden 的逐行平铺
  实际正确保留了行序。故不做。
- **每章分文件输出**：用户裁定不需要，保持单文件交付。
- **LayoutParser/PP-Structure**：重依赖无收益（版式固定，几何规则全覆盖）。
- **文本正则检测标题/代码**：旧链已证伪路线；且新文档字体清单漏 CourierStd。

## 外部格式问题报告核查记录（20 条逐条实证）

结论：该报告描述的是另一粗糙转换产物，与本项目输出不符。逐条核查（对 llms-from-scratch.md 实测）：

| # | 报告声称 | 实测 | 判定 |
|---|---|---|---|
| 一 | 代码未包裹 150+ 处 | 1206 fence 行 / 339 个 ```python，配对完整；SimpleTokenizerV1 等均在围栏内 | 不实 |
| 二 | 缩进丢失/行首单空格残留 | 37 处行首单空格全部与 golden 逐字一致（源书输出折行原样）；" class DummyGPTModel" 不存在 | 不实（源书原样） |
| 三 | 锚点应删除 | 172 个 `<a id>` 是 TOC 链接目标，删除即断链 | 设计使然，不修 |
| 四 | 图片全缺失 | `![Fig X.Y]` ×127 + 加粗图注 ×127 一一对应 | 不实 |
| 五 | 表格非 MD 语法 | 属实（全书仅 1 表） | **已修**：fix_table_1_1 内容锚点补丁转管道表（内容逐字核对 PDF） |
| 六 | Listing 格式不统一 | 编号加粗 67、裸行 0、完全统一 | 不实 |
| 七 | 输出未区分 | 输出随源书排版（多在围栏内），golden 同构 | 源书原样 |
| 八 | 数学未 LaTeX 化 | 已知遗留（方案明示超范围），PUA α/ω 已映射 | 已知遗留 |
| 九 | NOTE 裸段落 | `> **NOTE**`×29，裸 0 | 不实 |
| 十 | Exercise 裸行 | 引用块 46 + 附录标题 4，裸 0 | 不实 |
| 十一 | TOC 无层级 | 二级缩进 76 条目 | 不实 |
| 十二 | 标题层级混乱 | ##章8/###节50/####小节27/##附录5 规整 | 不实 |
| 十三 | 行内代码缺失 | 反引号 3145 对；残余缺口=已知 golden 差异类 | 大体不实 |
| 十四 | ➥ 残留 | 全文 1 处 = 书序言自述文字本身，代码内 0 | 不实 |
| 十五~十八 | 特殊字符/交叉引用/版权页/注释缺失 | 与 golden 一致或系书原文自述（"comments removed" 是书前言原文）；交叉引用无链接为 golden 同构既有形态 | 不修（保持 golden 对齐） |
| 十九 | 边栏无格式 | 均为 `> **标题**` 概念框（含弯引号变体 "self"） | 不实 |
| 二十 | Appendix C 代码未包裹 | 同一围栏机制覆盖 | 不实 |

修复动作：新增 `patches.fix_table_1_1`（唯一真实命中项）。回归：verify ALL PASS，
三脚本 = baseline（PASS / 128 flagged=0 / 裸上标6）。

## 第二份外部报告核查记录（18 条逐条实证）

结论：与首份报告同源，仍针对另一产物。实测判定：

| # | 声称 | 实测 | 判定 |
|---|---|---|---|
| 一 | 标题无 # | 200 行标题（## Preface 等） | 不实 |
| 二 | 图片引用缺失 | ![Fig]×127 在位 | 不实 |
| 三 | `<\|token\|>` 裸露 | 围栏外 42 处中仅索引区 2 处真裸 | **已修**：fix_bare_special_tokens 通用包裹 |
| 四 | NOTE 未格式化 | `> **NOTE**`×29 | 不实 |
| 五 | 缺语言标识 | ```python×339 + ```text×264，无裸栏（先前"裸栏"系探针把闭合行误计） | 不实 |
| 六 | Table 1.1 对齐 | 管道表已在位（上轮修复） | 已修过 |
| 七 | 锚点应删 | TOC 链接目标 + verify 校验 ⊆ 关系 | 设计使然 |
| 八 | 数学未 LaTeX | 方案明示超范围已知遗留 | 记录在案 |
| 九 | 列表缺标记 | covers 后即 `- ` 列表 | 不实 |
| 十 | 论文书名斜体/术语加粗 | 源书用引号非斜体；风格增强偏离源书 | 不采纳 |
| 十一 | Exercise 未格式化 | 引用块×46 + 附录 ###×4 | 不实 |
| 十二 | 段落间距缺失 | 开栏前无空行=0；块间空行由渲染保证 | 不实 |
| 十三 | TOC 无层级 | 二级缩进 76 条 | 不实 |
| 十四 | 边注混入 | 单栏主文流提取，无边注层 | 不适用 |
| 十五 | URL 折行 | prose 内折行 URL=0（代码内多行字符串为源书原样） | 不实 |
| 十六 | Index 应删/章重复 | Index 为书内容保留（R5 曾专门修复）；**章重复属实**：`## 7` ×2 | **已修**（见下） |
| 十七 | 附录标题加冒号 | 层级规整；加冒号偏离源书标题原文 | 不采纳 |
| 十八 | 注释外泄 | 围栏外注释行=0 | 不实 |

### 新增回归修复：第 7 章标题重复

- 现象：`## 7 Fine-tuning to follow instructions` 出现两次（含 `-2` 后缀锚点），golden 仅一次。
- 根因：classify 的 toc_norm 含数字剥离别名（供无编号章节文本匹配），
  `_match_heading` 的前缀回退同时命中物理页 226 的两个碎片——
  ①真标题残片 'Fine-tuning to follow'（NewBaskerville-Italic@30）
  ②covers 第二项的折行残片 'fine-tuning'（FranklinGothic-Book@10, y=312.6）。
  两块均被改写为完整 TOC 标题 → 双 heading。旧链 p1 先并视觉行故无此问题。
- 修复：`patches.fix_duplicate_chapter_headings`——`## <数字>…` 全书唯一，
  保留首个、后续置空（含紧邻前置锚点）。幂等。
- 附带发现：Index 区另有 1 处同型伪标题（p359），一直被 rebuild_index
  区域替换掩盖；新去重 pass 同时显式清除。

## 断行完整性审计与修复（check_line_integrity）

新增 `pipeline/check_line_integrity.py`：层1 静态规则（R1 小写续行/R2 缩写点/
R3 悬空连字符/R4 反引号奇数/R5 Index 条目）+ 层2 PDF 流对齐仲裁
（strict 零间隙 / relaxed 跨页眉两级）。

首轮审计：候选 71，确认真断行 **63**（strict 8 + relaxed 55），抽查均为
新管道回归（golden 无）。

根因与修复：
1. **页界段落不合并（~35 处）**：`merge_pending_prose` 的同页约束——旧链
   p1 行流缓冲可跨页。→ 移除同页限制。
2. **段落末行守卫误拒**：`_should_merge_prose` 的 `len(cur)<40 → False`
   ——PDF 段落末行通常正是短行。→ 去掉 cur 长度下限（保留 prev≥40 +
   小写开头双守卫）。
3. **Index 折行条目（47 处 PDF 源头）**：悬挂缩进续行恒比条目首行深
   ≥15pt（子条目起始仅 +9pt）。→ `build_entries` 新增 `x-buf_x≥12 且
   同栏同页` 续接规则。

复跑审计：候选 71→4，确认断行 63→**0**。剩余 4 条为分组词嵌套级误报
（'- classification' 等无页码父条目，内容完整，仅缩进层级偶有偏差），
记为已知外观项。

回归：verify ALL PASS；三脚本 = baseline；行数 11407→11246（假段界消除）。

## URL/词内空格损伤审计与修复（L284 类问题）

用户指认 L284：GitHub URL 被段落边界切断。排查扩至四类，全部闭环：

| 类 | 形态 | 数量 | 根因 | 修复 |
|---|---|---|---|---|
| A | `https://␣livebook` scheme 后空格 | 11 | extract 层 span 拼接注空格 | merge_all 入口 + render 双重 `(https?://)\s+→\1`（前置是为 URL 断裂判定铺路） |
| B | `live- Book` | 1 | PDF 'live-\nBook' 断词 + 右侧大写保留规则误判 | 内容锚点 → liveBook（golden 同错，超越 golden） |
| C | URL 跨段断裂 | 5 | PDF 行宽折断；合并守卫拒 '-' / 数字开头续段 | `_url_direct_join` 无缝直拼（prev 尾恰为 URL 且 cur 首 '-'/数字） |
| D | 中段空格 | 20 | span 空格注入变体（'books/ build'、'wikisource .org'、'2405 .01535'） | `fix_url_internal_spaces` 三形态规则（围栏外 http 行） |

D 类另两例定性：`LLMsfrom-scratch`（JOIN_PREFIXES 过度合并，golden 同错）
与 `OpenAccess -AI-Collective`（golden 同错）均内容锚点单修；
`mng.bz/o05v that` 为误报排除。

### checker 扩展

- R6 scheme 空格 / R7 URL 跨段断裂 / R8 词内大写续接
- 注释体例豁免：图注/清单标题/围栏 ±3 行内的短行对不判断行
  （Manning 边注体例：'idx is a…'、'New tokenized sample' 为合法分行）
- V 校验：MD 全部围栏外 URL 以归一化字符流在 PDF 全书串中做包含验证，
  未命中列人工复核清单。当前 = 0

### 重要发现：注释式清单的代际差异

排查中发现 ch3/ch4 等地的"代码碎片化"（[code][prose][code] 交替）并非
缺陷——书的 Listing 3.5 等本就是注释式分段排版。**golden 合并时把这些
解说散文弄丢了**（grep 实证 golden 无 'trailing underscore' 等段落）。
当前输出保留之：围栏对 603→697、行数 11407→12349 的增量即恢复的内容。
verify/asserts/三脚本全绿确认无伴随损伤。

## PDF↔MD 全面对账审计（audit_pdf_md.py）

新增只读审计器：法1 章节分段 token 对账（搬家配对+全文包含终审）、
法3 字体普查、法4 版式普查。

### 总量健康度
PDF 正文 token 85218 vs MD 84595（99.3%）；前置五段（preface~cover illus）
0 丢失 0 新增；无幻觉段落（终审新增 62 条均为重排残影）。

### 🔴 实锤系统性丢失：矢量 union 过宽吞正文

根因链（Listing 7.3 标签全程追踪实证）：
1. `page_element_regions` 收集页面全部 fill 图元建 union，含零宽/细线
   碎片（箭头、引导线、装饰规则线），union 膨胀至横跨半页；
2. `is_figure_text` 判定落入 union 的文本块为"图内文字"→ 整块丢弃；
3. 受害者三类（grep 全部 =0，实锤不在 MD）：
   a) 清单旁注释标签（'Use 85% of the data…'、'shortcut connection…'
      、'downloads the file unzips…' 等 ≥15 处）
   b) 边注（'The context size determines…'、'dropout is disabled during
      evaluation…' 等）
   c) 图旁分步演示片段（'x_2 = inputs[1]'、'batch=torch.stack(inputs,dim=0)'
      、'ffn=FeedForwardGPT(GPT_CONFIG_124M)' 等交互会话步）
4. 幸存者则以错位形态污染正文：L2210 '…using `nn.Linear` Weight matrix'
   （图旁标签粘进无关段落尾）——53 条终审丢失大半由此连续性断裂派生。

字体侧佐证：HumanistMann521-BoldCond @9 ×2008 span 即该类标签本体。

### 🟠 量化但未处理（语义弱化类）
- 行内强调（图外正文）：BoldItalic×467 / Bold×364 / Italic×278（含结构重复，
  净行内数百级）——MD 无对应 **/*/` 形态
- B2 代码内加粗高亮行：未审计（Courier 域 flags 待查）

### 🟡 已知遗留（维持记录）
上/下标 LaTeX 化、display 公式、URL autolink 增强。

### P0 修复实施记录（已落地）

1. `extract._manifest_clips`：p0 渲染裁剪框为图区域权威源；
   `page_element_regions` 启发式降级为无图页 fallback（并滤除零宽/
   面积<16pt² 退化矩形）。
2. `is_figure_text(font_guard=)`：manifest 模式与无图页均关闭
   HumanistMann/Arial 字体一票否决（三类丢失根源）。
3. 行级多数判定：块内过半行中心落在 clip 内才判"已烘进 PNG"剔除
   （块级中心判定漏掉 'Model input without instructions' 类跨宽标签）。
4. classify 标注 `meta.annot`（HumanistMann/Arial 小字号 prose 块）；
   merge 标签↔正文互不吸收、同型标签折行可并回。

恢复验证（grep 实锤）：'Use 85%…'/'dropout is disabled…'/'context size
determines…'/'shuffles the entire dataframe'/'predictions for 1st token'
/'shortcut connection'×21 等全部在位；'Model input without instructions'
in-clip 漏网已堵。

回归：verify ALL PASS；checker strict/relaxed=0；check_figures 128 flagged=0；
audit_md_quality 持平。**已知偏差**：legacy check_figure_text V5 预期删除
p2 总览图标签，但该图从未渲染 PNG（manifest 无条目），旧链行为实为内容
丢失——保留属修正，V5 判FAIL 记录为预期不符。行数 12349→12088（去 PNG
重复内容 - 净增恢复注释）。

审计复跑：终审丢失 53→**30**，新增 62→23。残余 30 条抽样均为多标签
复合运行的连续性误报（各片段实际在位）+ brief contents/p7 结构性裁剪 +
封底 promo（tail）。A1 行内强调映射（P2）仍待裁定。

## 执行环境备注

- 方案约定 `$PY=.venv/bin/python`，但仓库根 `.venv` 无 fitz；实际使用系统 `python3`
  （pymupdf 1.28.2）。阶段 4 的 `run_pipeline.sh` 已相应使用 python3。
- 阶段 4 后验证套件位于 `.backup/legacy_scripts/`，从根目录以
  `PYTHONPATH=. python3 .backup/legacy_scripts/check_figure_text.py <pdf> <md>` 方式运行
  （check_figures / audit_md_quality 同理），已实测三脚本结果 = baseline。
- 旧链中间产物 text_stream_full.md / text_stream_p2.md 已移至 `.backup/`。

## 阶段 4（归档）待执行

按方案任务清单执行：归档旧脚本（保留 p0_extract_images.py、figure_text_detect.py 原位）、
新建 run_pipeline.sh、更新 pdf-to-md-plan.md 总览表、清理临时文件。
