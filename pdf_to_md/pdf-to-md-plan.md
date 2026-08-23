# PDF → Markdown 转换方案

> **目标**：将 `Build a Large Language Model (From Scratch) (Sebastian Raschka).pdf` 转为可阅读 Markdown。
> **路线**：图片独立提取 + 文本流直接转换（PyMuPDF 直提，不复用 pymupdf4llm，不 import 旧补丁链）。
> 本文件是索引与不变量；详细子方案见 `attachments/`，历史执行计划见 `attachments/archive/`。

## 流水线总览

单一入口 `bash run_pipeline.sh`（冷 ~7s / 热 ~2s，产物确定性可哈希比对）。
数据流固定 **P0 → extract → classify → merge → render → verify**；旧补丁链已归档 `.backup/legacy_scripts/`（只读留档），回归基准见 `golden/`。

| 阶段 | 脚本 | 输入 → 输出 |
|------|------|------------|
| P0 图片提取 | `p0_extract_images.py` | PDF → `extracted_images/{embedded,figures}/` + manifest |
| 提取 | `pipeline/extract.py` | PDF → Page/Block 页模型（一次拿全字体/颜色/绘图/TOC；带 `.cache/pages.pkl` 磁盘缓存） |
| 分类 | `pipeline/classify.py` | 页模型 → 语义块（规则前移到提取期，禁止新增判定） |
| 合并 | `pipeline/merge.py` | 块级合并：同框碎片→段落→代码→两行标题（词表唯一来源 `mdlib/config.py`） |
| 渲染 | `pipeline/render.py` (+`patches.py`/`index.py`) | 语义块 → 最终 MD（图文合成、数学清洗、加粗、TOC+锚点、内容补丁、索引重排；文案在 `patches_data.json`） |
| 校验 | `mdlib/asserts.py` + `pipeline/verify.py` | §3 断言 + TOC/Figure/概念框/covers 对账 + 图片完整性 + 渲染语义 lint + 引用块碎片对账 + Summary 列表对账 |
| 离线审计 Summary | `audit_summary_formatting.py`（只读） | Summary 区 S1–S6 规则检查与列表项数 PDF↔MD 对账 |

离线工具（不属管道）：`p0_extract_images.py`、`figure_text_detect.py`（被 extract 调用）、`audit_quote_fragmentation.py`、`.backup/legacy_scripts/check_*.py`。

## 1. 方案决策依据（PDF 实测事实）

| 事实 | 对策 |
|------|------|
| 内嵌位图极少：全书仅 33 个唯一 xref | 按 xref 直接提取原始字节，无损 |
| 正文图表是矢量绘图（143 个 `Figure X.Y`），非位图 | 定位区域后高分辨率渲染（3x ≈ 216 DPI） |
| 版式规律固定：图在上、`Figure X.Y` 标题紧贴其下 | 以标题 bbox 为锚点反推图形区域，标题充当完备性基准 |
| Courier 字体 = 代码块（阈值 ≥75%） | 按字体识别代码块，保留原始行结构与缩进 |
| 边注 bold-italic；侧注字体 `HumanistMann521-BoldCond` | 按字体 + 位置聚类自动收集 |
| 概念框固定填充色 (0.969,0.961,0.910)，宽≥100pt 高≥40pt | 按颜色+尺寸识别 callout box |

## 2. 核心原则（违反即返工）

1. **不要事后修补，要在提取时就拿对数据**。
2. **不做全局行移动**；图注类转换一律原地处理。
3. **小范围测试优先**：单页/单函数验证通过后再全量生成做全局校验。
4. **一切判定基于结构信号**（字体/颜色/位置聚类/跨页频次），无硬码字符串/页号/坐标。
5. 删任何"疑似死代码"前必须 A/B：monkeypatch 为 identity → 全量渲染 → MD 哈希对比，不变才删（已有三项候选被此护栏证伪保留，见 [attachments/performance.md](attachments/performance.md)）。

## 3. 全局验证断言（`mdlib/asserts.py`）

