"""pipeline.index — 索引区截断（自 Index 起不转换入 MD）。

历史（见 attachments/format-fix-round2.md 子方案 2）：
  索引页原为三栏版式，曾以多栏重排重建为 `## Index` 列表。
  现按用户决策改为整段截断：书末 Index 及其后内容（含封底
  liveProjects 页残段）一律不进入 Markdown。

截断点为结构信号：`index` 标题独立行（PDF TOC 对 "Index" 的正文落点，
与旧 replace_region 同一探测逻辑），或重跑场景下已存在的 `## Index`。
幂等可重跑：截断后再跑找不到标记行，原样返回。
"""

CUT_MARKS = ("## Index", "index")


def drop_index(lines: list) -> tuple[list, int]:
    """删除从索引标题行起到文件末尾的全部行。返回 (新行列表, 是否截断)。"""
    s = next((i for i, l in enumerate(lines)
              if l.strip() in CUT_MARKS and i > len(lines) * 0.9), None)
    if s is None:
        print("[pipeline.index] 未找到索引标记行（已截断或无索引），跳过")
        return lines, 0
    # 吃掉标题行前的锚点行（TOC 已注入的重跑场景）
    if s > 0 and lines[s - 1].strip().startswith("<a id="):
        s -= 1
    lines = list(lines[:s])
    print(f"[pipeline.index] 自索引标题起截断 {len(lines)} 行后全部内容")
    return lines, 1
