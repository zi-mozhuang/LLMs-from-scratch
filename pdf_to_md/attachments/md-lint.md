# 渲染语义 Lint（pipeline/md_lint.py + verify 第 6 步）与图片硬换行（2025-08）

## 问题

§3 断言只查"结构完整性"（围栏/PUA/锚点），查不出"转成 Markdown 后渲染语义走样"——典型如 `![Fig]` 行后无硬换行，图片与图注在 CommonMark 下并段渲染成一行（全书 126 处）；CRLF 外部污染会进一步吞掉两空格硬换行。此类问题肉眼难察、逐处补丁不可持续。

## 规则集（围栏感知逐行状态机）

| 规则 | 缺陷 | 级别 |
|------|------|------|
| R1 图片行硬换行 | `![..](..)` 后跟非空行须以两空格结尾；render 步骤 7.5 `fix_image_hard_breaks` 在源头生成 | ERROR |
| R2 行尾规范 | CR/CRLF 污染（编辑器/git autocrlf）破坏硬换行 | ERROR |
| R3 标题邻接 | ATX 标题前一行非空（`<a id>` 锚点豁免）→ 不解析为标题 | ERROR |
| R4 表格起始邻接 | 表格首行前非空 → GFM 不解析表格 | ERROR |
| R5 杂散尾随空白 | 非图片行尾随 ≥2 空格 = 意外硬换行 | WARN |
| R6 强调失衡 | 围栏外单行 `*` 奇数（疑似残留星号） | WARN |
| R7 图文配对 | `![Fig X.Y]`↔斜体图注 `*Figure X.Y*`（兼容旧加粗）双向对账 + 距离窗口 | ERROR/WARN |
| R8 连续空行 >2 | 版式噪声 | WARN |
| R9 文件尾卫生 | 缺末尾换行 / 文件尾多空行 | WARN |
| R10 manifest 对账 | manifest figure 与附录 E.1–E.5 必须在 MD 出现——防整图静默消失 | ERROR |
| R11 框内代码三明治 | 裸围栏前后最近非空行均为引用行 → 概念框被代码切断未合成复合框 | WARN |

R10 曾实锤捕获 Fig6.5/Fig7.11 整图消失回归。

## 基线机制

存量可容忍问题记入 `golden/baseline_md_lint.txt`（`RULE|detail`，不含行号），只对**新增**违规报错退出码 1；`--update-baseline` 重写。verify 第 6 步强制校验；`run_pipeline.py` 落盘后信息性输出（`--verify` 时跳过，避免同进程跑两遍）。

## 图片行硬换行规范

`insert_figures` 生成的 `![Fig X.Y]` 行与其后的 `**Figure X.Y**` 图注（或正文段）必须以两个空格结尾实现硬换行，否则 CommonMark 渲染为同一段落。由 render 统一补齐（TOC 之后执行防被 rstrip 抹掉），lint R1 兜底；图片行与相邻内容行的相对顺序保持不变。

## 效果

首次运行暴露 R1×126、CRLF×12088；render 源头修复 + LF 归一后新增违规归零。
