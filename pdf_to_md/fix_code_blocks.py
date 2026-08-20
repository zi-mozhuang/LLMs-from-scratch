"""Restore fenced code blocks in pymupdf4llm markdown output.

Pass 1: convert PDF with page_chunks=True.
Pass 2: re-extract code blocks from the PDF by font (Courier*) with proper
        line breaks/indentation; replace merged single-line code in the md
        with fenced code blocks via span-precise stream matching per page.
        Margin annotations (bold-italic) become blockquotes after the code.
Matching normalizes both sides by stripping whitespace and backslashes
(pymupdf4llm may or may not backslash-escape; normalization is safe either way).

Pass 3 (add_toc): promote the plain-text chapter/appendix titles that
pymupdf4llm did not emit as headings (ch 1/4/6, appendix B/D/E), give every
TOC-target heading a cleaned text plus an explicit `<a id="slug">` anchor, and
replace the mangled `Brief Contents` / `Contents` tables with a real nested
Markdown TOC whose links jump to those anchors. This pass runs LAST so it
cannot disturb any of the earlier code-block / math / prose fixes.
"""
import re
import pathlib
import numpy as np
from PIL import Image
import pymupdf
import pymupdf4llm

BASE = pathlib.Path(__file__).parent
PDF = BASE / "Build a Large Language Model (From Scratch) (Sebastian Raschka) (z-library.sk, 1lib.sk, z-lib.sk).pdf"
OUT_DIR = BASE / "output"
IMG_DIR = OUT_DIR / "images"
MD = OUT_DIR / "Build-a-LLM-from-scratch.md"

STRIP_RE = re.compile(r"[\s\\]+")


def is_code_font(font: str) -> bool:
    return font.startswith("Courier")


def norm(s: str) -> str:
    return STRIP_RE.sub("", s)


def strip_with_map(line: str):
    """Remove whitespace/backslashes; return (stripped, map stripped_idx->orig_idx)."""
    out, mp = [], []
    for i, ch in enumerate(line):
        if ch.isspace() or ch == "\\":
            continue
        out.append(ch)
        mp.append(i)
    return "".join(out), mp


def block_keys(lines):
    """Anchor keys: prefer the block's first lines (least interleaving before them),
    plus the longest line as a fallback."""
    keys = []
    for l in lines[:6]:
        nl = norm(l)
        if len(nl) >= 8:
            keys.append(nl[:12])
        if len(keys) == 2:
            break
    longest = max((norm(l) for l in lines), key=len, default="")
    if len(longest) >= 8 and longest[:12] not in keys:
        keys.append(longest[:12])
    return keys


def extract_notes(text: str):
    """Margin annotations appear as bold (**...**) segments interleaved in the
    merged md code line. Filter out false positives like power operators."""
    notes = []
    for m in re.finditer(r"\*\*([^*]+)\*\*", text):
        n = m.group(1).strip()
        if len(n) >= 15 and sum(c.isalpha() for c in n) >= 8:
            notes.append(n)
    return notes


def make_replacement(blk, notes):
    first = blk["lines"][0].strip() if blk["lines"] else ""
    lang = "bash" if first.startswith("$") else "python"
    parts = [f"```{lang}\n" + "\n".join(blk["lines"]) + "\n```"]
    parts += [f"> {n}" for n in notes]
    return "\n\n".join(parts)


def try_match(blk, hay, start=0, protected=None):
    """Locate block's flat text inside hay via an anchor key, then align the
    span by backward-matching the needle prefix and forward-matching the tail.
    Every occurrence of every key is tried; a candidate whose span crosses a
    protected position (a figure caption or picture-text line) is rejected and
    the next occurrence is tried. This matters because a key fragment can also
    appear earlier inside a caption's picture-text block (e.g. `Attention
    weights:`), and anchoring there would let the fuzzy tail swallow the
    caption line between that spot and the real code (eaten Figure 3.9)."""
    f = blk["flat"]
    for key in blk["keys"]:
        prefix_len = f.find(key)
        pos = start
        while True:
            pos = hay.find(key, pos)
            if pos == -1:
                break
            lb = max(start, pos - 2 * prefix_len - 100)
            # backward: latest start so needle[:prefix_len] is a subsequence of hay[.:pos]
            j, failed = pos, False
            for ch in reversed(f[:prefix_len]):
                k = hay.rfind(ch, lb, j)
                if k == -1:
                    failed = True
                    break
                j = k
            if not failed:
                span_start = j
                # forward: earliest end covering needle tail
                i, matched_tail = pos + len(key), 0
                for ch in f[prefix_len + len(key):]:
                    k = hay.find(ch, i)
                    if k == -1:
                        break
                    matched_tail += 1
                    i = k + 1
                total = prefix_len + len(key) + matched_tail
                if (total / len(f) >= 0.95
                        and (protected is None
                             or not any(protected(p) for p in range(span_start, i)))):
                    return span_start, i
            pos += len(key)
    return None


# ---------- figure captions ----------
# pymupdf4llm emits each "Figure X.Y ..." caption immediately after its figure
# image on the same page. Only the caption line is matched here; the caption is
# normalized to a blockquote in place (no re-pairing by image index — global
# pairing mis-pairs because front-matter images like the cover / author portrait
# have no captions).
_CAP_RE = re.compile(r"^Figure\s+(\d+\.\d+)\b\s*(.*)$")


# ============================================================================
# ⚠️ DO NOT REMOVE — 对应 PDF报告缺陷 #7（图注格式 / 图注内部硬换行）。
#    本函数把图注规范化为 blockquote（`> **Figure X.Y** ...`）。
#    注意：不得做"全局第 N 图注→第 N 图"配对 —— 封面图、作者肖像等无图注图片
#    会令序号错位（实测曾把 Figure 1.4 图注挂到作者肖像图下）。转换器输出中
#    图注本就紧跟其图片（同页相邻），因此原地转 blockquote 即可。
# ============================================================================
def pair_figures_captions(text: str) -> str:
    """Normalize figure captions to Markdown blockquotes in place. The converter
    emits each `Figure X.Y ...` caption immediately after its figure image on
    the same page, so captions are left where they are — no re-pairing by image
    index (front-matter images have no captions and would shift the pairing).
    """
    out = []
    for l in text.split("\n"):
        m = _CAP_RE.match(l)
        if m:
            out.append(f"> **Figure {m.group(1)}** {m.group(2).strip()}")
        else:
            out.append(l)
    return "\n".join(out)


# ============================================================================
# ⚠️ DO NOT REMOVE — 对应 PDF报告缺陷 #8(图片被拆分成多块)。pymupdf4llm 常把一页上
#    由多个对象/矢量内容拼成的 Figure（如 ChatGPT 截图的多个面板）切成多张碎片图，
#    导致"完整图片被拆分"。本函数两种策略合并：
#      (A) 若页面存在 PDF 栅格图片对象（如第27页 Figure 1.2），直接用图片对象 union
#          bbox，并向右扩展纳入紧邻右侧的文字标注块（如"User input"/"Model output"），
#          从整页渲染图裁剪——这样图片区域精确，不会吞掉下方正文，也不会丢右侧标注。
#      (B) 若页面无栅格图片对象（纯矢量图，如第50、74页），用 NCC 模板匹配定位
#          pymupdf4llm 碎片在整页渲染图中的位置，取并集 bbox 裁剪。
#    第0页（封面）由 extract_full_cover_image 单独处理，这里跳过。
# ============================================================================
_IMG_RE = re.compile(r"!\[([^\]]*)\]\((images/[^)]*?-00(\d+)-(\d+)\.png)\)")


