#!/usr/bin/env python3
"""pipeline.md_lint — 渲染语义 Linter（详细规则见 attachments/md-lint.md）。

排查"PDF 结构转成 Markdown 后渲染语义走样"类缺陷，与 §3 全局断言互补：
- R1 图片行硬换行：`![..](..)` 后紧跟非空行时必须以两个空格结尾（GFM 硬换行），
  否则图片与下一行（图注/正文）在 CommonMark 下并段渲染。
- R2 行尾规范：禁止 CR/CRLF（外部编辑器/git autocrlf 污染会破坏硬换行语义）。
- R3 标题邻接：ATX 标题前一行必须为空行（`<a id>` 锚点行豁免），否则不解析为标题。
- R4 表格起始邻接：表格首行前必须为空行，否则 GFM 不解析表格。
- R5 杂散尾随空白：非图片行的尾随空格（≥2 即意外硬换行）；图片行允许恰两个。
- R6 强调符号失衡：围栏外、行内代码外单行 `*` 计数为奇数（疑似意外斜体/残留星号）。
- R7 图文配对：`![Fig X.Y]` 与斜体图注 `*Figure X.Y*`（兼容旧加粗）双向配对；数量不等=ERROR，
  配对距离超过窗口=WARN（图注被隔断的信号）。
- R8 连续空行 >2：版式噪声。
- R9 文件尾卫生：缺末尾换行或文件尾多余空行。
- R10 manifest 对账：extracted_images/manifest.json 的每个 figure 必须在 MD
  以图链或图注出现（防整图静默消失，见 attachments/extract-robustness.md）。

基线机制：存量可容忍问题记入 golden/baseline_md_lint.txt（每行 `RULE|detail`），
默认只对"新增"违规报错退出码 1；--update-baseline 重写基线。

用法：
  python pipeline/md_lint.py [md路径]            # 对照基线检查
  python pipeline/md_lint.py [md路径] --update-baseline
  python -c "from pipeline.md_lint import lint_text; ..."   # 程序内调用
"""

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mdlib import config

BASELINE_PATH = ROOT / "golden" / "baseline_md_lint.txt"

IMG_LINE_RE = re.compile(r"^!\[[^\]]*\]\([^)]+\)\s*$")
IMG_FIG_RE = re.compile(r"^!\[Fig ([\w.]+)\]\(")
CAPTION_RE = re.compile(r"^\*{1,2}Figure ([\w.]+)\*{1,2}")
HEADING_RE = re.compile(r"#{1,6} ")
TABLE_ROW_RE = re.compile(r"^\|")
ANCHOR_RE = re.compile(r'^<a id="[^"]+"></a>$')
PAIR_WINDOW = 10  # 图↔注最大行距（实测最大间隔 3 行）

# detail 截断长度：保证基线条目稳定且可读
_DETAIL_MAX = 60


def _iter_visible(lines):
    """yield (idx, line)，跳过围栏内的行（围栏行本身仍 yield）。
    兼容 GFM 引用内围栏："> ```" 行同样切换状态。"""
    in_fence = False
    for i, line in enumerate(lines):
        yield i, line, in_fence
        s = line.lstrip()
        if s.startswith(">"):
            s = s[1:].lstrip()
        if s.startswith("```"):
            in_fence = not in_fence


def _strip_inline_code(line: str) -> str:
    return re.sub(r"`[^`]*`", "", line)


