# 标题级别与章节标题修复详细方案

> 返回主方案 §9、§11

## §9 标题级别修复（`fix_structure.py`）

### 问题根因

`p1_text_stream.py`（P1）仅识别 `Chapter N` / `N.N Title` 模式的标题。以下类型标题被遗漏：

- 无编号小节标题（"Who should read this book"、"Summary"）
- 三级子节标题（"3.3.1 A simple self-attention mechanism..."）
- 练习标题（"Exercise 2.1"）
- 索引子标题（"Symbols"、"Numerics"）

### 方案

以 PDF 内置 TOC（193 条）为 ground truth，交叉匹配 MD 中的纯文本行：

1. 提取 PDF TOC，跳过结构性条目（brief contents、contents、index、书名）
2. 对每个 TOC 条目，在 MD 中搜索 NFKC 归一化精确匹配的独立行
3. 匹配条件：该行不以 `#` 开头、不在代码围栏内、**下一行为空行**（排除正文中的交叉引用）
4. 合并行检测：若行文本以标题文本开头但后续还有正文，拆分为标题 + 空行 + 正文
5. 重复标题处理（如 8 个 "Summary"）：返回所有匹配位置，逐一修复
6. 级别映射：PDF L1→`##`、L2→`###`、L3→`####`

### 关键函数

```python
def find_all_heading_candidates(lines: list[str], title: str) -> list[tuple[int, bool]]:
    """Return all 0-based line indices where *title* appears as a standalone
    non-heading line, plus a flag indicating merged heading+body text."""
    title_n = norm(title)
    if len(title_n) <= 2:
        return []
    results: list[tuple[int, bool]] = []
    in_fence = False
    for i, line in enumerate(lines):
        s = line.strip()
        if s.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence or not s or s.startswith("#"):
            continue
        if s.startswith(("!", ">", "-", "*", "|", "<a ")):
            continue
        line_n = norm(s)
        # Exact match: next line must be blank (proves standalone heading)
        if line_n == title_n:
            next_blank = (i + 1 >= len(lines) or lines[i + 1].strip() == "")
            if next_blank:
                results.append((i, False))
            continue
        # Merged match: line starts with title text followed by body text
        if line_n.startswith(title_n) and len(line_n) > len(title_n) + 5:
            remainder = line_n[len(title_n):]
            if remainder and remainder[0].isalpha():
                results.append((i, True))
    return results
```

### 执行结果

- 修复 **72 个缺失标题** + 1 合并行拆分（E.2）；幂等（重跑 0 修复）。
- TOC 覆盖率 164/166（仅 §5.4、§7.4 整节内容提取缺失，非标题问题）。
- §3 断言通过（围栏 1308 成对、无 PUA、TOC⊆anchors）。

### 迭代过程

1. 初版用"前一行非空 = 交叉引用"启发式 → 61 匹配（误过滤 Summary 和 3.6）
2. 改为"下一行为空 = 真正标题"启发式 → 72 匹配
3. 移除 `index_start` 排除 → 添加 Symbols/Numerics

---

## §11 章节标题与"本章涵盖"修复（`fix_structure.py`）

### 问题根因

P1 pipeline 的标题识别仅支持 `Chapter N` / `N.N Title` 模式，无法识别章首页的大号斜体标题（如 `Understanding large` / `language models` 分属两个 PDF block）。同时，"This chapter covers" 的 bullet 列表使用 Wingdings 字体标记，P1 无法处理，导致 bullet 内容几乎全部丢失。

### 异常类型

| 类型 | 模式 | 修复 | 数量 |
|------|------|------|------|
| 章标题碎片 | 多行碎片（如 Ch1/3/7）或单行无 `##` 前缀（如 Ch2/4/5/6） | 合并碎片 + 添加 `## N` 前缀 | 7 |
| "本章涵盖" bullet 丢失 | Wingdings bullet 被 P1 忽略，仅残留尾部碎片 | 从 PDF 提取完整 bullet 列表替换 | 7（共 34 条 bullet） |

### 关键设计

- 从 PDF TOC 获取正确标题文本（含空格），从 block 结构获取标题碎片用于匹配
- 使用 Wingdings 字体检测 bullet 起始块，合并续行块为完整 bullet
- 碎片检测：短行（<80 字）、无终端标点、无特殊前缀
- 脚本幂等，可重复运行

### 执行结果

- 7 个章标题全部修复（`## 1 Understanding large language models` 至 `## 7 Fine-tuning to follow instructions`）
- 7 个"本章涵盖"区域替换为完整 bullet 列表（3+5+5+5+4+6+6 = 34 条）
- MD 从 11212 → 11208 行
- V1-V8 OVERALL PASS
- TOC 覆盖率：164/166（仅 §5.4、§7.4 为已知内容提取缺失）