def _ncc_match(page_pix, frag_path):
    """Return (x, y, w, h) of ``frag_path`` inside ``page_pix`` using
    normalized cross-correlation via numpy FFT. RGB is converted to grayscale
    before matching. Returns None if correlation is too weak."""
    page = Image.frombytes("RGB", [page_pix.width, page_pix.height], page_pix.samples)
    frag = Image.open(frag_path).convert("RGB")
    I = np.array(page.convert("L"), dtype=np.float32)
    T = np.array(frag.convert("L"), dtype=np.float32)
    H, W = I.shape
    h, w = T.shape
    if h > H or w > W:
        return None
    # FFT-based NCC
    pad_h = H + h - 1
    pad_w = W + w - 1
    fI = np.fft.rfft2(I, s=(pad_h, pad_w))
    fI2 = np.fft.rfft2(I * I, s=(pad_h, pad_w))
    fT = np.fft.rfft2(T[::-1, ::-1], s=(pad_h, pad_w))
    fOnes = np.fft.rfft2(np.ones_like(T)[::-1, ::-1], s=(pad_h, pad_w))
    IT = np.fft.irfft2(fI * fT, s=(pad_h, pad_w))
    I_sum = np.fft.irfft2(fI * fOnes, s=(pad_h, pad_w))
    I2_sum = np.fft.irfft2(fI2 * fOnes, s=(pad_h, pad_w))
    T_sum = T.sum()
    T2_sum = (T * T).sum()
    N = h * w
    mean_T = T_sum / N
    var_T = T2_sum - T_sum * T_sum / N
    num = IT - mean_T * I_sum
    denom = np.sqrt(np.maximum(I2_sum - I_sum * I_sum / N, 0) * var_T)
    denom[denom < 1e-6] = 1e-6
    ncc = num / denom
    ncc = ncc[h - 1:H, w - 1:W]
    iy, ix = np.unravel_index(np.argmax(ncc), ncc.shape)
    if ncc[iy, ix] < 0.25:
        return None
    return int(ix), int(iy), w, h


def _page_image_bbox(page):
    """Union bbox (in page points) of all raster image objects on ``page``.

    Returns None if the page has no raster image objects (pure vector figure).
    """
    imgs = page.get_images(full=True)
    rects = []
    for im in imgs:
        rs = page.get_image_rects(im[0])
        if rs:
            rects.extend(rs)
    if not rects:
        return None
    xs0 = [r.x0 for r in rects]
    ys0 = [r.y0 for r in rects]
    xs1 = [r.x1 for r in rects]
    ys1 = [r.y1 for r in rects]
    return pymupdf.Rect(min(xs0), min(ys0), max(xs1), max(ys1))


def _extend_for_right_annotations(bbox, page):
    """Extend ``bbox`` rightward to include text-annotation blocks sitting just
    to the right of the figure (e.g. ``User input (instructions)`` /
    ``Model output`` on page 27). Only blocks whose left edge is within 30pt of
    the figure's right edge and vertically overlapping the figure are absorbed.
    """
    for b in page.get_text("blocks"):
        x0, y0, x1, y1 = b[:4]
        if x0 >= bbox.x1 - 30 and x0 <= bbox.x1 + 60 and y1 > bbox.y0 and y0 < bbox.y1:
            bbox.x1 = max(bbox.x1, x1)
    return bbox


def fix_split_figures(text: str, pdf, img_dir) -> str:
    """Reassemble figures that pymupdf4llm split into multiple image fragments.

    For each page (except page 0, handled by extract_full_cover_image), if the
    markdown contains two or more consecutive same-page image fragments, we
    reassemble the figure:

      (A) Raster-image pages (e.g. page 27, Figure 1.2): take the union bbox of
          the PDF image objects, extend rightward to absorb nearby text
          annotations, and crop from a full-page render. This yields the exact
          figure area — no dropped side text, no swallowed body prose.
      (B) Pure-vector pages (e.g. pages 50/74): locate each pymupdf4llm fragment
          via NCC template matching, union the positions, crop from the render.
    """
    doc = pymupdf.open(pdf)
    img_dir = pathlib.Path(img_dir)

    lines = text.split("\n")
    out = []
    i = 0
    n = len(lines)
    while i < n:
        m = _IMG_RE.match(lines[i])
        if not m:
            out.append(lines[i])
            i += 1
            continue
        pagenum = int(m.group(3))
        if pagenum == 1:  # cover handled elsewhere
            out.append(lines[i])
            i += 1
            continue
        # collect a run of same-page images (blank separators allowed)
        grp_paths = [m.group(2)]
        j = i + 1
        while j < n:
            mm = _IMG_RE.match(lines[j])
            if mm and int(mm.group(3)) == pagenum:
                grp_paths.append(mm.group(2))
                j += 1
            elif lines[j].strip() == "":
                j += 1
            else:
                break
        if len(grp_paths) < 2:
            out.extend(lines[i:j])
            i = j
            continue

        page = doc[pagenum - 1]
        page_pix = page.get_pixmap(matrix=pymupdf.Matrix(2, 2))

        # Strategy A: raster image objects present on this page
        bbox = _page_image_bbox(page)
        if bbox is not None and bbox.width > 20 and bbox.height > 20:
            bbox = _extend_for_right_annotations(bbox, page)
            margin = 4
            clip = pymupdf.Rect(
                max(0, bbox.x0 - margin),
                max(0, bbox.y0 - margin),
                min(page.rect.width, bbox.x1 + margin),
                min(page.rect.height, bbox.y1 + margin),
            )
            crop_pix = page.get_pixmap(clip=clip, matrix=pymupdf.Matrix(2, 2))
            out_name = f"figure-{pagenum:04d}.png"
            crop_pix.save(img_dir / out_name)
            out.append(f"![Figure on page {pagenum}](images/{out_name})")
            i = j
            continue

        # Strategy B: pure-vector figure — NCC template matching
        positions = []
        for rel in grp_paths:
            frag_path = img_dir.parent / rel  # rel is "images/..."
            if not frag_path.exists():
                continue
            pos = _ncc_match(page_pix, frag_path)
            if pos is not None:
                positions.append(pos)
        if len(positions) < 2:
            out.extend(lines[i:j])
            i = j
            continue
        min_x = min(p[0] for p in positions)
        min_y = min(p[1] for p in positions)
        max_x = max(p[0] + p[2] for p in positions)
        max_y = max(p[1] + p[3] for p in positions)
        margin = 4
        min_x = max(0, min_x - margin)
        min_y = max(0, min_y - margin)
        max_x = min(page_pix.width, max_x + margin)
        max_y = min(page_pix.height, max_y + margin)
        clip = pymupdf.Rect(min_x / 2, min_y / 2, max_x / 2, max_y / 2)
        crop_pix = page.get_pixmap(clip=clip, matrix=pymupdf.Matrix(2, 2))
        out_name = f"figure-{pagenum:04d}.png"
        crop_pix.save(img_dir / out_name)
        out.append(f"![Figure on page {pagenum}](images/{out_name})")
        i = j
    return "\n".join(out)


# ---------- post-processing: math / annotations / prose cleanup ----------
_SUP_GAP_RE = re.compile(r"</sup>\s*\|\s*<sup>")  # broken split inside a word
# pymupdf4llm sometimes splits (T) into "$^{( + <sup>_T_)</sup>"; stitch it first
_SUP_SPLIT_PAR_RE = re.compile(r"\$_?\^\{\(?\s*\}?\s*\$_?\s*<sup>_?([A-Za-z0-9]+)_?\)?</sup>")
# variable followed by a subscript-styled superscript: _x_<sup>_T_</sup> -> $x_T$
_SUP_SUB_RE = re.compile(r"_(\w)_<sup>_([^_]+?)_</sup>")
# variable followed by a parenthesised superscript: _z_<sup>(2)</sup> -> $z^{(2)}$
_SUP_PAR_RE = re.compile(r"_(\w)_<sup>\(\s*([^)]*?)\s*\)</sup>")
# bare token + superscript (numbers / symbols, e.g. 10<sup>-8</sup>, e<sup>-\u221e</sup>)
_SUP_BARE_RE = re.compile(r"(\w)<sup>([^<>]+?)</sup>")
_MARK_RE = re.compile(r"</?mark>")
_CONTINUED_RE = re.compile(r"(?m)^\s*#{1,6}\s*_?\(continued\)_?\s*$\n?", re.I)
_FENCE_RE = re.compile(r"```.*?```", re.S)

