"""pipeline.ir — 管道中间表示（数据结构，照抄方案）。"""

from dataclasses import dataclass, field

KINDS = {"prose", "heading", "code", "callout", "note", "exercise",
         "listing_caption", "figure_caption", "bullet", "concept_box",
         "table", "index", "cover"}

# 同矩形概念框碎片归组（classify._tag_box_membership 打 box_key，
# merge.merge_box_fragments 合并）：落入同一 CALLOUT_FILL 矩形的相邻块
# 属于同一视觉容器，允许入组的 kind；heading/code/图注出现即打断归组。
BOX_GROUP_KINDS = {"concept_box", "callout", "prose", "exercise", "note"}
BOX_OVERLAP = 0.20  # 与 classify._is_concept_box 同源的重叠阈值


@dataclass
class Block:
    kind: str                 # 取值必须在 KINDS 内
    text: str                 # 原始文本（可多行，不含 markdown 标记）
    page: int                 # PDF 页号（0 基）
    bbox: tuple = (0, 0, 0, 0)  # (x0,y0,x1,y1)
    level: int = 0            # heading: 1=##, 2=###, 3=####
    lang: str = ""            # code: python 等，可为空
    meta: dict = field(default_factory=dict)  # 附加信号（字体、颜色等）


@dataclass
class Page:
    index: int
    height: float
    blocks: list = field(default_factory=list)
