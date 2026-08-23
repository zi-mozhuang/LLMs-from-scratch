"""pipeline.manifest_enrich — 图片 manifest 元数据增强（吸收自架构设计文档）。

为 extracted_images/manifest.json 的每个图片条目补充：
- source : "rendered"（figures/ 区域渲染图）| "embedded"（embedded/ 原生位图）
- bytes  : 文件字节数
- md5    : 文件内容哈希（跨路径去重依据）
- caption: 来自最终 MD 的图注文本（![Fig X.Y] 链接后的斜体 *Figure X.Y* 行，兼容旧加粗形态）

原则：只增不改既有键；无变化不落盘（幂等）；不做任何删除/过滤
（装饰图标等由 verify 的体积检查报告，不在提取层静默丢弃）。
"""

import hashlib
import json


def _captions_from_md(md_text: str) -> dict:
    """从最终 MD 提取 fig_id → caption 映射。

    渲染约定：图片链接行 `![Fig X.Y](...)` 的下一个非空行是加粗图注
    `**Figure X.Y** ...`（bold_captions 产物）。
    """
    caps = {}
    lines = md_text.split("\n")
    for i, line in enumerate(lines):
        s = line.strip()
        if not (s.startswith("![Fig ") and "](" in s):
            continue
        tag = s[2:].split("]")[0]  # "Fig X.Y"
        fig_id = tag[len("Fig "):]
        # 向下找第一个非空行作为图注
        for j in range(i + 1, min(i + 4, len(lines))):
            nxt = lines[j].strip()
            if not nxt:
                continue
            if nxt.startswith(("*Figure ", "**Figure ")) and "*" in nxt[1:]:
                caps[fig_id] = nxt
            break
    return caps


def enrich_manifest(manifest_path, md_text: str) -> bool:
    """增强 manifest 条目。返回是否写盘。"""
    path = __import__("pathlib").Path(manifest_path)
    data = json.loads(path.read_text(encoding="utf-8"))
    base = path.parent
    caps = _captions_from_md(md_text)

    changed = False
    for key in ("figures", "embedded"):
        for entry in data.get(key, []):
            rel = entry.get("file")
            if not rel:
                continue
            fpath = base / rel
            # 来源：目录即 provenance
            src = "rendered" if rel.startswith("figures/") else "embedded"
            if entry.get("source") != src:
                entry["source"] = src
                changed = True
            if fpath.exists():
                raw = fpath.read_bytes()
                digest = hashlib.md5(raw).hexdigest()
                if entry.get("md5") != digest:
                    entry["md5"] = digest
                    changed = True
                if entry.get("bytes") != len(raw):
                    entry["bytes"] = len(raw)
                    changed = True
            # 图注仅对 figures 条目有意义
            if key == "figures":
                fid = entry.get("fig", "")
                cap = caps.get(fid)
                if cap and entry.get("caption") != cap:
                    entry["caption"] = cap
                    changed = True

    if changed:
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False),
                        encoding="utf-8")
    return changed