# Private-use-area code points emitted by pymupdf4llm for Symbol-font Greek
# letters, mapped to their proper Unicode glyphs.
_GREEK_PUA = {
    "\uF061": "\u03B1",  # α
    "\uF062": "\u03B2",  # β
    "\uF063": "\u03B3",  # γ
    "\uF064": "\u03B4",  # δ
    "\uF065": "\u03B5",  # ε
    "\uF066": "\u03B6",  # ζ
    "\uF067": "\u03B7",  # η
    "\uF068": "\u03B8",  # θ
    "\uF069": "\u03B9",  # ι
    "\uF06A": "\u03BA",  # κ
    "\uF06B": "\u03BB",  # λ
    "\uF06C": "\u03BC",  # μ
    "\uF06D": "\u03BD",  # ν
    "\uF06E": "\u03BE",  # ξ
    "\uF06F": "\u03BF",  # ο
    "\uF070": "\u03C0",  # π
    "\uF071": "\u03B1",  # α (alt slot)
    "\uF077": "\u03C9",  # ω
    "\uF078": "\u03C6",  # φ
    "\uF079": "\u03C8",  # ψ
    "\uF072": "\u03C1",  # ρ
    "\uF073": "\u03C3",  # σ
    "\uF074": "\u03C4",  # τ
    "\uF075": "\u03C5",  # υ
    "\uF076": "\u03C6",  # χ
}


def _in_code(text: str, pos: int) -> bool:
    """Return True if pos falls inside a fenced code block."""
    for m in _FENCE_RE.finditer(text):
        if m.start() <= pos < m.end():
            return True
    return False


# ============================================================================
# ⚠️ DO NOT REMOVE — 对应 PDF报告缺陷 #3（数学符号乱码 / 畸形上标）。
#    把 pymupdf4llm 的 <sup> 上标改写为 LaTeX 行内公式，并映射 Symbol 字体私有区
#    希腊字母。管道中调用两次（见 process_pdf_to_markdown）：首次在拼页后，二次在
#    段落重排后（幂等）。【删掉任一调用都会让 <sup>/乱码 重现】
# ============================================================================
def fix_math_superscripts(text: str) -> str:
    """Rewrite pymupdf4llm `<sup>` superscripts into LaTeX inline math.
    Handles the three shapes seen in this book:
      _x_<sup>_T_</sup>        -> $x^{(T)}$
      _z_<sup>(2)</sup>        -> $z^{(2)}$
      10<sup>-8</sup>          -> $10^{-8}$
      _e_<sup>_-\u221e_</sup>  -> $e^{-\u221e}$
    A broken word split (`</sup>|<sup>`) is stitched back first. Only genuine
    math superscripts are rewritten; other `<sup>` usages fall through.
    """
    text = _SUP_GAP_RE.sub("", text)
    text = _SUP_SPLIT_PAR_RE.sub(lambda m: f"$^{{{m.group(1)}}}$", text)
    text = _SUP_SUB_RE.sub(lambda m: f"${m.group(1)}_{{{m.group(2).strip()}}}$", text)
    text = _SUP_PAR_RE.sub(lambda m: f"${m.group(1)}^{{({m.group(2).strip()})}}$", text)
    text = _SUP_BARE_RE.sub(lambda m: f"${m.group(1)}^{{{m.group(2).strip()}}}$", text)
    # normalize private-use-area Greek letters to real Unicode FIRST so the
    # dot-product rule below can match them as math operands.
    text = "".join(_GREEK_PUA.get(ch, ch) for ch in text)
    # normalize dot-product operators `⋅`/`·` to LaTeX `\cdot` inside math-ish
    # contexts (the book uses them in formulas like `x⋅Φ(x)`). Match a dot
    # operator bordered by letters/symbols/`$` so prose periods are untouched,
    # then wrap the whole `A \cdot B` fragment in `$...$` if not already math.
    text = re.sub(r"(?<=[A-Za-z0-9$\u03a0\u03a6\u03a8\u03a3])\s*[⋅·]\s*(?=[A-Za-z0-9$\u03a0\u03a6\u03a8\u03a3])",
                  r" \\cdot ", text)
    # wrap a `word \cdot word` fragment in inline math if it is not already
    # inside a `$...$` pair (avoids double-wrapping via the negative lookbehind
    # on an opening `$`). Allow an optional `(...)` after the second operand so
    # `x \cdot Φ(x)` becomes `$x \cdot Φ(x)$`.
    text = re.sub(
        r"(?<!\$)([A-Za-z0-9\u03a0\u03a6\u03a8\u03a3]+) \\cdot ([A-Za-z0-9\u03a0\u03a6\u03a8\u03a3]+(?:\([^)]*\))?)(?!\$)",
        r"$\1 \\cdot \2$", text)
    return text


# ============================================================================
# ⚠️ DO NOT REMOVE — 对应图片类问题：pymupdf4llm 把图片内部的文字（封面艺术字、
#    图表标签、流程图文字）用 `<!-- Start/End of picture text -->` 包裹后当成正文
#    提取出来，导致"图片 + 重复文字"并存。这些文字本属于图片，应整块删除（保留
#    图片本身）。共 280 处。`【删掉本函数会让图内文字残留污染正文】`
# ============================================================================
_PICTURE_TEXT_RE = re.compile(
    r"<!--\s*Start of picture text\s*-->.*?<!--\s*End of picture text\s*-->\n?",
    re.S | re.I,
)


def strip_picture_text(text: str) -> str:
    """Remove every `<!-- Start/End of picture text -->` block (in-image text
    that pymupdf4llm extracts as body text). The figure images themselves are
    kept; only the redundant extracted caption/label text is dropped."""
    return _PICTURE_TEXT_RE.sub("", text)


# ============================================================================
# ⚠️ DO NOT REMOVE — 封面归一化（用户报告：封面文字不该提取、封面不该分割、
#    封面不完整）。封面整页大图已含完整书名/作者/出版社视觉；pymupdf4llm 额外从
#    图内/文字层提取出残缺的 "# BUILD A"、作者、装饰 logo、出版社行，再拆成多块叠加
#    在整页图之上 —— 既残缺又重复。本函数把封面碎片块替换为单张整页封面图（alt 文本
#    承载完整书名信息），不再提取任何封面文字、不再分块。
#    注意：pymupdf4llm 会把封面整页大图错误截断/缩放成 278x278 方形（见
#    extract_full_cover_image），故封面图必须用 extract_full_cover_image 重新提取的
#    完整整页图，不能用 pymupdf4llm 生成的 0001-01.png。
# ============================================================================
_COVER_BLOCK_RE = re.compile(
    r"^#\s*\*{0,3}BUILD A\b.*?"          # 残缺主标题 "# BUILD A ..."
    r"(?:(?!^#\s).)*?"                     # 跨任意行（直到非标题行）
    r"\*\*M\s*A\s*N\s*N\s*I\s*N\s*G\*\*\s*$",  # 出版社行 "**M A N N I N G**"
    re.S | re.M | re.I,
)