```python
fences = [l for l in text.split("\n") if l.strip().startswith("```")]
assert len(fences) % 2 == 0                      # 围栏成对
assert not re.search(r"[\uF000-\uF0FF]", text)   # 无 PUA 希腊字母
assert "<sup>" not in text and "<mark>" not in text
anchors = set(re.findall(r'<a id="([^"]+)">', text))
links = set(re.findall(r'\]\(#([^)]+)\)', text))
assert links <= anchors                          # TOC 全部可跳转
```

完备性对账以 `pipeline/verify.py` 为准（TOC 缺口≤2、Figure≥141、概念框计数、covers 存在、图片完整性、lint 新增=0、真碎片=0）。

## 4. 文本转换规则速查（现由 pipeline 承载）

| 规则 | 参数 |
|------|------|
| 页眉/页脚剔除（span 级） | 字体∈HEADER_FONTS 且 top<35pt 或 bottom>页高-75pt 且字号<10.5；图注块整体豁免 |
| 代码块 | block 内 Courier 占比 ≥75% → 围栏（保留缩进）；正文 Courier 片段包行内反引号 |
| 标题 | PDF TOC（NFKC 归一化匹配）为唯一真相源；级别映射 ##/###/#### |
| 段落合并 | 块内折行并一段；跨块按终止标点/开放词守卫（`_should_merge_prose`），允许跨页 |
| 代码清单合并 | 连续 code run 合并，图片/绘图不打断（fenced 828→605） |
| 断词 | 词表唯一来源 `mdlib/config.KEEP_HYPHEN_PREFIXES`（消除旧双词表矛盾） |
| 概念框 | CALLOUT_FILL 颜色+尺寸+重叠 ≥0.20；同矩形碎片归组合并（`box_key`） |
| 章末 Summary | heading 文本=="Summary" 状态机；区域内 Wingdings 圆点 → `- `/`  - ` 多级列表（详见 [attachments/summary-lists.md](attachments/summary-lists.md)） |
| 图文合成 | `^Figure X.Y` 前插图链；图注斜体 `*Figure N.M*`（首词小写守卫防误染正文）；图片行尾两空格硬换行（GFM） |
| TOC/锚点 | 扫描真实标题→注入唯一锚点→书级 TOC；重复 Chapter 加 `(Appendix)` |

## 5. 专项修复索引（细节全在附件）

| 条目 | 一句话 | 附件 |
|------|--------|------|
| 图内文字删除 | 两级：几何相交剔除（1149 块）+ 导出后窗口清理（11 行） | [figure-text-removal.md](attachments/figure-text-removal.md) |
| 标题级别 / 章节 covers | TOC ground truth 补 72 标题；7 章 covers 重建 34 条 bullet | [heading-and-chapter-fixes.md](attachments/heading-and-chapter-fixes.md) |
| 跨块段落 / 代码清单 | pending_prose ~121 处、pending_code 合并 | [paragraph-and-code-split.md](attachments/paragraph-and-code-split.md) |
| 异形文本块 | 6 类（callout/Exercise/NOTE/Listing/Table/covers）识别重建 | [special-blocks.md](attachments/special-blocks.md) |
| 格式一致性 round2 | 附录 Listing 加粗等收尾 pass | [format-fix-round2.md](attachments/format-fix-round2.md) |
| 提取健壮性 | Fig7.8 缺失 / Fig6.5·7.11 整图静默消失 / 附录 E 标签泄漏 / 边注丢失 86%→4% | [extract-robustness.md](attachments/extract-robustness.md) |
| 引用块碎片合并 | 同矩形概念框碎片 28 对→0；审计工具三类分诊 | [quote-fragmentation.md](attachments/quote-fragmentation.md) |
| Summary 列表化 | 章末小结 Wingdings 列表 → `- ` 多级列表（67 项对账）；悬挂连字符守卫 | [summary-lists.md](attachments/summary-lists.md) |
| Listing 旁注 | 代码旁箭头注释短语归位为 fence 内 `# ` 注释（229 块对账；三条件防边注误收） | [listing-callouts.md](attachments/listing-callouts.md) |
| 图注斜体 | `*Figure N.M*` 斜体区别于正文（首词小写守卫防误染正文引用句） | §4 图文合成行 |
| 框内代码 | 概念框内代码合成复合框输出 GFM 引用内围栏 `> ``` `（空行用 ">" 保持盒子连续；fence_mask/md_lint 兼容引用围栏） | verify 第 10 步对账 |
| 截图式代码图重建 | 8 处书版截图式代码（文本层无）PNG 逐字转录 + 锚点补丁重建（`patches_data.json`，含 output 段）；P0 按 `crop_top` 标记裁 6 图顶部代码带（纯示意图如 Fig 3.22 不裁） | verify 第 11 步对账 + `audit_promise_chains.py` 承诺链审计 |
| 渲染语义 lint | R1–R10 规则 + 基线机制（新增违规才报错） | [md-lint.md](attachments/md-lint.md) |
| 性能与减量 | 冷 16s→7.3s 热 2.0s；A/B 护栏下的死代码清理 | [performance.md](attachments/performance.md) |

## 6. 已知遗留（非阻断）

- §5.4 / §7.4 在 PDF 提取中缺失，已由内容锚点补丁重建关键段与代码块（`patches.py` + `patches_data.json`）。
- 正文上标直提时平化，未做 LaTeX 还原（典型：附录 E LoRA 权重更新公式以 Fig E.1 截图承载，正文仅余残段）。
- 个别编号列表点号丢失（PDF 内编号与文本分块，如 "1 Setting" 应为 "1. Setting"，ch5 deterministic 段 2 处）。
- 重复 Chapter 标题（附录练习）：TOC 以 `(Appendix)` 区分，正文标题文本相同。
- Fig 3.12 p84 / Fig 6.5 p195 的 PNG 上缘多截相邻代码片段（正文围栏中已有，属可接受冗余；几何上无法与纯文本图可靠区分，约束尝试曾伤及 Fig 7.7 故回退）。
- 跨页截断概念框 3 处呈两个引用块（p97-98 / p126-127 / p161-162 "(continued)"）。
- 无框 Wingdings 列表中，仅章末 Summary 已列表化；参考文献（93 块）、正文讲解列表（§2.4 特殊 token、§4.1 配置参数等 21 块）与前置链接按决策维持旧链 `> ` 引用块语义（影响面统计见 [attachments/summary-lists.md](attachments/summary-lists.md)）。

## 附件索引

`attachments/`：figure-text-removal · heading-and-chapter-fixes · paragraph-and-code-split · special-blocks · format-fix-round2 · extract-robustness · quote-fragmentation · md-lint · performance（各条目对应关系见 §5 表）。
历史执行计划归档于 `attachments/archive/`（line_continuity_subplan、restructure-execution-plan）。
