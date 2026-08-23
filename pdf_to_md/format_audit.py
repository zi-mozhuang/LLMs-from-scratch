#!/usr/bin/env python3
"""format_audit — PDF 特殊格式清单 vs MD 差分（Phase 1）。

读 format_scan.json + llms-from-scratch.md，按类别产出
"PDF 有 / MD 缺失或弱化" 的差异报告 + 抽样。
匹配为启发式（子串/标记），结论标注 approximate。
"""
import json
import re

SCAN = "format_scan.json"
MD = "llms-from-scratch.md"


def load():
    with open(SCAN, encoding="utf-8") as fh:
        scan = json.load(fh)
    md = open(MD, encoding="utf-8").read()
    return scan, md


def clean(t: str) -> str:
    """归一化连字/智能引号，消除 PDF↔MD 表示差异。"""
    return (t.replace("\ufb01", "fi").replace("\ufb02", "fl")
             .replace("\u2019", "'").replace("\u2018", "'")
             .replace("\u201c", '"').replace("\u201d", '"'))


def probe(t: str, n=30) -> str:
    return clean(t).strip()[:n]


def in_md(md: str, p: str) -> bool:
    return clean(p) in clean(md)


def wrapped(md: str, p: str, marker: str) -> bool:
    """p 所在行是否含 marker（** 或 *），近似判断是否被包裹。"""
    if not p:
        return False
    pc = clean(p)
    for line in clean(md).splitlines():
        if pc in line and marker in line:
            return True
    return False


def audit_margin(scan, md):
    items = scan["margin_notes"]
    miss = [x for x in items if not in_md(md, probe(x["text"]))]
    right = [x for x in items if x.get("right_margin")]
    right_miss = [x for x in right if not in_md(md, probe(x["text"]))]
    print(f"\n[页边注 margin_notes] 总数 {len(items)} | 右侧边注 {len(right)}")
    print(f"  MD 中缺失(内容丢失): {len(miss)}  ({100*len(miss)/max(1,len(items)):.0f}%)")
    print(f"  右侧边注缺失: {len(right_miss)}")
    for x in miss[:5]:
        print("    - p%d: %s" % (x["page"], x["text"][:55]))


def audit_emphasis(scan, md, key, marker, label):
    items = scan[key]
    # 去重近似（同文本多次出现只算一次）
    seen = {}
    for x in items:
        p = probe(x["text"])
        if not p:
            continue
        seen.setdefault(p, x)
    unmarked = [v for p, v in seen.items() if not wrapped(md, p, marker)]
    print(f"\n[{label}] 去重后 {len(seen)} 处 | 未在 MD 以 {marker} 包裹: {len(unmarked)}")
    for v in unmarked[:6]:
        print("    - p%d: %s" % (v["page"], v["text"][:50]))


def audit_math(scan, md):
    chars = {}
    for x in scan["math_pua"]:
        chars.setdefault(x["ch"], 0)
        chars[x["ch"]] += 1
    print(f"\n[数学 PUA] 出现 {len(scan['math_pua'])} 次 | 不同码点 {len(chars)}")
    for ch, c in chars.items():
        print("    码点 U+%04X 次数 %d | MD 含此码点: %s"
              % (ord(ch), c, ("U+%04X" % ord(ch)) in md))
    syms = re.findall(r"[αβγδεζηθικλμνξοπρστυφχψω∑∫√×÷≈≠≤≥∈∉∂∇]", md)
    print("  MD 已转符号样例:", list(dict.fromkeys(syms))[:16], "(共 %d)" % len(syms))


def audit_superscript(scan, md):
    items = scan["superscript"]
    flat = [x for x in items if in_md(md, probe(x["text"], 20))]
    print(f"\n[上标 superscript] 真实上标 {len(items)} | 在 MD 中(已平化,无<sup>): {len(flat)}")
    for x in items[:5]:
        print("    - p%d: %s" % (x["page"], x["text"][:40]))


def audit_links(scan, md):
    items = scan["links"]
    # 仅统计有文本链接
    txt = [x for x in items if x["text"].strip()]
    linked = [x for x in txt if re.search(r"\]\(#?" + re.escape(x["text"].strip()[:15]), md)]
    print(f"\n[内部超链接 links] 总数 {len(items)} | 含文本 {len(txt)} | MD 成可点链接: {len(linked)}")
    print("  -> 其余为正文交叉引用，静态 MD 不可跳转（可接受差异）。")


def audit_tables(scan, md):
    cand = scan["tables"]
    md_tbl = sum(1 for l in md.splitlines() if l.count("|") >= 2)
    print(f"\n[表格 tables] 启发式候选区域 {len(cand)} 页: "
          + ", ".join("p%d" % c["page"] for c in cand))
    print(f"  MD 含 | 行数: {md_tbl}（需人工辨认真表格 vs 代码/张量形状误判）。")


def audit_callouts(scan, md):
    n = len(scan["callouts"])
    bq = sum(1 for l in md.splitlines() if l.lstrip().startswith(">"))
    print(f"\n[Callout 框] PDF 填充命中 {n} | MD 引用块(> )行数 {bq}")
    print("  -> 需抽样确认每个 callout 用 >/标题 分隔、未混正文。")


def audit_footnotes(scan, md):
    n = len(scan["footnotes"])
    print(f"\n[脚注 footnotes] 检测到 {n}（若 0 表示无独立脚注或已并入正文）。")


def main():
    scan, md = load()
    print("=" * 60)
    print("PDF ↔ MD 特殊格式差分报告 (heuristic / approximate)")
    print("=" * 60)
    audit_margin(scan, md)
    audit_emphasis(scan, md, "bold", "**", "正文加粗 bold")
    audit_emphasis(scan, md, "italic", "*", "斜体 italic")
    audit_math(scan, md)
    audit_superscript(scan, md)
    audit_links(scan, md)
    audit_tables(scan, md)
    audit_callouts(scan, md)
    audit_footnotes(scan, md)
    print("\n" + "=" * 60)
    print("报告结束。差异为启发式估计，建议对高缺失类别人工抽验。")


if __name__ == "__main__":
    main()