def extract_full_cover_image(pdf, img_dir) -> str | None:
    """Extract the cover page's full-page image directly with PyMuPDF.

    pymupdf4llm truncates the cover's full-page image to a 278x278 square, so we
    re-extract page 0's largest image whose bbox covers the whole page and save
    it as ``images/cover-full.png``. Returns the markdown-relative path, or None
    if no full-page image is found (caller then falls back to pymupdf4llm's image).
    """
    doc = pymupdf.open(pdf)
    page = doc[0]
    pw, ph = page.rect.width, page.rect.height
    best = None
    for img in page.get_images(full=True):
        try:
            bbox = page.get_image_bbox(img)
        except Exception:
            continue
        # full-page image: covers >= 95% of both dimensions
        if bbox.width >= 0.95 * pw and bbox.height >= 0.95 * ph:
            d = doc.extract_image(img[0])
            if best is None or d["width"] * d["height"] > best[1]:
                best = (img[0], d["width"] * d["height"], d["ext"], d["image"])
    if best is None:
        return None
    out = img_dir / "cover-full.png"
    out.write_bytes(best[3])
    return f"images/{out.name}"


def fix_cover_page(text: str, cover_img: str | None = None) -> str:
    """Collapse the fragmented front-cover region into a single full-page image.

    The full-page cover image already renders the complete title/author/
    publisher, so any extracted text around it is redundant *and* incomplete
    ("BUILD A" only). Replace the whole block with one image reference; its alt
    text carries the complete bibliographic information. No cover text is kept.
    ``cover_img`` must be the full-resolution image from extract_full_cover_image.
    """
    def _replace(m: re.Match) -> str:
        if cover_img:
            img_path = cover_img
        else:
            img = re.search(r"!\[[^\]]*\]\((images/[^)]*?-0001-01\.png)\)", m.group(0))
            if not img:
                return m.group(0)  # safety: no cover image found, leave as-is
            img_path = img.group(1)
        return (
            f"![Build a Large Language Model From Scratch — "
            f"Sebastian Raschka (Manning)]({img_path})\n"
        )
    return _COVER_BLOCK_RE.sub(_replace, text, count=1)


# ============================================================================
# ⚠️ DO NOT REMOVE — 对应 PDF报告缺陷 #2(边注)/#6(continued 残留)。
#    删除 <mark> 高亮标签与分页提示 (continued)。仅删这两类，勿扩大范围。
# ============================================================================
def clean_annotations(text: str) -> str:
    """Strip residual HTML highlight tags and PDF pagination markers."""
    text = _MARK_RE.sub("", text)
    text = _CONTINUED_RE.sub("", text)
    return text


# ============================================================================
# ⚠️ DO NOT REMOVE — 对应 PDF报告缺陷 #10（正文旁注 NOTE 未用 blockquote 区分）。
#    pymupdf4llm 把书中的 margin note 输出为两类：行首直接 `NOTE ...` 或带列表前缀
#    `- NOTE ...`（两种均为独立旁注段），原样输出会丢失"旁注/引用"语义。本函数把
#    这两类独立 NOTE 段都转成 Markdown 引用块 `> NOTE ...`（多行 NOTE 逐行加 `>`），
#    与代码块边注的 blockquote 风格统一。仅在段首为 `NOTE `/`- NOTE ` 且为独立段
#    （前一行空行）时处理，避免误伤内文中的 "NOTE" 或普通 `- ` 列表项。
# ============================================================================
_NOTE_RE = re.compile(r"^(-\s*)?NOTE\s?(.*)$")


def fix_margin_notes(text: str) -> str:
    """Convert standalone `NOTE ...` / `- NOTE ...` margin-note paragraphs into blockquotes."""
    lines = text.split("\n")
    out = []
    i = 0
    n = len(lines)
    while i < n:
        m = _NOTE_RE.match(lines[i])
        if m:
            buf = [m.group(2).strip()]
            j = i + 1
            while j < n and lines[j].strip() != "":
                buf.append(lines[j].strip())
                j += 1
            quoted = "\n".join(
                ("> NOTE " + buf[0]) if k == 0 else ("> " + b)
                for k, b in enumerate(buf)
            )
            out.append(quoted)
            i = j
        else:
            out.append(lines[i])
            i += 1
    return "\n".join(out)


# ============================================================================
# ⚠️ DO NOT REMOVE — 对应 PDF报告缺陷 #8c（矢量 Figure 顶部编号标注被误判为标题）。
#    纯矢量 Figure（如第30页 Figure 1.4，无栅格图片对象）经 pymupdf4llm 渲染后，
#    图顶部的编号标注（如 "8. The complete output (translation)"）常被单独提取成
#    `###`/`####` 标题，而渲染出的页面图本身不含该顶部标注 → 图片顶部文本丢失。
#    本函数检测 "数字. 描述" 格式的误判标题且其后紧邻 pymupdf4llm 页面图时：
#      (1) 删除该标题行（它是图标注，不是结构标题）；
#      (2) 用整页所有文本块的最小外接矩形从整页渲染裁剪出完整 Figure（含顶部标注），
#          覆盖原图片，文件名统一为 figure-<页>.png。
# ============================================================================
_FIG_LABEL_HEAD_RE = re.compile(r"^#{1,6}\s+\*{0,2}\d+\.\s+\S")


def fix_figure_label_headings(text: str, pdf, img_dir) -> str:
    """Restore vector-figure top labels mis-captured as headings."""
    doc = pymupdf.open(pdf)
    img_dir = pathlib.Path(img_dir)
    lines = text.split("\n")
    out = []
    i = 0
    n = len(lines)
    while i < n:
        m = _FIG_LABEL_HEAD_RE.match(lines[i])
        if m:
            target = None
            for k in range(i + 1, min(n, i + 4)):
                mm = _IMG_RE.match(lines[k])
                if mm and mm.group(3).isdigit():
                    target = (int(mm.group(3)), k)
                    break
            if target is None:
                out.append(lines[i])
                i += 1
                continue
            pagenum = target[0]
            page = doc[pagenum - 1]
            blocks = page.get_text("blocks")
            if not blocks:
                out.append(lines[i])
                i += 1
                continue
            # Figure region = vector-drawing boundary as anchor, expanded to
            # include only text blocks that overlap it or sit within 25pt of it
            # (in-figure labels like "8. The complete output (translation)").
            # This excludes the page running header, the "Figure X.Y" caption
            # and the body paragraphs below the figure.
            draws = page.get_drawings()
            if draws:
                drect = pymupdf.Rect(
                    min(d["rect"].x0 for d in draws),
                    min(d["rect"].y0 for d in draws),
                    max(d["rect"].x1 for d in draws),
                    max(d["rect"].y1 for d in draws),
                )
                fig_blocks = []
                for b in blocks:
                    if b[4].strip().startswith("Figure"):
                        continue  # caption, not part of the figure
                    br = pymupdf.Rect(b[0], b[1], b[2], b[3])
                    inter = br & drect
                    if not inter.is_empty:
                        fig_blocks.append(b)
                        continue
                    dx = max(drect.x0 - br.x1, br.x0 - drect.x1, 0)
                    dy = max(drect.y0 - br.y1, br.y0 - drect.y1, 0)
                    if (dx * dx + dy * dy) ** 0.5 < 25:
                        fig_blocks.append(b)
                if not fig_blocks:
                    fig_blocks = blocks
            else:
                # no vector drawings — fall back to all blocks (minus caption)
                fig_blocks = [b for b in blocks if not b[4].strip().startswith("Figure")]
            xs0 = [b[0] for b in fig_blocks]
            ys0 = [b[1] for b in fig_blocks]
            xs1 = [b[2] for b in fig_blocks]
            ys1 = [b[3] for b in fig_blocks]
            mg = 4
            clip = pymupdf.Rect(
                max(0, min(xs0) - mg),
                max(0, min(ys0) - mg),
                min(page.rect.width, max(xs1) + mg),
                min(page.rect.height, max(ys1) + mg),
            )
            crop = page.get_pixmap(clip=clip, matrix=pymupdf.Matrix(2, 2))
            out_name = f"figure-{pagenum:04d}.png"
            crop.save(img_dir / out_name)
            # remove the stale pymupdf4llm fragment that this figure replaces
            old_rel = _IMG_RE.match(lines[target[1]])
            if old_rel:
                stale = img_dir.parent / old_rel.group(2)
                if stale.exists() and stale.name != out_name:
                    stale.unlink()
            lines[target[1]] = f"![Figure on page {pagenum}](images/{out_name})"
            # drop the mislabeled heading line; advance past it
            i += 1
            continue
        out.append(lines[i])
        i += 1
    return "\n".join(out)


