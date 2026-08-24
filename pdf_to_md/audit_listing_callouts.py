#!/usr/bin/env python3
"""audit_listing_callouts.py — Listing 旁注 PDF↔MD 对账（离线只读）。

PDF 侧：extract+classify 重放，收集全部 listing_callout 组（callout_gid）
的完整短语与目标页码；
MD 侧：围栏掩码内做字母数字归一化包含性对账，分诊三类：
  MISSING      短语未出现在任何围栏内（提取/归位链路丢失）
  OUTSIDE      短语只出现在围栏外（散段落，未归位）
  MULTI        围栏内出现多次（重复注入）

用法：python audit_listing_callouts.py [md路径]（默认 llms-from-scratch.md）
退出码：存在 MISSING/OUTSIDE/MULTI 时为 1。
"""

import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mdlib import config
from mdlib.textutil import fence_mask
from pipeline.extract import extract_book
from pipeline.classify import (
    classify_pages,
    _annot_line_units,
    _group_callout_fragments,
)


def _calign(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower()) if s else ""


def main(md_path: Path = None) -> int:
    md_path = Path(md_path) if md_path else config.MD_PATH
    pages = extract_book(str(config.PDF_PATH))
    blocks = classify_pages(pages)

    # PDF 侧：组短语（callout_lines 行文本按组拼接，与 merge 同语义）
    groups: "dict[str, list]" = {}
    for b in blocks:
        if not b.meta.get("listing_callout"):
            continue
        for gid, items in (b.meta.get("callout_lines") or {}).items():
            groups.setdefault(gid, []).extend(t for t, _cy in items)
    phrases = []
    for gid, texts in groups.items():
        txt = " ".join(" ".join(t.split()) for t in texts if t.strip()).strip()
        page = int(gid.split(":")[0][1:]) if ":" in gid else -1
        if txt:
            phrases.append((gid, page, txt))

    # MD 侧：围栏行整行精确匹配（插入形制恒为 [缩进]# <短语>），
    # 子串匹配仅用于 OUTSIDE 分诊（散段落可能折行）
    text = md_path.read_text(encoding="utf-8")
    lines = text.split("\n")
    mask = fence_mask(lines)
    in_line_keys = Counter(_calign(l) for l, m in zip(lines, mask)
                           if m and l.strip())
    in_f = _calign("\n".join(l for l, m in zip(lines, mask) if m))
    out_f = _calign("\n".join(l for l, m in zip(lines, mask) if not m))

    # 同文短语合法重复：PDF 侧同名组数 = 围栏内整行出现次数才豁免
    key_counts_pdf = Counter(_calign(t) for _, _, t in phrases)

    n_missing = n_outside = n_multi = 0
    flagged = set()
    for gid, page, txt in phrases:
        key = _calign(txt)
        cin = in_line_keys.get(key, 0)
        status = None
        if cin == 0:
            if key not in in_f:
                status = "MISSING" if key not in out_f else "OUTSIDE"
                if status == "MISSING":
                    n_missing += 1
                else:
                    n_outside += 1
            # 围栏内以子串形态存在但无整行 → 视为注入形制漂移，计 MULTI 级告警
            else:
                n_multi += 1
                status = "SUBSTR"
        elif cin > key_counts_pdf[key] and (key,) not in flagged:
            flagged.add((key,))
            n_multi += 1
            status = "MULTI"
        if status:
            print(f"[{status}] p{page} {gid}: {txt[:60]}")
    print(f"\n[audit] 旁注组={len(phrases)}  正常={len(phrases)-n_missing-n_outside-n_multi}  "
          f"MISSING={n_missing}  OUTSIDE={n_outside}  MULTI/SUBSTR={n_multi}")

    # UNDERCOVER 分诊：全部 annot 单元组中未被标记配对者。
    #   STRAY  = 以围栏外散段存在于 MD（疑似漏收编）
    #   ABSENT = MD 全文无（多为图内标签被图区剔除，合法）
    stray = []
    n_absent = 0
    by_page: "dict[int, list]" = {}
    for b in blocks:
        by_page.setdefault(b.page, []).append(b)
    body_out = _calign("\n".join(l for l, m in zip(lines, mask) if not m))
    for page, pb in sorted(by_page.items()):
        for g in _group_callout_fragments(pb):
            if any(u[0].meta.get("listing_callout") for u in g):
                continue
            txt = " ".join(u[2] for u in g).strip()
            if not txt:
                continue
            key = _calign(txt)
            if not key:
                continue
            if key in body_out:
                stray.append((page, txt))
            elif key not in _calign("\n".join(
                    l for l, m in zip(lines, mask) if m)):
                n_absent += 1
    print(f"[audit] 未标记 annot 组={n_absent + len(stray)}  "
          f"(ABSENT 图内标签类={n_absent}  STRAY 散段={len(stray)})")
    for page, txt in stray[:15]:
        print(f"   [STRAY] p{page}: {txt[:70]!r}")

    ok = not (n_missing or n_outside or n_multi)
    return 0 if ok else 1


if __name__ == "__main__":
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    raise SystemExit(main(path))
