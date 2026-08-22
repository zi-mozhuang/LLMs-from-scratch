#!/usr/bin/env python3
"""
fix_missing_sections.py — 修复 §5.4 / §7.4 的标题缺失、残段与丢失代码块。

根因（见 attachments/format-fix-round2.md 子方案 3）：
  - §5.4 节标题（FranklinGothic-DemiI 大字）被 P1 页眉过滤丢弃，正文幸存。
  - §7.4 开头段所在 block 混有 Courier 行内代码，被误判为代码块后在清理中丢失；
    `device` 初始化代码块同样丢失。

所有修复按内容特征定位，幂等可重跑。
"""

import re
import sys
from pathlib import Path

PATH = Path("llms-from-scratch.md")

# §7.4 开头段（依据 PDF 物理页 245 原文重建，行内代码已加反引号）。
SECTION_74_OPENING = (
    "We have completed several stages to implement an `InstructionDataset` class and a "
    "`custom_collate_fn` function for the instruction dataset. As shown in figure 7.14, we "
    "are ready to reap the fruits of our labor by simply plugging both `InstructionDataset` "
    "objects and the `custom_collate_fn` function into PyTorch data loaders. These loaders"
)

# §7.4 丢失的 device 初始化代码块（依据 PDF 物理页 246）。
DEVICE_CODE = [
    "```python",
    'device = torch.device("cuda" if torch.cuda.is_available() else "cpu")',
    "# if torch.backends.mps.is_available():",
    '#     device = torch.device("mps")',
    'print("Device:", device)',
    "```",
]


def fix_54_heading(lines: list[str]) -> int:
    """在 §5.4 首段前插入缺失的节标题；返回插入次数。"""
    if any(l.startswith("### 5.4 ") for l in lines):
        return 0
    anchor = "Thus far, we have discussed how to numerically evaluate the training progress"
    for i, l in enumerate(lines):
        if l.startswith(anchor):
            lines[i:i] = ["### 5.4 Loading and saving model weights in PyTorch", ""]
            return 1
    return 0


def fix_54_hyphenation(lines: list[str]) -> int:
    """合并 §5.4 区残留的断词（P1 跨行拆字未清干净）。"""
    n = 0
    for i, l in enumerate(lines):
        new = re.sub(r"(pre|fig)- (train|ure)", r"\1\2", l)
        if new != l:
            lines[i] = new
            n += 1
    return n


def fix_74_heading(lines: list[str]) -> int:
    """在 §7.4 首段前插入缺失的节标题；返回插入次数。"""
    if any(l.startswith("### 7.4 ") for l in lines):
        return 0
    anchor = "As of this writing, researchers are divided on whether masking"
    for i, l in enumerate(lines):
        if l.startswith(anchor):
            lines[i:i] = ["### 7.4 Creating data loaders for an instruction dataset", ""]
            return 1
    return 0


def fix_74_opening(lines: list[str]) -> int:
    """用完整开头段替换残留碎片 `custom_collate_fn` function for the instruction dataset.。"""
    fragment = "`custom_collate_fn` function for the instruction dataset. As shown in figure 7.14"
    for i, l in enumerate(lines):
        if l.startswith(fragment):
            lines[i] = SECTION_74_OPENING
            return 1
    return 0


def fix_74_device_code(lines: list[str]) -> int:
    """在 device 引导句后补入丢失的初始化代码块。"""
    lead = "The following code initializes the `device` variable:"
    for i, l in enumerate(lines):
        if l.startswith(lead):
            # 幂等：后面已是代码块则跳过
            j = i + 1
            while j < len(lines) and lines[j].strip() == "":
                j += 1
            if j < len(lines) and lines[j].strip().startswith("```"):
                return 0
            lines[i + 1:i + 1] = [""] + DEVICE_CODE
            return 1
    return 0


def main() -> None:
    lines = PATH.read_text(encoding="utf-8").split("\n")
    stats = {
        "5.4 标题插入": fix_54_heading(lines),
        "5.4 断词合并": fix_54_hyphenation(lines),
        "7.4 标题插入": fix_74_heading(lines),
        "7.4 开头段重建": fix_74_opening(lines),
        "7.4 device 代码块": fix_74_device_code(lines),
    }
    PATH.write_text("\n".join(lines), encoding="utf-8")
    for k, v in stats.items():
        print(f"  {k}: {v}")
    # 自检：两个标题必须存在
    text = "\n".join(lines)
    assert "### 5.4 Loading and saving model weights in PyTorch" in text
    assert "### 7.4 Creating data loaders for an instruction dataset" in text
    fences = [l for l in lines if l.strip().startswith("```")]
    assert len(fences) % 2 == 0, "围栏不再成对"
    print("fix_missing_sections 完成，自检通过。")


if __name__ == "__main__":
    sys.exit(main())