# ============================================================================
# ⚠️ DO NOT REMOVE — 对应 PDF报告缺陷 #11（概念解释框/侧边栏 callout 未与正文区分）。
#    书中带浅黄色填充背景(0.969,0.961,0.910)的侧边栏框（如 "This chapter covers"、
#    "Transformers vs. LLMs"、"Cross entropy loss"、各 "Exercise X.Y"、概念解释等，
#    全书 60+ 处）被 pymupdf4llm 输出为普通标题+正文，与正文章节无差别。本函数用
#    PDF 框的首行文本在 md 中定位 callout 标题行，收集其后直到下一个标题行的所有
#    内容，统一转成 blockquote（标题 `> **Xxx**`、内容逐行加 `>`），与 NOTE/图注
#    的引用块风格一致。须在 add_toc 之后、fix_faux_headings 之前调用（此时 callout
#    标题仍是 `#`/`##`/`######` 形态；fix_faux_headings 会把 `_X_` 包裹标题转纯粗体，
#    在此之前处理才能定位）。
# ============================================================================
_CALLOUT_FILL = (0.969, 0.961, 0.910)
_CALLOUT_HEAD_RE = re.compile(r"^#{1,6}\s+")


def _is_callout_fill(fill) -> bool:
    return (
        fill is not None
        and abs(fill[0] - _CALLOUT_FILL[0]) < 0.02
        and abs(fill[1] - _CALLOUT_FILL[1]) < 0.02
        and abs(fill[2] - _CALLOUT_FILL[2]) < 0.02
    )


def fix_callout_blocks(text: str, pdf) -> str:
    """Convert the book's shaded concept boxes (callouts) into blockquotes."""
    doc = pymupdf.open(pdf)
    lines = text.split("\n")

    def _wsnorm(s: str) -> str:
        return re.sub(r"\s+", " ", s).strip()

    # collect candidate callout boxes (dedupe overlapping rects by center)
    boxes = []
    for pidx in range(len(doc)):
        page = doc[pidx]
        for d in page.get_drawings():
            r = d["rect"]
            if not _is_callout_fill(d.get("fill")):
                continue
            if r.width < 100 or r.height < 40:
                continue
            txt = page.get_textbox(r)
            if not txt.strip():
                continue
            boxes.append((pidx, r, txt))
    # dedupe boxes whose rects are nearly identical
    seen = set()
    uniq = []
    for b in boxes:
        key = (b[0], round(b[1].x0 / 5), round(b[1].y0 / 5), round(b[1].x1 / 5), round(b[1].y1 / 5))
        if key in seen:
            continue
        seen.add(key)
        uniq.append(b)

    spans = []
    for pidx, r, txt in uniq:
        first = txt.split("\n")[0].strip()
        fn = _wsnorm(first)
        if len(fn) < 4:
            continue
        # locate the heading line that matches, skipping any line already
        # claimed by an earlier callout (same title text can occur on many
        # chapter-opening pages, e.g. "_This chapter covers_")
        start = None
        for idx, l in enumerate(lines):
            if not _CALLOUT_HEAD_RE.match(l):
                continue
            if fn not in _wsnorm(l):
                continue
            if any(s <= idx <= e for s, e in spans):
                continue
            start = idx
            break
        if start is None:
            continue
        j = start + 1
        while j < len(lines) and not _CALLOUT_HEAD_RE.match(lines[j]):
            j += 1
        spans.append((start, j - 1))

    spans.sort()
    out = []
    prev = 0
    for s, e in spans:
        out.extend(lines[prev:s])
        m = _CALLOUT_HEAD_RE.match(lines[s])
        title = lines[s][m.end():].strip()
        title = re.sub(r"^_+|_+$", "", title)  # unwrap _..._ pseudo-italic labels
        out.append(f"> **{title}**")
        for k in range(s + 1, e + 1):
            l = lines[k]
            out.append("" if l.strip() == "" else "> " + l)
        prev = e + 1
    out.extend(lines[prev:])
    return "\n".join(out)


# ============================================================================
# ⚠️ DO NOT REMOVE — 对应 PDF报告缺陷 #4（段落内硬换行）。
#    保守合并 PDF 物理行宽造成的段落内断行；代码块内绝不触碰（_in_code 守卫）。
#    需在 pair_figures_captions 之后调用，否则图注移动会打断续行判断。
# ============================================================================
# ============================================================================
# ⚠️ DO NOT REMOVE — 对应 PDF报告缺陷 #8（代码块围栏边界模糊/不成对）。
#    校验并重平衡代码围栏（``` 成对），为缺失的闭围栏补一行。幂等。
# ============================================================================
def ensure_fences_balanced(text: str) -> str:
    """Ensure fenced code blocks come in balanced pairs of ``` markers.
    pymupdf4llm occasionally drops a closing fence at a page boundary; this
    appends a missing closing fence so downstream Markdown renderers do not
    swallow the rest of the document as code. Idempotent: if already balanced,
    the text is returned unchanged.
    """
    lines = text.split("\n")
    depth = 0
    out = []
    for line in lines:
        if re.match(r"^\s*```", line):
            if depth == 0:
                depth = 1
            else:
                depth = 0
        out.append(line)
    if depth != 0:  # an unclosed fence at EOF
        out.append("```")
    return "\n".join(out)


# ⚠️ DO NOT REMOVE — 对应 PDF报告缺陷 #4（段落内硬换行）。
def merge_prose_hard_breaks(text: str) -> str:
    """Join paragraph-internal hard line breaks (a side effect of the PDF's
    fixed physical line width) into a single flowing paragraph, but ONLY where
    it is safe: the previous line is plain prose (ends without sentence-ending
    punctuation, a list bullet, a heading marker, or a code-fence) and the
    current line starts with a lower-case word. Lines inside fenced code
    blocks are never touched.
    """
    lines = text.split("\n")
    out = []
    for i, line in enumerate(lines):
        if i == 0 or not line:
            out.append(line)
            continue
        prev = out[-1]
        prev_stripped = prev.rstrip()
        # A blank line normally ends a paragraph, so we don't merge across it.
        # EXCEPTION: pymupdf4llm splits a paragraph across a page boundary and
        # inserts a blank line; if the line *before* the blank is plain prose
        # (not ending in sentence punctuation, not an index/citation entry) and
        # the current line starts lower-case, it is the same paragraph -> join.
        if prev_stripped == "" and len(out) >= 2:
            before = out[-2].rstrip()
            if (not before.endswith((":", ".", "!", "?", ";", '"', ")", "]", "}", "”"))
                    and not before.startswith(("#", "-", "*", ">", "1.", "2.", "3.", "```"))
                    and not re.search(r",\s*\d{1,3}\s*$", before)  # index entry w/ page no.
                    and not _in_code(text, text.find(before))):
                stripped = line.lstrip()
                first_word = re.match(r"[A-Za-z]+", stripped)
                if first_word and first_word.group(0)[0].islower() and not _in_code(text, text.find(line)):
                    out[-2] = before + " " + stripped
                    out.pop()  # drop the blank line between the two halves
                    continue
            out.append(line)
            continue
        # boundaries that must NOT be merged across
        if (prev_stripped.endswith((":", ".", "!", "?", ";", '"', ")", "]", "}", "”"))
                or prev_stripped.startswith(("#", "-", "*", ">", "1.", "2.", "3.", "```"))
                or _in_code(text, text.find(prev))):
            out.append(line)
            continue
        stripped = line.lstrip()
        first_word = re.match(r"[A-Za-z]+", stripped)
        if first_word and first_word.group(0)[0].islower() and not _in_code(text, text.find(line)):
            out[-1] = prev.rstrip() + " " + stripped
        else:
            out.append(line)
    return "\n".join(out)


