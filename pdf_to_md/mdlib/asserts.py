"""mdlib.asserts — 全局验证断言（对应 pdf-to-md-plan.md §3）。

5 条断言：围栏成对、无 PUA、无 <sup>/<mark>、TOC 链接 ⊆ 锚点。
失败时抛出 AssertionError 并带具体信息。
"""

import re


def run_global_asserts(text: str) -> None:
    """对最终 Markdown 文本运行 §3 全局断言；失败即断言错误。"""
    # 1. 围栏成对
    fences = [l for l in text.split("\n") if l.strip().startswith("```")]
    assert len(fences) % 2 == 0, f"围栏不成对：共 {len(fences)} 个 ```（应为偶数）"

    # 2. 无 PUA 希腊字母（私有区 U+F000–U+F0FF）
    m = re.search(r"[\uF000-\uF0FF]", text)
    assert m is None, f"存在 PUA 私有区字符：{m.group(0)!r}"

    # 3. 无 HTML 上标/标记残留
    assert "<sup>" not in text and "<mark>" not in text, "残留 <sup> 或 <mark> 标签"

    # 4. TOC 链接 ⊆ 锚点（全部可跳转）
    anchors = set(re.findall(r'<a id="([^"]+)">', text))
    links = set(re.findall(r'\]\(#([^)]+)\)', text))
    missing = links - anchors
    assert not missing, f"存在无法跳转的 TOC 链接：{sorted(missing)[:10]}"
