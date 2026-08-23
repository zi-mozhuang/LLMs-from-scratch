#!/usr/bin/env python3
"""audit_promise_chains — 引导语承诺链完整性审计（只读）。

背景（pdf-to-md-plan.md「截图式代码图重建」）：书版截图式排版把代码只画进
矢量图，正文引导语（以 ":" 结尾）承诺的代码在文本层缺失。初版扫描
"冒号→图链→有后续即判完整"存在判据缺陷——输出 tensor 在场会掩护缺失的
代码（Fig 3.12 事故：z(2): 承诺代码，输出被误判为兑现）。

修正判据：冒号引导语的**下一非空行必须是代码围栏**——
  ``` 围栏            -> PASS（承诺兑现）
  另一冒号段落         -> 链式引导，继续看最终兑现（全链无围栏仍 FLAG）
  图链 / 输出 / 其它   -> FLAG（承诺未兑现；人工分诊是否书版本意）

已知合法例外（书版本意，图即承诺物）："as illustrated in figure X.Y:" 类
引导图示的句子——审计报告中按图链关键词辅助标注，人工分诊。

用法：python audit_promise_chains.py [--md PATH]
退出码：存在 FLAG -> 1（供 CI/人工复核），否则 0。
"""

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MD_PATH = ROOT / "llms-from-scratch.md"

_COLON_END = re.compile(r":\s*$")
_FENCE = re.compile(r"^\s*```")
_IMG = re.compile(r"^!\[")
_QUOTE = re.compile(r"^\s*>")
_LIST = re.compile(r"^\s*[-*+] |\s*\d+\. ")
_HEADING = re.compile(r"^#{1,6} ")
_URL = re.compile(r"https?://|mng\.bz|arxiv\.org|github\.com", re.I)
# 图示类引导关键词（图即承诺物，合法例外候选）
_FIGURE_WORDS = re.compile(
    r"(illustrated|shown|depicted|visualization|diagram|steps?"
    r"|figure \d+\.\d+)", re.I)


def _classify_next(nxt: str) -> str | None:
    """下一非空行的类目；返回 None 表示可兑现/免检。
    过滤已知非代码承诺形态（附录引文、输出标签、列表、URL 行）。"""
    if _FENCE.match(nxt):
        return None
    if _URL.search(nxt):
        return None                      # 参考文献条目（URL 承接）
    if _LIST.match(nxt):
        return None                      # 列表兑现
    if len(nxt) < 35 and not nxt.endswith((".", "!", "?")):
        return None                      # 输出标签行（Score: 类短行承接）
    return nxt


def scan(lines: list) -> dict:
    """返回 {"promises": N, "flags": [(lineno, kind, context)]}。"""
    flags = []
    n_promises = 0
    i = 0
    n = len(lines)
    in_fence = False
    while i < n:
        line = lines[i]
        if _FENCE.match(line):
            in_fence = not in_fence
            i += 1
            continue
        if in_fence or not line.strip() or _HEADING.match(line) \
                or _QUOTE.match(line) or _LIST.match(line) or _IMG.match(line):
            i += 1
            continue
        if not _COLON_END.search(line):
            i += 1
            continue
        # 冒号引导语：找下一非空行
        n_promises += 1
        # (a) 短冒号行 = 输出标签（"Score:"/"Dataset response:"），免检
        if len(line.strip()) < 35:
            i += 1
            continue
        j = i + 1
        while j < n and not lines[j].strip():
            j += 1
        if j >= n:
            i += 1
            continue
        nxt = lines[j].strip()
        if _FENCE.match(lines[j]):
            i += 1
            continue  # 兑现
        if _IMG.match(lines[j]):
            flags.append((i + 1, "FIG", line.strip()[-55:]))
            i += 1
            continue
        if _QUOTE.match(lines[j]):
            i += 1
            continue  # 引用块承接（token 说明/引文/手算步骤），免检
        if _classify_next(nxt) is None:
            i += 1
            continue  # 列表/URL 行承接，免检
        # (c) 旁注插队容忍：4 个非空行窗口内出现围栏即兑现
        k = j
        seen = 0
        while k < n and seen < 4:
            if lines[k].strip():
                if _FENCE.match(lines[k]):
                    break
                seen += 1
            k += 1
        if k < n and _FENCE.match(lines[k]):
            i += 1
            continue
        if _COLON_END.search(nxt):
            flags.append((i + 1, "CHAIN", line.strip()[-55:]))
            i += 1
            continue
        flags.append((i + 1, "TEXT", line.strip()[-55:]))
        i += 1
    return {"promises": n_promises, "flags": flags}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--md", default=str(MD_PATH))
    args = ap.parse_args()
    lines = Path(args.md).read_text(encoding="utf-8").split("\n")
    rep = scan(lines)
    print(f"[promise-audit] 引导语={rep['promises']}  未兑现={len(rep['flags'])}")
    for ln, kind, ctx in rep["flags"]:
        hint = "图示类(合法候选)" if _FIGURE_WORDS.search(ctx) else "代码类(疑似缺失)"
        print(f"  L{ln} [{kind}/{hint}] ...{ctx}")
    return 1 if rep["flags"] else 0


if __name__ == "__main__":
    sys.exit(main())
