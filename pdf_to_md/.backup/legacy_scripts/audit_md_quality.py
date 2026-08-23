#!/usr/bin/env python3
"""全面审计 llms-from-scratch.md 的格式问题（只读，输出报告）。"""
import re, os, unicodedata
from collections import Counter, defaultdict

PATH = "llms-from-scratch.md"
text = open(PATH, encoding="utf-8").read()
lines = text.split("\n")

def report(title):
    print(f"\n{'='*70}\n## {title}\n{'='*70}")

# ---------- 1. 代码围栏 ----------
report("1. 代码围栏")
fence_lines = [(i+1, l) for i, l in enumerate(lines) if l.strip().startswith("```")]
print(f"围栏总数: {len(fence_lines)} (奇数=不配对: {len(fence_lines)%2!=0})")
langs = Counter(l.strip()[3:].strip() for _, l in fence_lines)
print(f"语言标注分布: {dict(langs)}")
# 围栏内部嵌套 ``` 或标题
in_code = False; nested = []
for i, l in enumerate(lines):
    s = l.strip()
    if s.startswith("```"):
        in_code = not in_code
        continue
    if in_code and (s.startswith("#") or s.startswith("![") or s.startswith("> ")):
        pass  # 代码内正常，不报

# ---------- 2. 标题结构 ----------
report("2. 标题结构")
headings = []  # (lineno, level, text)
in_code = False
for i, l in enumerate(lines):
    s = l.strip()
    if s.startswith("```"):
        in_code = not in_code; continue
    if not in_code:
        m = re.match(r"^(#{1,6})\s+(.*)", l)
        if m:
            headings.append((i+1, len(m.group(1)), m.group(2)))
lv = Counter(h[1] for h in headings)
print(f"标题总数: {len(headings)}, 级别分布: {dict(sorted(lv.items()))}")
# 级别跳变（## 直接到 ####）
for idx in range(1, len(headings)):
    if headings[idx][1] > headings[idx-1][1] + 1:
        print(f"  级别跳变 L{headings[idx][0]}: {headings[idx-1][2]!r} -> {headings[idx][2]!r}")
# 小写开头标题（大小写不规范）
low = [(n,t) for n,_,t in headings if t and t[0].islower() and not t.startswith("(")]
print(f"小写开头标题 {len(low)} 个: {[t for t in low]}")
# 疑似裸编号标题（无正文名称）
bare = [(n,t) for n,_,t in headings if re.fullmatch(r"\d+(\.\d+)*\.?", t.strip())]
print(f"裸编号标题: {bare}")
# Chapter 标题检查
ch = [(n,t) for n,_,t in headings if t.startswith("Chapter")]
print(f"Chapter 标题: {ch}")
# 重复标题
cnt = Counter(t for _,_,t in headings)
dups = {t:c for t,c in cnt.items() if c > 1}
print(f"重复标题 {len(dups)} 个: { {t:c for t,c in list(dups.items())[:15]} }")

# ---------- 3. 目录完整性 ----------
report("3. 目录(TOC)完整性")
# 动态定位 TOC 区（从标题行到第一个非列表行）
toc_block = []
in_toc_blk = False
for l in lines:
    if l.strip() == "## Table of Contents":
        in_toc_blk = True
        continue
    if in_toc_blk:
        if l.strip() == "" or re.match(r"^\s*- \[", l):
            toc_block.append(l)
        else:
            break
toc_links = re.findall(r"\[([^\]]+)\]\(#([^)]+)\)", "\n".join(toc_block))
anchors = set(re.findall(r'<a id="([^"]+)">', text))
print(f"TOC 链接数: {len(toc_links)}, 显式锚点数: {len(anchors)}")
# GFM 自动锚点规则
def gfm_anchor(t):
    t = t.lower()
    t = re.sub(r"[^\w\s-]", "", t, flags=re.UNICODE)
    return re.sub(r"\s+", "-", t.strip())
heading_anchors = defaultdict(int)
for _,_,t in headings:
    heading_anchors[gfm_anchor(t)] += 1
broken = []
for txt, ref in toc_links:
    base = re.sub(r"-\d+$", "", ref)
    if ref not in heading_anchors and base not in heading_anchors and ref not in anchors:
        broken.append((txt, ref))
print(f"TOC 无法解析的链接 {len(broken)} 个:")
for t,r in broken: print(f"  [{t}](#{r})")
# TOC 与实际标题的覆盖差（去掉显示后缀后比对）
heading_texts = [t for _,_,t in headings if t not in ("Table of Contents",) and not t.startswith("Build a Large")]
in_toc = {re.sub(r" \(Exercise solutions\)$", "", txt) for txt,_ in toc_links}
missing = [t for t in heading_texts if t not in in_toc]
print(f"正文有但 TOC 缺失的标题 {len(missing)} 个:")
for t in missing: print(f"  - {t}")

# ---------- 4. PUA / 特殊字符 / HTML ----------
report("4. 残留特殊字符与 HTML")
pua = [(i+1, l) for i, l in enumerate(lines) if re.search(r"[\uE000-\uF8FF\uF000-\uF0FF]", l)]
print(f"含 PUA 字符的行: {len(pua)}")
for n,l in pua[:10]: print(f"  L{n}: {l[:80]}")
html = Counter()
for i, l in enumerate(lines):
    for tag in re.findall(r"</?[a-zA-Z]+[^>]*>", l):
        html[re.match(r"</?([a-zA-Z]+)", tag).group(1)] += 1
print(f"HTML 标签统计: {dict(html)}")
# 控制字符/异常 unicode
ctrl = [(i+1, ch) for i, l in enumerate(lines) for ch in l
        if unicodedata.category(ch).startswith("C") and ch not in "\t"]
