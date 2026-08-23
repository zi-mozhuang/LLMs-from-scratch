# 章末 Summary 列表化改造（2025-08）

对应 pdf-to-md-plan.md「Summary 列表」条目。结构信号：`classify` Summary 状态机 + `merge.merge_summary_items` + `render` summary_list 分支。

## 问题（改造前）

PDF 每章 Summary 是 Wingdings 圆点列表：每条 bullet = 首块（Wingdings 字体、`\uf0a1` 圆点）+ N 个续行块（普通字体纯折行），部分 bullet 内含 `– ` 二级子弹。旧链处理：

1. classify 规则 7：`\uf0a1` 块 → kind="callout" → render 输出独立 `> ` 引用块，段间空行 → 渲染成 N 个分离盒子；
2. 续行块 wing=False → prose → 被 merge_pending_prose 吸收或成裸孤行（ch1 "LLMs."、ch2 整段、ch7 "(for example...)"）；
3. 行级补丁 normalize_list_markers 把引用后裸 "– " 行强拉成 "> - " 杂交形态；
4. 同型事故：悬挂连字符 "X- and Y-"（time/GPT/CPU 三处）被两层断词拼接器误并为 timeand/GPTand/CPUand。

## 影响面统计（Step 0，改造依据）

全书区域外 `\uf0a1` 共 182 块：Summary 区内 65、参考文献列表 93、正文讲解列表（§2.4/§4.1/§6.3/§7.7/§7.8）21、前置链接 3。
→ **只动 Summary**：一刀切会把参考文献等一并变列表。classify 用状态机精确定位：heading 文本恰为 "Summary" 进入，任一下一级 heading 退出；仅区域内 `\uf0a1` 块判 `summary_list`，其余维持 callout 渲染不变。

## 改造

| 层 | 改动 |
|----|------|
| classify | `in_summary` 状态机（零硬码页号）；区域内 `\uf0a1` → `summary_list` |
| merge | `merge_summary_items`（先于 prose 合并）：summary_list+连续 prose 吸收为单块，`_build_summary_items` 按 PDF 行拆项——含 `\uf0a1` 行→一级项，行首 –/•→二级项，其余为折行续行 |
| render | summary_list → `- `/`  - ` 多级列表；不再产生任何 `> ` |
| 断词 | `_dehyphen_replace`（merge）与 `_join_split`（render SPLIT_RE）各加连词守卫：右侧为 and/or/nor/to 的 "- " 悬挂式保留原样 |

断词续行保持 "previ- ously" 空格形拼接，交由 BREAK_RE + COMPOUND/JOIN 词表统一裁决（与正文同语义）。

## 审计与校验

- `audit_summary_formatting.py`：S1 引用行残留 / S2 裸段落行 / S3 列表项数 PDF↔MD 对账（需 classify 后 blocks）/ S4 en-dash 残留 / S5 断词黑名单 / S6 二级缩进恰两空格。verify 第 8 步接入（run_pipeline 传 blocks）。
- 改造后：8 区域 67 项 ↔ PDF 67 全对账；违规 0。

## 效果

真碎片式断裂清零：孤行/脱列段全部并入所属项；二级子弹正确缩进；三处悬挂连字符恢复原样。diff 分诊：67 个变更块全部落在 8 个 Summary 区 + 3 处连字符位，零未预期变更。参考文献列表（93 块）与正文讲解列表（21 块）按决策维持旧链 `> ` 语义。