_PAGE_HEADER_RE = re.compile(
    r"(?m)^\s*(PREFACE|CONTENTS|INDEX|BIBLIOGRAPHY|APPENDIX[A-Z]?|GLOSSARY|"
    r"ACKNOWLEDGMENTS|ABOUT THE AUTHOR|ABOUT THIS BOOK)\s*$\n?",
    re.I,
)
# chapter running header: `CHAPTER 4 **_Implementing a GPT model..._**` (every
# page); some pages lose the title and emit a bare `CHAPTER 4` line instead
_CHAPTER_HEADER_RE = re.compile(r"(?m)^CHAPTER\s+\d+\s*(?:\*{1,3}[^*]*\*{1,3})?\s*$\n?")
# section running header: `**_2.1 Section Title_**` (bold-italic, one per page;
# every one duplicates the `###### N.M Title` heading, which is kept)
_SECTION_HEADER_RE = re.compile(r"(?m)^\*{1,3}_[^*]+_\*{1,3}\s*$\n?")
# appendix running header: `APPENDIX A **_Introduction to PyTorch_**` (one per
# appendix page); duplicates the real `## appendix A ...` heading, so drop it.
_APPENDIX_HEADER_RE = re.compile(
    r"(?m)^APPENDIX\s+[A-Z]\s*(?:\*{1,3}_[^*]*_\*{1,3})?\s*$\n?"
)
# front-matter footer page numbers in roman numerals: `**xi**`, `**xx**`
_ROMAN_PAGE_RE = re.compile(
    r"(?m)^\*\*(?=[MDCLXVI])M{0,3}(CM|CD|D?C{0,3})(XC|XL|L?X{0,3})(IX|IV|V?I{0,3})\*\*\s*$\n?",
    re.I,
)


# ============================================================================
# ⚠️ DO NOT REMOVE — 对应 PDF报告缺陷 #4b（页眉残留词 PREFACE/CONTENTS 等）。
#    这些孤立大写词行插在段落中间会打断硬换行合并，须在 merge_prose_hard_breaks
#    之前清理。删除会让段落续行无法合并。
# ============================================================================
def strip_page_headers(text: str) -> str:
    """Remove the PDF's running headers/footers that pymupdf4llm extracts as
    standalone lines: front-matter words (`PREFACE`, `CONTENTS`, `ABOUT THIS
    BOOK`, ...), `CHAPTER N **_Title_**` page headers, `**_N.M Section_**`
    bold-italic running headers, and roman-numeral footer page numbers
    (`**xi**`). They sit between the two halves of a wrapped paragraph and
    break hard-line-break merging, so they must be stripped *before* prose
    re-flowing. Section/chapter titles themselves are untouched (they exist as
    real `######`/`##` headings).
    """
    text = _PAGE_HEADER_RE.sub("", text)
    text = _CHAPTER_HEADER_RE.sub("", text)
    text = _SECTION_HEADER_RE.sub("", text)
    text = _APPENDIX_HEADER_RE.sub("", text)
    text = _ROMAN_PAGE_RE.sub("", text)
    return text


# ============================================================================
# ⚠️ DO NOT REMOVE — 对应"伪标题"缺陷：pymupdf4llm 把原书的粗斜体小标签
#    （如 _This chapter covers_、_Summary_、_About the code_、目录/练习页的
#    _Chapter 2_/_Exercise 2.2_）误判为 ### 标题，破坏标题层级。这些本非文档结构
#    标题，应转为粗体 **_Xxx_** 而非标题。仅匹配首尾被 _..._ 包裹的标题行，
#    真实章节标题（无 _ 包裹）不受影响。须放 add_toc 之后（add_toc 已用
#    ## _contents_ 等定位并替换为目录），再转粗体。
# ============================================================================
_FAUX_HEADING_RE = re.compile(r"^(#{1,6})\s+_([^_].*?)_\s*$", re.M)


def fix_faux_headings(text: str) -> str:
    """Convert bold-italic label lines misread as headings back to bold text.

    pymupdf4llm maps the book's small bold-italic labels (e.g. ``_This chapter
    covers_``, ``_Summary_``, ``_About the code_``, TOC/exercise entries like
    ``_Chapter 2_``/``_Exercise 2.2_``) to ``###`` headings. They are not
    structural headings, so render them as ``**_label_**`` instead. Only lines
    whose text is wrapped in ``_..._`` are touched; real chapter titles (no
    surrounding underscores) are left as headings.
    """
    return _FAUX_HEADING_RE.sub(r"**_\2_**", text)


# ---------- TOC: clickable table of contents ----------
_HEAD_RE = re.compile(r"^(#{1,6})\s+(.*)$")

# Chapter / appendix titles that pymupdf4llm emitted as plain italic text
# instead of markdown headings (first occurrence is promoted to a heading).
_TITLE_PROMOTIONS = [
    r"^_Understanding large language models_\s*$",
    r"^_Implementing a GPT model from scratch to generate text_\s*$",
    r"^_Fine-tuning_ _<u>for classification</u>_\s*$",
    r"^_appendix B References and_ _<u>further reading</u>_\s*$",
    r"^_appendix D Adding bells and whistles to the training loop_\s*$",
    r"^_appendix E Parameter-efficient_ _<u>fine-tuning with LoRA</u>_\s*$",
]

_CHAPTER_DISPLAY = {
    "understanding large language models": "1 Understanding Large Language Models",
    "working with text data": "2 Working with Text Data",
    "coding attention mechanisms": "3 Coding Attention Mechanisms",
    "implementing a gpt model from scratch to generate text": "4 Implementing a GPT Model From Scratch to Generate Text",
    "pretraining on unlabeled data": "5 Pretraining on Unlabeled Data",
    "fine-tuning for classification": "6 Fine-Tuning for Classification",
    "fine-tuning to follow instructions": "7 Fine-Tuning to Follow Instructions",
}
_APPENDIX_DISPLAY = {
    "appendix a introduction to pytorch": "Appendix A Introduction to PyTorch",
    "appendix b references and further reading": "Appendix B References and Further Reading",
    "appendix c exercise solutions": "Appendix C Exercise Solutions",
    "appendix d adding bells and whistles to the training loop": "Appendix D Adding Bells and Whistles to the Training Loop",
    "appendix e parameter-efficient fine-tuning with lora": "Appendix E Parameter-Efficient Fine-Tuning With LoRA",
}
_FRONT_DISPLAY = {
    "preface": "Preface",
    "acknowledgments": "Acknowledgments",
    "about this book": "About This Book",
    "about the author": "About the Author",
    "about the cover illustration": "About the Cover Illustration",
}


# ⚠️ DO NOT REMOVE — 对应 PDF报告缺陷 #13(add_toc) 的支撑函数（标题清洗/锚点slug/目录标签）。
def _clean_heading_text(s: str) -> str:
    """Strip HTML tags, LaTeX math, and markdown emphasis from heading text so
    it renders cleanly and produces a stable anchor slug."""
    s = re.sub(r"</?[a-zA-Z][^>]*>", "", s)  # <u>, </u>, <br>, <sup> ...
    s = re.sub(r"\$[^$]*\$", "", s)          # LaTeX math fragments
    s = re.sub(r"[`*_]", "", s)              # inline code / emphasis markers
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _heading_slug(text: str) -> str:
    """GitHub-compatible anchor slug (lowercase, punctuation dropped,
    whitespace collapsed to single hyphens)."""
    slug = text.lower()
    slug = re.sub(r"[^a-z0-9 \-_]", "", slug)
    slug = re.sub(r"\s+", "-", slug)
    slug = re.sub(r"-+", "-", slug).strip("-")
    return slug