def lint_text(text: str) -> list:
    """返回 findings 列表：[(rule, lineno(1-based), detail)]。"""
    # R2 在原始文本层检查（split 会吃掉行尾信息）
    findings = []
    if "\r" in text:
        n_cr = text.count("\r\n") or text.count("\r")
        findings.append(("R2", 0, f"CRLF/CR x{n_cr}"))

    lines = text.split("\n")
    n = len(lines)

    img_positions = {}       # fig_id -> 最近一次图片行号
    cap_positions = {}       # fig_id -> 图注行号列表

    for i, line, in_fence in _iter_visible(lines):
        ln = i + 1
        nxt = lines[i + 1] if i + 1 < n else ""
        # CommonMark 邻接判定看紧邻上一行（不是最近非空行）
        prev_line = lines[i - 1] if i else ""

        # R1 图片行硬换行
        if IMG_LINE_RE.match(line):
            if nxt.strip() and not line.endswith("  "):
                m = IMG_FIG_RE.match(line)
                detail = m.group(1) if m else line[:_DETAIL_MAX]
                findings.append(("R1", ln, f"{detail} 无两空格硬换行"))
            elif line.endswith("   "):
                findings.append(("R1", ln, "尾随空格>2"))

        if in_fence:
            continue

        stripped = line.strip()

        # R3 标题邻接（锚点行豁免）
        if HEADING_RE.match(stripped) and prev_line.strip() \
                and not ANCHOR_RE.match(prev_line.strip()):
            findings.append(("R3", ln, f"前一行非空: {prev_line.strip()[:30]}"))

        # R4 表格起始邻接（表内续行豁免）
        if TABLE_ROW_RE.match(stripped) and prev_line.strip() \
                and not TABLE_ROW_RE.match(prev_line.strip()):
            findings.append(("R4", ln, f"首行前非空: {prev_line.strip()[:30]}"))

        # R5 杂散尾随空白（图片行已由 R1 管辖）
        if not IMG_LINE_RE.match(line) and line != line.rstrip() \
                and len(line) - len(line.rstrip()) >= 2:
            findings.append(("R5", ln, f"...{line.rstrip()[-25:]!r}"))

        # R6 强调符号失衡
        body = _strip_inline_code(line)
        if body.count("*") % 2 == 1:
            findings.append(("R6", ln, body.strip()[:_DETAIL_MAX]))

        # R7 图文配对登记
        m_img, m_cap = IMG_FIG_RE.match(line), CAPTION_RE.match(line)
        if m_img:
            img_positions[m_img.group(1)] = ln
        if m_cap:
            cap_positions.setdefault(m_cap.group(1), []).append(ln)

    # R7 对账：数量与归属
    img_ids, cap_ids = set(img_positions), set(cap_positions)
    for fid in sorted(img_ids - cap_ids):
        findings.append(("R7", img_positions[fid], f"Fig {fid} 有图无注"))
    for fid in sorted(cap_ids - img_ids):
        findings.append(("R7", cap_positions[fid][0], f"Fig {fid} 有注无图"))
    for fid in sorted(img_ids & cap_ids):
        dist = min(c - img_positions[fid] for c in cap_positions[fid])
        if dist > PAIR_WINDOW:
            findings.append(("R7", img_positions[fid],
                             f"Fig {fid} 图注相距 {dist} 行"))

    # R8 连续空行 >2
    blank_run = 0
    for i, line, in_fence in _iter_visible(lines):
        blank_run = 0 if line.strip() else blank_run + 1
        if blank_run == 3:
            findings.append(("R8", i + 1, "连续空行>=3"))

    # R9 文件尾卫生
    if lines and lines[-1] == "" and len(lines) >= 2 and lines[-2] == "":
        findings.append(("R9", n, "文件尾多空行"))
    if not text.endswith("\n"):
        findings.append(("R9", n, "缺末尾换行"))

    # R11 框内代码三明治：裸围栏前后最近非空行均为引用行 → 概念框被代码
    # 切断的断裂形态（应合成复合框输出 "> ```" 引用内围栏）。WARN 级。
    for i, line, in_f in _iter_visible(lines):
        if in_f or not line.strip().startswith("```") or line.lstrip().startswith(">"):
            continue

        def _bare(s):
            s = s.lstrip()
            return s[1:].lstrip() if s.startswith(">") else s

        p = i - 1
        while p >= 0 and not lines[p].strip():
            p -= 1
        j = i + 1
        while j < n and not _bare(lines[j]).startswith("```"):
            j += 1
        j += 1
        while j < n and not lines[j].strip():
            j += 1
        if (p >= 0 and lines[p].lstrip().startswith(">")) \
                and (j < n and lines[j].lstrip().startswith(">")):
            findings.append(("R11", i + 1, "引用-围栏-引用三明治（框内代码未合成）"))


    # R10 manifest 对账：每个 manifest figure 必须在 MD 中出现（图链或图注）。
    # 防"clip 过度生长 + 块级剔除"导致整图静默消失（Fig6.5/Fig7.11 事故）。
    try:
        data = json.loads(config.MANIFEST_PATH.read_text(encoding="utf-8"))
        md_ids = set(img_positions) | set(cap_positions)
        for entry in data.get("figures", []):
            fid = str(entry.get("fig", ""))
            if fid and fid not in md_ids:
                findings.append(("R10", 0, f"manifest Fig {fid} 未出现在 MD"))
    except Exception:
        pass
    # R10b 附录 E 图：不在 manifest 内（patches 渲染），单独核对 E.1–E.5。
    # 图链与图注同灭时 R7 配对仍"一致"，必须独立锚定 PDF 侧事实。
    try:
        import pymupdf
    except ImportError:
        pymupdf = None
    if pymupdf is not None:
        try:
            from pipeline.patches import _find_captions
            doc = pymupdf.open(str(config.PDF_PATH))
            appendix_tags = {t for _p, t, _c in _find_captions(doc)}
            doc.close()
            for tag in sorted(appendix_tags):
                if tag not in md_ids:
                    findings.append(("R10", 0, f"附录 {tag} 未出现在 MD"))
        except Exception:
            pass

    return findings


def load_baseline(path: Path = BASELINE_PATH) -> set:
    if not path.exists():
        return set()
    out = set()
    for raw in path.read_text(encoding="utf-8").splitlines():
        raw = raw.strip()
        if raw and not raw.startswith("#"):
            out.add(raw)
    return out


def fmt_findings(findings: list) -> set:
    """findings → 基线条目集合 `RULE|detail`（不含行号：跨次生成行号必漂移）。"""
    return {f"{rule}|{detail}" for rule, _, detail in findings}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("md", nargs="?", default=str(config.MD_PATH))
    ap.add_argument("--update-baseline", action="store_true",
                    help="以当前结果重写基线后退出")
    args = ap.parse_args(argv)

    text = Path(args.md).read_text(encoding="utf-8")
    findings = lint_text(text)

    if args.update_baseline:
        entries = sorted(fmt_findings(findings))
        BASELINE_PATH.write_text(
            "# md_lint 基线：存量可容忍违规（RULE|detail）。由"
            " --update-baseline 生成。\n" + "\n".join(entries) +
            ("\n" if entries else ""), encoding="utf-8")
        print(f"[md_lint] 基线已更新：{len(entries)} 条 -> {BASELINE_PATH}")
        return 0

    known = load_baseline()
    current = fmt_findings(findings)
    new = sorted(current - known)

    by_rule = {}
    for rule, _, _ in findings:
        by_rule[rule] = by_rule.get(rule, 0) + 1
    print(f"[md_lint] 违规 {len(findings)} 处 {by_rule} | "
          f"基线 {len(known)} 条 | 新增 {len(new)} 条")

    for entry in new:
        rule = entry.split("|", 1)[0]
        for r, ln, detail in findings:
            if f"{r}|{detail}" == entry:
                print(f"  NEW {r}:L{ln}: {detail}")
                break
    return 1 if new else 0


if __name__ == "__main__":
    raise SystemExit(main())
