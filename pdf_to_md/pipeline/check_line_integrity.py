#!/usr/bin/env python3
"""断行完整性审计：找出"本应同行却被拆成多行"的候选位置。

两层方案：
  层 1（本文件 --scan）：静态规则扫描，输出候选清单（JSON 到 stdout 或文件）。
    R1 下行小写开头 + 上行无终止标点   R2 上行缩写点收尾
    R3 上行悬空 "-"（断词漏配）        R4 段内反引号奇数（代码跨度被切断）
    R5 Index 区非标准条目行（条目折行）
  层 2（--arbitrate）：PDF 流对齐仲裁——对每个候选取连接点两侧 token 窗口，
    在 PDF 全文 token 流中搜索拼接形态（允许页眉/页码噪声插入的间隙），
    找到即判"真·断行"，找不到判"误报/存疑"。

用法：
  python3 pipeline/check_line_integrity.py                # 层1+层2 全量审计
  python3 pipeline/check_line_integrity.py --scan-only    # 仅层1

只读报告，不修改任何文件。围栏内部为源书原样，不在检查范围。
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from mdlib.config import MD_PATH, PDF_PATH  # noqa: E402
from mdlib.textutil import fence_mask  # noqa: E402

# ---------------------------------------------------------------- 层 1 ----

ABBREV_RE = re.compile(
    r"\b(e\.g|i\.e|etc|vs|Fig|fig|Ch|ch|Sec|sec|Eq|eq|Eqn|al|No|Vol)\.$")
STRUCT_RE = re.compile(
    r"^(#{1,6} |\s*- |\s{0,4}- |>|<a id=|\||!\[|- \[|\d+\. |``` )")
INDEX_START = "## Index"
INDEX_END_PREFIX = "## Hands-on"
# Index 合法条目：可选缩进 "- " + 文本 + 页码（可多段逗号分隔/范围）
INDEX_ENTRY_RE = re.compile(r"^\s*-\ .+\d+(\D*\d+)*$")
SECTION_RE = re.compile(r"^###? ")
EMPHASIS_RE = re.compile(r"\*+|_+")


def _clean_tail(s):
    """剥掉行尾 markdown 修饰符后取终止字符。"""
    s = s.rstrip()
    s = EMPHASIS_RE.sub("", s).rstrip()
    return s


def _is_terminal(s):
    if ABBREV_RE.search(s):
        return False  # 缩写点不算终止（交给 R2 判定）
    return bool(s) and s[-1] in ".!?…:;)]”’\""


def scan(lines):
    mask = fence_mask(lines)
    # 定位 Index 区
    idx_s = next((i for i, l in enumerate(lines) if l.strip() == INDEX_START), None)
    idx_e = next((i for i, l in enumerate(lines)
                  if idx_s is not None and i > idx_s and l.startswith(INDEX_END_PREFIX)),
                 len(lines))
    in_index = [idx_s is not None and idx_s <= i < idx_e for i in range(len(lines))]

    cands = []

    def prose(i):
        return (not mask[i] and lines[i].strip()
                and not STRUCT_RE.match(lines[i])
                and not lines[i].lstrip().startswith("```")
                and not SECTION_RE.match(lines[i])
                and not in_index[i])

    def annotation_zone(i):
        """Manning 体例：图注/清单标题/代码围栏附近的短行对是合法边注，
        不是断行缺陷（如 'idx is a (batch...)'、'New tokenized sample'）。"""
        for k in range(max(0, i - 3), min(len(lines), i + 4)):
            s = lines[k].strip()
            if s.startswith(("```", "**Figure", "**Listing", "**Table", "![")):
                return True
        return False

    def next_nonblank(i):
        j = i + 1
        while j < len(lines) and not lines[j].strip():
            j += 1
        return j

    for i, l in enumerate(lines):
        if not l.strip() or mask[i]:
            continue
        # R5：Index 区条目完整性
        if in_index[i]:
            s = l.strip()
            if (s and not s.startswith("<a id=") and not SECTION_RE.match(s)
                    and not INDEX_ENTRY_RE.match(s)):
                # 分组词（无页码的父条目）合法：其下紧跟缩进子条目
                j2 = i + 1
                while j2 < len(lines) and not lines[j2].strip():
                    j2 += 1
                if not (j2 < len(lines) and lines[j2].startswith("  - ")):
                    cands.append({"line": i + 1, "rule": "R5",
                                  "cur": s[-45:], "nxt": ""})
            continue
        if not prose(i):
            continue
        j = next_nonblank(i)
        if j >= len(lines) or not prose(j):
            continue
        tail = _clean_tail(l)
        head = lines[j].strip()
        # 注释体例豁免：图注/清单/围栏邻近区不做断行判定
        if annotation_zone(i) or annotation_zone(j):
            continue
        # R6 scheme 后空格（含行内任意位置）
        m6 = re.search(r"https?://\s+[A-Za-z0-9]", l)
        if m6:
            cands.append({"line": i + 1, "rule": "R6",
                          "cur": l.strip()[:70], "nxt": ""})
        # R7 URL 跨段断裂：本行尾为 URL 片段，下段以续接形态开头
        if re.search(r"(https?://\S*|\S/|[a-z0-9]-)$", tail.rstrip()) \
                and re.match(r"^(-?[a-z0-9]|[a-z0-9]+[-./])", head) \
                and ("http" in tail or "/" in tail[-20:]):
            cands.append({"line": i + 1, "rule": "R7",
                          "cur": tail[-45:], "nxt": head[:45]})
            continue
        # R8 词内大写续接残留（xx- Xx）
        m8 = re.search(r"\b[a-z]{2,}- [A-Z][a-z]+", l)
        if m8:
            cands.append({"line": i + 1, "rule": "R8",
                          "cur": m8.group(0), "nxt": ""})
        # R3 悬空连字符
        if re.search(r"[A-Za-z]-$", tail):
            cands.append({"line": i + 1, "rule": "R3",
                          "cur": tail[-45:], "nxt": head[:45]})
            continue
        # R2 缩写点收尾
        if ABBREV_RE.search(tail) and re.match(r"^[a-z]", head):
            cands.append({"line": i + 1, "rule": "R2",
                          "cur": tail[-45:], "nxt": head[:45]})
            continue
        # R1 小写续行
        if (re.match(r"^[a-z]", head) and not _is_terminal(tail)):
            cands.append({"line": i + 1, "rule": "R1",
                          "cur": tail[-45:], "nxt": head[:45]})

    # R4 反引号奇数段（连续 prose 段落内计数）
    para = []
    for k in range(len(lines) + 1):
        ok = k < len(lines) and prose(k)
        if ok:
            para.append(k)
        else:
            if para:
                ticks = sum(lines[p].count("`") for p in para)
                if ticks % 2 == 1:
                    cands.append({"line": para[0] + 1, "rule": "R4",
                                  "cur": f"{len(para)} 行 {ticks} 个反引号",
                                  "nxt": ""})
            para = []
    return cands


# ---------------------------------------------------------------- 层 2 ----

_PUNCT = "\"'`“”‘’.,;:!?—–-*()[]{}<>|"


def norm_tok(t):
    t = t.lower().strip(_PUNCT)
    return t


def tokenize(text):
    out = []
    for raw in text.split():
        n = norm_tok(raw)
        if n:
            out.append(n)
    return out


def build_pdf_stream():
    import pymupdf
    doc = pymupdf.open(str(PDF_PATH))
    toks = []
    for page in doc:
        toks.extend(tokenize(page.get_text(sort=True)))
    return toks


def md_line_tokens(l):
    s = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", l)      # 图片
    s = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", s)  # 链接→文本
    s = s.replace("`", "")
    s = re.sub(r"<a id=\"[^\"]*\"></a>", "", s)
    return tokenize(s)


def arbitrate(cands, pdf_toks, md_lines, slack=15):
    """对每个候选在 pdf_toks 中验证连接处连续性，分两级：
      strict  : 上行末2 + 下行头2 共4 token 在 PDF 流中零间隙连续 → 高可信
      relaxed : 同窗口允许 ≤slack 个噪声 token（页眉/页码）→ 需人工复核
    返回 (verdict, gap)。"""
    from collections import defaultdict
    pos = defaultdict(list)
    for p, t in enumerate(pdf_toks):
        pos[t].append(p)

    results = []
    for c in cands:
        i = c["line"] - 1
        prev_toks = [t for t in md_line_tokens(md_lines[i])][-2:]
        j = i + 1
        while j < len(md_lines) and not md_lines[j].strip():
            j += 1
        nxt_toks = md_line_tokens(md_lines[j])[:2] if j < len(md_lines) else []
        verdict, gap = "unconfirmed", -1
        if len(prev_toks) == 2 and len(nxt_toks) == 2:
            best = None
            for p in pos.get(prev_toks[0], []):
                if p + 1 >= len(pdf_toks) or pdf_toks[p + 1] != prev_toks[1]:
                    continue
                # strict：紧跟
                if (p + 3 < len(pdf_toks)
                        and pdf_toks[p + 2] == nxt_toks[0]
                        and pdf_toks[p + 3] == nxt_toks[1]):
                    best = ("strict", 0)
                    break
                # relaxed：允许噪声间隙
                q, g = p + 2, 0
                while q < len(pdf_toks) and g <= slack:
                    if (pdf_toks[q] == nxt_toks[0]
                            and q + 1 < len(pdf_toks)
                            and pdf_toks[q + 1] == nxt_toks[1]):
                        best = ("relaxed", g)
                        break
                    g += 1
                    q += 1
                if best:
                    break
            if best:
                verdict, gap = f"CONFIRMED-{best[0]}", best[1]
        results.append({**c, "verdict": verdict, "gap": gap})
    return results


# ---------------------------------------------------------------- V 校验 ----

def url_continuity_audit(lines, pdf_toks=None):
    """V 校验：列出全部围栏外 URL 及其行内紧随片段，供人工复核
    空格损伤/伪造链接。自动判定不可靠（自然语言续词与断链形态难分），
    故仅聚合展示。返回 [(行号, URL区文本)]。"""
    import pymupdf

    def nz(s):
        return re.sub(r"[^a-z0-9]", "", s.lower())

    doc = pymupdf.open(str(PDF_PATH))
    big = nz("".join(pg.get_text() for pg in doc))

    inf = [False] * len(lines)
    f = False
    for i, l in enumerate(lines):
        if l.strip().startswith("```"):
            f = not f
        else:
            inf[i] = f

    review = []
    for i, l in enumerate(lines):
        if inf[i] or "http" not in l:
            continue
        for m in re.finditer(r"https?://\S+", l):
            base = m.group(0).rstrip(".,;:)")
            tail_words = l[m.end():].split(maxsplit=2)
            # 自动初筛：URL 本体（归一化）能在 PDF 中找到 -> 干净，不列
            if nz(base) in big:
                continue
            ctx = l.strip()
            k = ctx.find(base[:30])
            review.append((i + 1, ctx[max(0, k - 10):k + 80]))
    return review


# ---------------------------------------------------------------- main ----

def main():
    lines = MD_PATH.read_text(encoding="utf-8").split("\n")
    cands = scan(lines)
    print(f"[层1] 规则候选: {len(cands)} 条")
    by_rule = {}
    for c in cands:
        by_rule.setdefault(c["rule"], []).append(c)
    for r in sorted(by_rule):
        print(f"  {r}: {len(by_rule[r])}")

    if "--scan-only" in sys.argv:
        print(json.dumps(cands, ensure_ascii=False, indent=1))
        return

    print("[层2] PDF 流对齐仲裁 ...")
    pdf_toks = build_pdf_stream()
    print(f"  PDF token 流: {len(pdf_toks)}")
    results = arbitrate(cands, pdf_toks, lines)
    strict = [r for r in results if r["verdict"] == "CONFIRMED-strict"]
    relax = [r for r in results if r["verdict"] == "CONFIRMED-relaxed"]
    print(f"\n[结论] strict 确认: {len(strict)} | relaxed(需人工): {len(relax)}"
          f" | 未确认: {len(results) - len(strict) - len(relax)}")
    print("\n== strict（高可信真断行）==")
    for r in sorted(strict, key=lambda x: x["line"]):
        gap = f" gap={r['gap']}" if r["gap"] else ""
        print(f"  L{r['line']} [{r['rule']}{gap}] {r['cur']} ⏎ {r['nxt']}")
    print("\n== relaxed（跨页眉匹配，需人工）==")
    for r in sorted(relax, key=lambda x: x["line"]):
        print(f"  L{r['line']} [{r['rule']} gap={r['gap']}] {r['cur']} ⏎ {r['nxt']}")

    # V 校验：URL 复核清单（含自动初筛未命中者）
    review = url_continuity_audit(lines, pdf_toks)
    print(f"\n[V] URL 人工复核清单: {len(review)} 条")
    for ln, s in review:
        print(f"  L{ln}: {s}")


if __name__ == "__main__":
    main()