def _toc_label(clean: str) -> str:
    low = clean.lower()
    if low in _CHAPTER_DISPLAY:
        return _CHAPTER_DISPLAY[low]
    if low in _APPENDIX_DISPLAY:
        return _APPENDIX_DISPLAY[low]
    if low in _FRONT_DISPLAY:
        return _FRONT_DISPLAY[low]
    if low == "index":
        return "Index"
    return clean


# ---------- heading re-leveling ----------
_ANCHOR_RE = re.compile(r'<a id="[^"]*"></a>')
_NUMBERED_HEAD_RE = re.compile(r"(?:[A-E]\.\d+|\d+\.\d+)(?:\.\d+)*")


# ⚠️ DO NOT REMOVE — 对应 PDF报告缺陷 #13(relevel_headings) 的支撑函数（目录分级）。
def _section_depth(clean: str) -> int | None:
    """Number of dots in the section number (`1.3`->1, `3.3.1`->2, `A.1.1`->2).
    Returns None for anything that is not a numbered section heading. `clean`
    must be bare text (emphasis markers / HTML tags already stripped)."""
    tok = clean.split()[0] if clean.split() else ""
    if not _NUMBERED_HEAD_RE.fullmatch(tok):
        return None
    return tok.count(".")


# ⚠️ DO NOT REMOVE — 对应 PDF报告缺陷 #13(relevel_headings) 的支撑函数。
def _bare_heading(content: str) -> str:
    """Heading text with anchors, HTML tags and markdown emphasis removed, so
    classification works on both raw converter headings (`_1.3 ..._`) and
    already-rewritten ones (`<a id="..."></a>1.3 ...`)."""
    s = _ANCHOR_RE.sub("", content)
    s = re.sub(r"</?[a-zA-Z][^>]*>", "", s)
    s = re.sub(r"[`*_]", "", s)
    return s.strip()


# ⚠️ DO NOT REMOVE — 对应 PDF报告缺陷 #13（标题分级 ###### 升为 ###/####/#####）。
def relevel_headings(text: str) -> str:
    """Fix heading levels. pymupdf4llm dumps every section title at `######`
    (level 5), which is wrong: chapters are `##`, so sections `1.3` should be
    `###` and subsections `3.3.1` `####`. Rule:
      - numbered headings get `2 + dot count` (`###`/`####`/`#####`);
      - `_Summary_` / `_This chapter covers_` are chapter-level -> `###`;
      - anything else at `######` (box titles, exercises, `_Chapter N_`
        dividers, listing captions) sits one level below the section it
        belongs to.
    Idempotent: only rewrites level-6 headings, so re-runs are no-ops."""
    lines = text.split("\n")
    cur = 2  # structural depth of the open section (chapter = 2)
    for i, l in enumerate(lines):
        m = _HEAD_RE.match(l)
        if not m:
            continue
        level = len(m.group(1))
        content = m.group(2)
        clean = _bare_heading(content)
        if level <= 2:
            cur = 2  # chapter / front-matter heading resets section context
            continue
        if level != 6:
            continue
        depth = _section_depth(clean)
        low = clean.lower()
        if depth is not None:
            new = min(2 + depth, 5)
            cur = new
        elif low in ("summary", "this chapter covers"):
            new = 3
        else:
            new = min(max(cur + 1, 3), 5)
        lines[i] = "#" * new + " " + content
    return "\n".join(lines)


# ⚠️ DO NOT REMOVE — 对应 PDF报告缺陷 #13(add_toc) 的支撑函数（判定目录目标标题）。
def _is_toc_heading(level: str, clean: str) -> bool:
    """TOC targets: `##`-level (front matter / chapters / appendices) plus
    numbered sections (1.1, 3.3.1, A.1, D.1 ...) at any level. Caption
    headings, exercise headings, `#`-level lines (which in this document are
    always code comments like `# if torch.backends...`), and the
    `### Instruction:`/`Input:`/`Response:` prompts are not part of the
    navigation structure and are never rewritten."""
    if not clean:
        return False
    if clean.lower() in ("brief contents", "contents"):
        return False
    if level == "#":
        return False
    if re.match(r"^(?:###\s+)?(?:Instruction|Input|Response|Correct response|Dataset response|Score):", clean):
        return False
    if level == "##":
        return True
    return bool(re.match(r"^(?:[A-E]\.\d+|\d+\.\d+)", clean))


# ============================================================================
# ⚠️ DO NOT REMOVE — 生成可点击目录（TOC）。脚本实际存在的关键步骤，旧 README
#    第1-12步未记录本函数，请勿因"方案未提及"而删除。必须在所有清洗之后调用。
# ============================================================================
def add_toc(text: str) -> str:
    """Make the book's TOC clickable.

    1. Promote plain-text chapter/appendix titles (ch 1/4/6, app B/D/E) to
       `##` headings so they become valid jump targets.
    2. Give every TOC-target heading a cleaned text plus an explicit
       `<a id="slug">` anchor (deterministic jumps in any renderer).
    3. Replace the mangled `_brief contents_` / `_contents_` sections with a
       real nested Markdown TOC whose links point to those anchors.

    Runs last in the pipeline so it cannot affect any other fix.
    """
    lines = text.split("\n")

    # section markers are located before headings get rewritten
    def _find(prefix: str):
        for i, l in enumerate(lines):
            if l.startswith(prefix):
                return i
        return None

    i_brief = _find("## _brief contents_")
    i_contents = _find("## _contents_")
    i_preface = _find("## _<u>preface</u>_")

    # promote plain-text chapter/appendix titles to ## headings
    for pat in _TITLE_PROMOTIONS:
        r = re.compile(pat)
        for i, l in enumerate(lines):
            if r.match(l):
                lines[i] = "## " + l.rstrip()
                break

    # rewrite TOC-target headings with cleaned text + explicit anchors.
    # No fence tracking here: real headings are never inside code blocks, and
    # code-looking lines (`# if ...`, `### Instruction:`) are rejected by
    # `_is_toc_heading`, so heading detection is purely regex-based.
    toc = []  # (level, clean_text, slug)
    seen = {}
    for i, l in enumerate(lines):
        if i_contents is not None and i <= i_contents:
            continue  # everything up to and including the old contents section
        m = _HEAD_RE.match(l)
        if not m:
            continue
        level = m.group(1)
        clean = _clean_heading_text(m.group(2))
        if not _is_toc_heading(level, clean):
            continue
        slug = _heading_slug(clean)
        if not slug:
            continue
        if slug in seen:
            seen[slug] += 1
            slug = f"{slug}-{seen[slug] - 1}"  # 2nd occurrence -> -1 (GitHub style)
        else:
            seen[slug] = 1
        lines[i] = f'{level} <a id="{slug}"></a>{clean}'
        toc.append((len(level), clean, slug))

    # build nested TOC lists
    toc_lines = ["## Contents", ""]
    brief_lines = ["## Brief Contents", ""]
    for level, clean, slug in toc:
        label = _toc_label(clean)
        if level <= 2:
            brief_lines.append(f"- [{label}](#{slug})")
            toc_lines.append(f"- [{label}](#{slug})")
        else:
            dots = clean.split()[0].count(".") if clean.split() else 0
            indent = "  " * dots
            toc_lines.append(f"{indent}- [{label}](#{slug})")
    toc_lines.append("")
    brief_lines.append("")

    # replace the mangled sections (bottom-up so earlier indices stay valid)
    if i_preface is not None and i_contents is not None and i_preface > i_contents:
        lines[i_contents:i_preface] = toc_lines
    if i_contents is not None and i_brief is not None and i_contents > i_brief:
        lines[i_brief:i_contents] = brief_lines
    return "\n".join(lines)