print(f"控制字符: {len(ctrl)} 个: {Counter(c for _,c in ctrl)}")

# ---------- 5. 图片链接 ----------
report("5. 图片链接")
imgs = re.findall(r"!\[[^\]]*\]\(([^)]+)\)", text)
print(f"图片链接总数: {len(imgs)}")
missing_img = [p for p in imgs if not os.path.exists(p)]
print(f"图片文件缺失: {len(missing_img)} {missing_img[:10]}")
used = set(imgs)
all_png = []
for root in ("extracted_images/embedded", "extracted_images/figures"):
    if os.path.isdir(root):
        all_png += [os.path.join(root, f) for f in os.listdir(root)]
unused = [p for p in all_png if p not in used]
print(f"未被引用的图片: {len(unused)}")
for p in unused[:10]: print(f"  - {p}")

# ---------- 6. Figure/Listing/Table 标题格式 ----------
report("6. Figure/Listing/Table 标题")
fig_bold = len(re.findall(r"^\*\*Figure \d+\.\d+\*\*", text, re.M))
fig_plain = [(i+1,l) for i,l in enumerate(lines) if re.match(r"^Figure \d+\.\d+", l.strip())]
print(f"加粗 Figure 标题: {fig_bold}; 未加粗 Figure 行: {len(fig_plain)}")
for n,l in fig_plain[:10]: print(f"  L{n}: {l[:80]}")
lst_bold = len(re.findall(r"^\*\*Listing \d+\.\d+\*\*", text, re.M))
lst_plain = [(i+1,l) for i,l in enumerate(lines) if re.match(r"^Listing \d+\.\d+", l.strip())]
print(f"加粗 Listing: {lst_bold}; 未加粗 Listing 行: {len(lst_plain)}")
for n,l in lst_plain[:5]: print(f"  L{n}: {l[:80]}")

# ---------- 7. 引用块 / 列表 ----------
report("7. 引用块与列表")
quote = sum(1 for l in lines if l.startswith("> "))
print(f"引用块行数: {quote}")
bul = Counter(re.match(r"^(\s*)([-*+]) ", l).group(0) if re.match(r"^(\s*)([-*+]) ", l) else None for l in lines)
bul.pop(None, None)
print(f"列表符号用法: {dict(bul)}")

# ---------- 8. 断词 / 连字符 / 行断句 ----------
report("8. 断词与行内断句")
hyph_eol = [(i+1, l) for i, l in enumerate(lines) if re.search(r"\w-$", l)]
print(f"行尾连字符断词: {len(hyph_eol)}")
for n,l in hyph_eol[:10]: print(f"  L{n}: ...{l[-60:]}")
# 段内被空行拆开的嫌疑：以介词/冠词/连词结尾的非代码行
susp = [(i+1, l) for i, l in enumerate(lines)
        if re.search(r"\s(the|a|an|of|to|in|on|and|or|with|by|for|at|as|is|are)$", l.strip().lower())
        and not l.startswith((">", "```", "-", "#", "|", "!", " ")) and len(l) < 200]
print(f"疑似行中断句(以功能词结尾): {len(susp)}")
for n,l in susp[:15]: print(f"  L{n}: {l[-90:]}")

# ---------- 9. 内联代码反引号 ----------
report("9. 反引号平衡")
in_code = False; odd = []
for i, l in enumerate(lines):
    s = l.strip()
    if s.startswith("```"):
        in_code = not in_code; continue
    if not in_code and s.count("`") % 2 == 1:
        odd.append((i+1, l))
print(f"行内反引号数为奇数的行: {len(odd)}")
for n,l in odd[:10]: print(f"  L{n}: {l[:90]}")

# ---------- 10. 残留页眉页脚 / 重复行 ----------
report("10. 残留页眉/页脚/重复内容")
pat = Counter(l.strip() for l in lines if l.strip() and len(l.strip()) < 80)
rep = {k:v for k,v in pat.items() if v >= 4 and not re.match(r"^(\*\*|!|>|-|#|\||`)", k)}
print(f"重复≥4次的短行 {len(rep)} 种:")
for k,v in sorted(rep.items(), key=lambda x:-x[1])[:20]: print(f"  x{v}: {k!r}")

# ---------- 11. 空行问题 ----------
report("11. 空行与排版")
no_blank_head = [(headings[i][0], headings[i][2]) for i in range(len(headings))
                 if headings[i][0] < len(lines) and lines[headings[i][0]].strip() != ""
                 and not lines[headings[i][0]].startswith("#")]
print(f"标题后无空行: {len(no_blank_head)}")
three = sum(1 for i in range(len(lines)-2) if lines[i]=="" and lines[i+1]=="" and lines[i+2]=="")
print(f"连续3+空行处: {three}")

# ---------- 12. 已知缺失内容确认 ----------
report("12. 已知缺失内容验证")
print(f"出现 '5.4' 标题: {bool(re.search(r'^#{2,4} 5\.4', text, re.M))}")
print(f"出现 '7.4' 标题: {bool(re.search(r'^#{2,4} 7\.4', text, re.M))}")
print(f"出现 '1.6' 标题: {bool(re.search(r'^#{2,4} 1\.6', text, re.M))}")
print(f"'Weight sharing' 内容(4.5附近): {'weight sharing' in text.lower()}")

# ---------- 13. 数学公式 ----------
report("13. 数学表达式")
math_inline = len(re.findall(r"\$[^$\n]+\$", text))
print(f"$...$ 行内公式数: {math_inline}")
no_ascii_sub = re.findall(r"[a-z]\d{1,2}\s*[=×·]", text)
sup = re.findall(r"\^[^{\s]", text)
print(f"裸上标 ^ 出现 {len(sup)} 次(示例: {sup[:5]})")

print("\n审计完成。")
