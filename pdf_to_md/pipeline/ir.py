"""pipeline.ir — 管道中间表示（数据结构，照抄方案）。"""

from dataclasses import dataclass, field

KINDS = {"prose", "heading", "code", "callout", "note", "exercise",
         "listing_caption", "figure_caption", "bullet", "concept_box",
         "table", "index", "cover"}


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