def process_pdf_to_markdown(pdf, md_path, img_dir):
    """Full pipeline: restore code blocks, clean the markdown, then build a
    clickable TOC. Returns the output path."""
    # ---------- 1. extract code blocks per PDF page ----------
    doc = pymupdf.open(pdf)
    page_blocks = {}
    for pno in range(doc.page_count):
        d = doc[pno].get_text("dict")
        annos = []
        for b in d["blocks"]:
            if b["type"] != 0:
                continue
            for l in b["lines"]:
                for s in l["spans"]:
                    if "BoldItali" in s["font"] or "BoldItalic" in s["font"]:
                        yc = (s["bbox"][1] + s["bbox"][3]) / 2
                        annos.append((yc, s["bbox"][0], s["text"].strip()))
        blks = []
        for b in d["blocks"]:
            if b["type"] != 0:
                continue
            spans_all = [s for l in b["lines"] for s in l["spans"]]
            total = sum(len(s["text"]) for s in spans_all)
            if total == 0:
                continue
            code_chars = sum(len(s["text"]) for s in spans_all if is_code_font(s["font"]))
            if code_chars < 12 or code_chars / total < 0.75:
                continue
            lines = []
            for l in b["lines"]:
                cs = [s for s in l["spans"] if is_code_font(s["font"])]
                if not cs:
                    continue
                cs.sort(key=lambda s: s["bbox"][0])
                lines.append("".join(s["text"] for s in cs).rstrip())
            flat = norm("".join(lines))
            if len(flat) < 12:
                continue
            x0, y0, x1, y1 = b["bbox"]
            note_spans = sorted((yc, ax, t) for yc, ax, t in annos
                                if y0 - 4 <= yc <= y1 + 4 and ax > x1 - 30 and t)
            notes, cur, last_y = [], [], None
            for yc, ax, t in note_spans:
                if last_y is not None and yc - last_y > 8:
                    notes.append(" ".join(cur))
                    cur = []
                cur.append(t)
                last_y = yc
            if cur:
                notes.append(" ".join(cur))
            blks.append({"lines": lines, "flat": flat, "y0": y0, "notes": notes,
                         "keys": block_keys(lines), "used": False})
        blks.sort(key=lambda b: b["y0"])
        page_blocks[pno] = blks
    print(f"Extracted {sum(len(v) for v in page_blocks.values())} code blocks from PDF")

    # ---------- 2. convert with page chunks ----------
    chunks = pymupdf4llm.to_markdown(str(pdf), write_images=True, image_path=str(img_dir),
                                     image_format="png", page_chunks=True, show_progress=False,
                                     force_text=True)
    pages_text = [c["text"] if isinstance(c, dict) else c[0] for c in chunks]
    assert len(pages_text) == doc.page_count, (len(pages_text), doc.page_count)

    # ---------- 3. per-page stream matching & span replacement ----------
    n_used = 0
    for pno, text in enumerate(pages_text):
        blks = page_blocks.get(pno, [])
        if not blks:
            continue
        lines = text.split("\n")
        stream_parts, index = [], []
        for li, line in enumerate(lines):
            sl, mp = strip_with_map(line)
            for j, ch in enumerate(sl):
                stream_parts.append(ch)
                index.append((li, mp[j]))
        stream = "".join(stream_parts)
        if not stream:
            continue

        # protect figure-caption / picture-text lines: a code-block span must
        # never swallow them (see try_match). Maps stream position -> protected.
        protected_line = {li for li, l in enumerate(lines)
                          if re.match(r"^(?:Figure\s+\d+\.\d+|<!-- (?:Start|End) of picture text -->)", l)}
        protected = (lambda p: index[p][0] in protected_line) if protected_line else None

        spans = []
        cursor = 0
        for blk in blks:
            res = try_match(blk, stream, cursor, protected) or try_match(blk, stream, 0, protected)
            if res is None:
                continue
            ws, end = res
            if any(ws < e2 and end > s2 for s2, e2, _ in spans):
                continue
            spans.append((ws, end, blk))
            blk["used"] = True
            n_used += 1
            cursor = end

        for ws, end, blk in sorted(spans, reverse=True):
            ls, ps = index[ws]
            le, pe = index[end - 1]
            head_raw = lines[ls][:ps]
            span_text = "\n".join([lines[ls][ps:]] + [lines[k] for k in range(ls + 1, le)]
                                  + [lines[le][:pe + 1]]) if le > ls else lines[ls][ps:pe + 1]
            notes = extract_notes(head_raw + "\n" + span_text)
            repl = make_replacement(blk, notes)
            head = re.sub(r"\*\*[^*]*\*\*", "", head_raw).strip()
            tail = re.sub(r"\*\*[^*]*\*\*", "", lines[le][pe + 1:]).strip()
            if ls == le:
                lines[ls] = (head + "\n\n" if head else "") + repl + ("\n\n" + tail if tail else "")
            else:
                lines[ls] = (head + "\n\n" if head else "") + repl
                for k in range(ls + 1, le):
                    lines[k] = ""
                lines[le] = tail
        pages_text[pno] = "\n".join(lines)

    # ---------- 4. post-processing: math / annotations / prose cleanup ----------
    final = "\n\n".join(pages_text).replace("](output/images/", "](images/")
    # drop standalone page-number lines like **27**
    final = re.sub(r"(?m)^\*\*\d+\*\*\s*\n", "", final)
    # normalize any non-python/bash language tag emitted by the converter
    # (```py is not a valid tag; renaming keeps the fence pair intact)
    final = re.sub(r"(?m)^```py\s*$", "```python", final)
    # math / annotation / prose cleanup (each step is independent and idempotent)
    final = fix_math_superscripts(final)   # [3] superscripts -> LaTeX, Greek PUA
    final = strip_picture_text(final)      # [9b] drop in-image text extracted by converter
    cover_img = extract_full_cover_image(pdf, img_dir)  # re-extract full-page cover (pymupdf4llm truncates it)
    final = fix_cover_page(final, cover_img)  # [cover] collapse fragmented cover into one image
    final = clean_annotations(final)       # [6] drop <mark> tags & (continued)
    final = ensure_fences_balanced(final)  # [8] rebalance code fences (append missing close)
    final = strip_page_headers(final)      # [4b] drop running-header words
    final = pair_figures_captions(final)   # [7] move caption next to its figure
    final = fix_split_figures(final, pdf, img_dir)  # [8b] reassemble split figures
    final = fix_figure_label_headings(final, pdf, img_dir)  # [8c] restore vector-fig top labels
    final = fix_margin_notes(final)        # [10] NOTE margin notes -> blockquotes
    final = merge_prose_hard_breaks(final) # [4] reflow paragraph hard breaks
    # second, idempotent math pass: re-flowing may expose fragments (e.g. a
    # superscript split across a line boundary) the first pass did not rewrite.
    final = fix_math_superscripts(final)
    # last: clickable table of contents (runs after every other fix).
    # NB: heading re-leveling runs AFTER add_toc so that plain-text chapter
    # titles promoted here to `##` reset the section context; the TOC itself is
    # built from heading levels' dot counts, so it is unaffected by re-leveling.
    final = add_toc(final)
    # convert the book's shaded concept boxes into blockquotes (runs after
    # add_toc, before fix_faux_headings so the callout headings are still
    # `#`/`##`/`######` shaped and locatable)
    final = fix_callout_blocks(final, pdf)   # [11] concept boxes -> blockquotes
    # convert bold-italic labels misread as headings back to bold (**_label_**)
    final = fix_faux_headings(final)
    # fix heading levels (###### for sections -> ###/#### by structure)
    final = relevel_headings(final)
    md_path.write_text(final, encoding="utf-8")
    total = sum(len(v) for v in page_blocks.values())
    print(f"used={n_used}/{total}")
    unmatched = [(p, b) for p, v in page_blocks.items() for b in v if not b["used"]]
    print(f"unmatched: {len(unmatched)}")
    for p, b in unmatched[:200]:
        print(f"  p{p}: {b['flat'][:70]}")
    return md_path


if __name__ == "__main__":
    process_pdf_to_markdown(PDF, MD, IMG_DIR)