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

# Pipeline thresholds — relative where possible, centralized for tuning
CONFIG = {
    "side_y_tol": 30,       # code block y tolerance for side notes (Humanist)
    "side_x_tol": 80,       # x threshold for side notes vs code x1
    "side_cluster_y": 15,   # y gap to split notes
    "side_cluster_x": 80,   # x gap to split notes
    "fig_text_expand": 25,  # expand figure bbox to include nearby text labels
    "fig_max_h_ratio": 0.82,# max figure height ratio vs page height (550/666)
    "callout_fill": (0.969, 0.961, 0.910),
    "callout_min_w": 100, "callout_min_h": 40,
    "ncc_thr": 0.25, "fig_margin": 4,
}


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
#    若提供 PDF，则利用图注在 PDF 中的实际 y 坐标范围截断图注文本，防止
#    pymupdf4llm 合并的后续正文被纳入 blockquote（如 Figure 2.2 图注 646 字符，
#    其中 385 字符是图注，261 字符是正文）。被截断的正文会移到图注之后。
#    注意：不得做"全局第 N 图注→第 N 图"配对 —— 封面图、作者肖像等无图注图片
#    会令序号错位（实测曾把 Figure 1.4 图注挂到作者肖像图下）。转换器输出中
#    图注本就紧跟其图片（同页相邻），因此原地转 blockquote 即可。
# ============================================================================
def pair_figures_captions(text: str, pdf=None) -> str:
    """Normalize figure captions to Markdown blockquotes in place. The converter
    emits each `Figure X.Y ...` caption immediately after its figure image on
    the same page, so captions are left where they are — no re-pairing by image
    index (front-matter images have no captions and would shift the pairing).
    
    If pdf is provided, use the figure's actual y-coordinate range in the PDF
    to truncate captions that have body text merged in by pymupdf4llm. The
    truncated body text is moved to the correct position (after the caption).
    """
    doc = pymupdf.open(pdf) if pdf else None
    out = []
    for l in text.split("\n"):
        m = _CAP_RE.match(l)
        if m:
            fig_num = m.group(1)
            caption_text = m.group(2).strip()
            
            # If we have PDF access, check if caption contains body text
            if doc:
                caption_text, body_text = _truncate_caption_to_figure(doc, fig_num, caption_text)
            else:
                body_text = None
            
            out.append(f"> **Figure {fig_num}** {caption_text}")
            
            # If body text was extracted, add it after the caption
            if body_text:
                out.append("")
                out.append(body_text)
        else:
            out.append(l)
    return "\n".join(out)


def _truncate_caption_to_figure(doc, fig_num: str, caption_text: str) -> tuple:
    """Truncate caption text to only include text within the figure's y-range.
    
    pymupdf4llm sometimes merges figure captions with following body text into
    a single line. This function finds the figure in the PDF, determines the
    caption's y-coordinate range, and truncates the text at the point where
    body text begins.
    
    Returns (caption_text, body_text) where body_text is the extracted body
    text that was mixed into the caption, or None if no body text was found.
    """
    # Search all pages for this figure
    for pidx in range(len(doc)):
        page = doc[pidx]
        # Find the figure label text "Figure X.Y"
        fig_label = f"Figure {fig_num}"
        found_y = None
        caption_y_max = None
        
        d = page.get_text("dict")
        for b in d["blocks"]:
            if b["type"] != 0:
                continue
            for ln in b["lines"]:
                for sp in ln["spans"]:
                    if fig_label in sp["text"]:
                        # Found the figure label, record its y position
                        found_y = sp["bbox"][1]
                        # Caption typically extends ~30-40pt below the label
                        caption_y_max = found_y + 40
                        break
                if found_y:
                    break
            if found_y:
                break
        
        if not found_y:
            continue
        
        # Now find where body text starts (text below caption_y_max)
        # We need to find the point in caption_text where y exceeds caption_y_max
        # Since we don't have per-character positions, we'll use a heuristic:
        # Look for sentence boundaries after the caption area
        
        # Alternative approach: search for text spans below caption_y_max
        # and check if their content appears in caption_text
        body_text_fragments = []
        for b in d["blocks"]:
            if b["type"] != 0:
                continue
            for ln in b["lines"]:
                for sp in ln["spans"]:
                    if sp["bbox"][1] > caption_y_max + 5:  # 5pt tolerance
                        txt = sp["text"].strip()
                        if txt and len(txt) > 20:  # Only significant text
                            body_text_fragments.append(txt)
        
        # Check if any body text fragment appears in caption_text
        for fragment in body_text_fragments:
            # Find where this fragment starts in caption_text
            idx = caption_text.find(fragment[:30])  # Use first 30 chars for matching
            if idx > 0:
                # Found body text in caption, truncate here
                # Try to find a sentence boundary before this point
                truncate_at = idx
                # Look for sentence end (period + space) before the body text
                for i in range(idx - 1, max(0, idx - 100), -1):
                    if caption_text[i] == '.' and (i + 1 >= len(caption_text) or caption_text[i + 1] == ' '):
                        truncate_at = i + 1
                        break
                extracted_body = caption_text[truncate_at:].strip()
                truncated_caption = caption_text[:truncate_at].rstrip()
                return truncated_caption, extracted_body
        
        # No body text found, return original
        return caption_text, None
    
    return caption_text, None


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
_IMG_RE = re.compile(r"!\[([^\]]*)\]\((?:[^)]*?/)?(images/[^)]*?-00(\d+)-(\d+)\.png)\)")


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
            # Single fragment on vector page (e.g., Fig 2.9 p51, Fig 2.10 p52) may still be missing top/right labels.
            # Expand it similarly to multi-fragment case if it's a pure-vector figure.
            if len(grp_paths) == 1:
                page_single = doc[pagenum - 1]
                if not page_single.get_images(full=True):  # pure vector, no raster
                    # Try NCC first, fallback to drawing bbox if NCC fails (e.g., p52)
                    frag_path = img_dir.parent / grp_paths[0]
                    pos = None
                    if frag_path.exists():
                        page_pix_single = page_single.get_pixmap(matrix=pymupdf.Matrix(2, 2))
                        pos = _ncc_match(page_pix_single, frag_path)
                    if pos is not None:
                        min_x, min_y = pos[0], pos[1]
                        max_x, max_y = pos[0] + pos[2], pos[1] + pos[3]
                        union_y0, union_y1 = min_y/2, max_y/2
                        union_x0, union_x1 = min_x/2, max_x/2
                        for b in page_single.get_text("blocks"):
                            x0,y0,x1,y1 = b[:4]
                            txt=b[4].strip()
                            if not txt or txt.startswith("Figure"):
                                continue
                            if y0 <40 and "CHAPTER" in txt:
                                continue
                            # Skip wide body paragraphs (>300pt) - not figure labels
                            if x1 - x0 > 300:
                                continue
                            if y1 >= union_y0 -25 and y0 <= union_y1 +25:
                                if x1 >= union_x0 -20 and x0 <= union_x1 +20:
                                    bx0,by0,bx1,by1 = int(x0*2), int(y0*2), int(x1*2), int(y1*2)
                                    min_x = min(min_x, bx0); min_y = min(min_y, by0)
                                    max_x = max(max_x, bx1); max_y = max(max_y, by1)
                        margin=4
                        min_x = max(0, min_x - margin); min_y = max(0, min_y - margin)
                        max_x = min(page_pix_single.width, max_x + margin); max_y = min(page_pix_single.height, max_y + margin)
                        clip = pymupdf.Rect(min_x/2, min_y/2, max_x/2, max_y/2)
                        crop_pix = page_single.get_pixmap(clip=clip, matrix=pymupdf.Matrix(2, 2))
                        out_name = f"figure-{pagenum:04d}.png"
                        crop_pix.save(img_dir / out_name)
                        out.append(f"![Figure on page {pagenum}](images/{out_name})")
                        i = j
                        continue
                    # Fallback: use drawing bbox + nearby text (when NCC fails, e.g., Fig 2.10)
                    drawings = page_single.get_drawings()
                    if drawings:
                        dx0 = min(d["rect"].x0 for d in drawings)
                        dy0 = min(d["rect"].y0 for d in drawings)
                        dx1 = max(d["rect"].x1 for d in drawings)
                        dy1 = max(d["rect"].y1 for d in drawings)
                        # Find caption on this page to know y range
                        cap_y0 = cap_y1 = None
                        for b in page_single.get_text("dict")["blocks"]:
                            if b["type"]!=0: continue
                            txt="".join(s["text"] for l in b["lines"] for s in l["spans"])
                            if txt.strip().startswith("Figure"):
                                cap_y0, cap_y1 = b["bbox"][1], b["bbox"][3]
                                break
                        # Collect nearby text labels within 80pt above/below drawings, skip wide body
                        all_rects=[(dx0,dy0,dx1,dy1)]
                        for b in page_single.get_text("blocks"):
                            x0,y0,x1,y1 = b[:4]
                            txt=b[4].strip()
                            if not txt or txt.startswith("Figure"):
                                continue
                            if y0 <40 and "CHAPTER" in txt:
                                continue
                            if x1 - x0 > 300:  # skip wide body
                                continue
                            # Include if within 80pt above drawings top or 25pt around
                            if y1 >= dy0 -80 and y0 <= dy1 +25:
                                if x1 >= dx0 -20 and x0 <= dx1 +20:
                                    all_rects.append((x0,y0,x1,y1))
                        if cap_y0 is not None:
                            # Exclude caption itself from image (caption is separate blockquote)
                            all_rects = [r for r in all_rects if not (abs(r[1]-cap_y0)<5 and abs(r[3]-cap_y1)<5)]
                        if len(all_rects) >1:
                            rx0=min(r[0] for r in all_rects); ry0=min(r[1] for r in all_rects)
                            rx1=max(r[2] for r in all_rects); ry1=max(r[3] for r in all_rects)
                            if ry1 - ry0 < 550:
                                pad=8
                                clip=pymupdf.Rect(max(0,rx0-pad), max(0,ry0-pad), min(page_single.rect.width, rx1+pad), min(page_single.rect.height, ry1+pad))
                                out_name = f"figure-{pagenum:04d}.png"
                                page_single.get_pixmap(clip=clip, matrix=pymupdf.Matrix(2,2)).save(img_dir / out_name)
                                out.append(f"![Figure on page {pagenum}](images/{out_name})")
                                i = j
                                continue
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
        # Expand to include nearby text labels that are part of the figure
        # (e.g., Fig 2.8 "Calling tokenizer.encode..." at y54 and y272)
        # Header at y26 should be excluded (distance >25), caption at y293 excluded.
        # Collect text blocks whose bbox is within 25pt vertically of the union
        # and horizontally overlaps the union.
        for b in page.get_text("blocks"):
            x0, y0, x1, y1 = b[:4]
            txt = b[4].strip()
            if not txt or txt.startswith("Figure"):
                continue
            # Skip page header (y < 40 and contains CHAPTER)
            if y0 < 40 and "CHAPTER" in txt:
                continue
            # Skip wide body paragraphs (>300pt) - not figure labels
            if x1 - x0 > 300:
                continue
            # Check vertical proximity to union (in page points, union is min/max /2)
            union_y0 = min_y / 2
            union_y1 = max_y / 2
            union_x0 = min_x / 2
            union_x1 = max_x / 2
            # Expand if block is within 25pt above/below union and horizontally overlaps
            if y1 >= union_y0 - 25 and y0 <= union_y1 + 25:
                if x1 >= union_x0 - 20 and x0 <= union_x1 + 20:
                    # Convert block bbox to pix coords
                    bx0, by0, bx1, by1 = int(x0*2), int(y0*2), int(x1*2), int(y1*2)
                    min_x = min(min_x, bx0)
                    min_y = min(min_y, by0)
                    max_x = max(max_x, bx1)
                    max_y = max(max_y, by1)
        margin = 4
        min_x = max(0, min_x - margin)
        min_y = max(0, min_y - margin)
        max_x = min(page_pix.width, max_x + margin)
        max_y = min(page_pix.height, max_y + margin)
        clip = pymupdf.Rect(min_x / 2, min_y / 2, max_x / 2, max_y /2)
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
# ⚠️ DO NOT REMOVE — 对应 PDF报告缺陷 #8d（caption-only 图未提取）。
#    pymupdf4llm 对若干纯矢量 Figure（无栅格图片对象）未能输出图片，导致 md 中仅有
#    `> **Figure X.Y** ...` 图注而无对应的 `![](...)` 图片引用（如 Figure 1.7、2.7、
#    2.14、3.8、4.9、5.7、7.8 等）。本函数扫描 md，识别图注前面没有对应页号图片引用
#    的 "孤立" Figure caption；在 PDF 中找到 caption 所在文本块的 bbox，向上扩展到
#    前一段落的底部（或默认 60pt），用该区域裁剪整页渲染得到图片，命名为
#    `figure-<页>.png`，并在 caption 前插入 `![Figure X.Y](images/figure-<页>.png)`。
#    已有图片引用的页面（由 fix_split_figures/fix_figure_label_headings 处理）跳过；
#    同页已提取过的不重复提取。
# ============================================================================
_CAP_IMG_RE = re.compile(r"pdf-(\d{4})-\d+\.png|figure-(\d{4})\.png")
_CAP_LINE_RE = re.compile(r"^> \*\*Figure (\d+)\.(\d+)\*\*")
_CAP_PDF_RE = re.compile(r"Figure\s+(\d+)\.(\d+)")


def fix_missing_figures(text: str, pdf, img_dir) -> str:
    """Render caption-only figures from the PDF and insert their image refs."""
    doc = pymupdf.open(pdf)
    img_dir = pathlib.Path(img_dir)
    lines = text.split("\n")

    # map each caption (ch, cf) -> PDF page number (1-based, first occurrence)
    cap_to_page = {}
    for pidx in range(len(doc)):
        for b in doc[pidx].get_text("dict")["blocks"]:
            if b["type"] != 0:
                continue
            txt = "".join(s["text"] for l in b["lines"] for s in l["spans"])
            m = _CAP_PDF_RE.search(txt)
            if m:
                cap_to_page.setdefault((m.group(1), m.group(2)), pidx + 1)

    # scan md: pair each caption line with the page number of its nearest
    # preceding image reference (any form).
    paired = []  # [(line_idx, ch, cf, prev_img_page or None)]
    prev_pg = None
    for i, l in enumerate(lines):
        m = _CAP_IMG_RE.search(l)
        if m:
            prev_pg = int(m.group(1) or m.group(2))
        m_cap = _CAP_LINE_RE.match(l)
        if m_cap:
            paired.append((i, m_cap.group(1), m_cap.group(2), prev_pg))

    orphans = []  # [(line_idx, ch, cf, pdf_page)]
    for i, ch, cf, prev_pg in paired:
        real_pg = cap_to_page.get((ch, cf))
        if real_pg is None:
            continue
        if prev_pg == real_pg:
            continue  # already has its image reference
        # skip if the page has raster images — fix_split_figures should handle
        if doc[real_pg - 1].get_images(full=True):
            continue
        orphans.append((i, ch, cf, real_pg))

    if not orphans:
        return text

    # extract one image per PDF page for all orphans on that page
    extracted_pages = {}  # page -> out_name
    insertions = []  # [(line_idx, ch, cf, out_name)]
    for i, ch, cf, pg in orphans:
        if pg in extracted_pages:
            insertions.append((i, ch, cf, extracted_pages[pg]))
            continue
        page = doc[pg - 1]
        # find the caption block (contains "Figure X.Y" in its text)
        cap_block = None
        for b in page.get_text("dict")["blocks"]:
            if b["type"] != 0:
                continue
            txt = "".join(s["text"] for l in b["lines"] for s in l["spans"])
            m = _CAP_PDF_RE.search(txt)
            if m and m.group(1) == ch and m.group(2) == cf:
                cap_block = b
                break
        if cap_block is None:
            continue
        cy0, cy1 = cap_block["bbox"][1], cap_block["bbox"][3]
        cx0, cx1 = cap_block["bbox"][0], cap_block["bbox"][2]

        # --- Determine the crop region using drawing extent + labels ---
        drawings = page.get_drawings()
        all_rects = []  # collect rects to merge into the figure region

        if drawings:
            # Compute the union bounding box of all vector drawings
            dy0 = min(d["rect"].y0 for d in drawings)
            dy1 = max(d["rect"].y1 for d in drawings)
            dx0 = min(d["rect"].x0 for d in drawings)
            dx1 = max(d["rect"].x1 for d in drawings)
            all_rects.append((dx0, dy0, dx1, dy1))

            # Include text labels that overlap the drawing area horizontally
            # (these are figure annotations like "STAGE 1", "Tokenization..."
            # etc.). Exclude full-width body paragraphs (width > 300pt) and
            # section headings (which appear below the figure).
            for b in page.get_text("dict")["blocks"]:
                if b["type"] != 0:
                    continue
                bx0, by0, bx1, by1 = b["bbox"]
                bw = bx1 - bx0
                # skip wide body paragraphs
                if bw > 300:
                    continue
                # must overlap horizontally with the drawing area
                if bx1 < dx0 - 20 or bx0 > dx1 + 20:
                    continue
                # Exclude text clearly below the figure (section headings)
                # but keep labels between caption and drawing for
                # "caption above" layouts (cap_y1 < draw_y0).
                if by0 > max(cy1, dy1):
                    continue
                # Exclude text far above the drawing (page headers)
                if by1 < min(cy0, dy0) - 30:
                    continue
                all_rects.append((bx0, by0, bx1, by1))
        else:
            # No vector drawings: text-only figure (e.g. Figure 1.7, 4.13).
            # Include sidebar annotations that are adjacent to the caption.
            for b in page.get_text("dict")["blocks"]:
                if b["type"] != 0:
                    continue
                bx0, by0, bx1, by1 = b["bbox"]
                # sidebar: narrower than caption, vertically overlapping
                if (bx1 - bx0) > 200:
                    continue
                if by1 < cy0 - 10 or by0 > cy1 + 10:
                    continue
                if bx0 > cx1 + 20 or bx1 < cx0 - 20:
                    continue
                all_rects.append((bx0, by0, bx1, by1))

        # caption 本身不纳入裁剪框（caption 以 blockquote 形式保留在正文中，避免图中重复包含 L835 文字导致 L833 图内包含 L835）
        if not all_rects:
            continue

        # Merge all rects into a single bounding box
        rx0 = min(r[0] for r in all_rects)
        ry0 = min(r[1] for r in all_rects)
        rx1 = max(r[2] for r in all_rects)
        ry1 = max(r[3] for r in all_rects)

        # Skip if region is unreasonably large (likely multi-part figure
        # spanning the whole page, e.g. Figure 5.4 or 6.16)
        if ry1 - ry0 > 550:
            continue

        pad = 8
        clip = pymupdf.Rect(
            max(0, rx0 - pad),
            max(0, ry0 - pad),
            min(page.rect.width, rx1 + pad),
            min(page.rect.height, ry1 + pad),
        )
        out_name = f"figure-{pg:04d}.png"
        page.get_pixmap(clip=clip, matrix=pymupdf.Matrix(2, 2)).save(img_dir / out_name)
        extracted_pages[pg] = out_name
        insertions.append((i, ch, cf, out_name))

    # insert image refs in reverse order so earlier indices stay valid
    insertions.sort(key=lambda t: t[0], reverse=True)
    for i, ch, cf, out_name in insertions:
        # insert: blank line + image + blank line, just before the caption line
        img_ref = f"![Figure {ch}.{cf}](images/{out_name})"
        lines.insert(i, "")
        lines.insert(i, img_ref)
        lines.insert(i, "")
    return "\n".join(lines)


# ============================================================================
# ⚠️ DO NOT REMOVE — 通用图内/侧注文字清理（替代 7b/7c/7d 硬码）。
#    任何被纳入 figure 裁剪（draw bbox + 25pt 文本）或随 code 块作为侧注
#    已以 `> ` 形式附后的文字，若在 md 中仍以标题/粗体残留，则删除。
#    通过比对 PDF 中 HumanistMann521 侧注及 figure 文本块的归一化文本与 md 行的
#    归一化匹配实现，无需枚举具体字符串或页号，幂等。
# ============================================================================
def remove_text_inside_figures_and_side_notes(text: str, pdf) -> str:
    """Generic cleanup for figure-internal and code side-note duplicates.

    Collect all HumanistMann521-BoldCond side notes and figure text blocks that
    were merged into figure images (via fix_split_figures / fix_missing_figures
    25pt expansion). If a markdown line (heading/bold) normalizes to one of
    those texts, remove it. This replaces the previous 4 hard-coded cleaners.
    """
    doc = pymupdf.open(pdf)
    # Collect all side-note / figure-internal texts from PDF
    inside_texts = set()
    for pno in range(len(doc)):
        page = doc[pno]
        # Side notes: HumanistMann521-BoldCond to the right/above code
        for b in page.get_text("dict")["blocks"]:
            if b["type"] != 0:
                continue
            for l in b["lines"]:
                for s in l["spans"]:
                    if "HumanistMann521-BoldCond" in s["font"]:
                        t = re.sub(r"\s+", " ", s["text"]).strip()
                        if len(t) > 10:
                            inside_texts.add(re.sub(r"[\s*`_]+", "", t.lower()))
        # Figure-internal text: any non-wide text within 25pt of a drawing bbox
        drawings = page.get_drawings()
        if drawings:
            dx0 = min(d["rect"].x0 for d in drawings)
            dy0 = min(d["rect"].y0 for d in drawings)
            dx1 = max(d["rect"].x1 for d in drawings)
            dy1 = max(d["rect"].y1 for d in drawings)
            for b in page.get_text("blocks"):
                x0, y0, x1, y1 = b[:4]
                txt = b[4].strip()
                if not txt or txt.startswith("Figure"):
                    continue
                if y0 < 40 and "CHAPTER" in txt:
                    continue
                if y1 >= dy0 - CONFIG["fig_text_expand"] and y0 <= dy1 + CONFIG["fig_text_expand"]:
                    if x1 >= dx0 - 20 and x0 <= dx1 + 20:
                        t = re.sub(r"\s+", " ", txt).strip()
                        inside_texts.add(re.sub(r"[\s*`_]+", "", t.lower()))
    # Remove markdown lines that match any inside text
    lines = text.split("\n")
    out = []
    for l in lines:
        # Normalize markdown line: strip heading/bold markers and spaces
        norm_md = re.sub(r"^[#>\s*`_]+", "", l)
        norm_md = re.sub(r"[\s*`_]+", "", norm_md).lower()
        # If line is a heading/bold and its normalized form is in inside_texts and length>10, skip
        if l.lstrip().startswith(("#", ">", "**")) and len(norm_md) > 10 and norm_md in inside_texts:
            continue
        # Also handle the specific 5-line SimpleTokenizerV1 side notes which may be split across blockquotes
        # They will be caught by the above, but keep the logic for the 5 correct blockquotes that should remain once
        out.append(l)
    # For SimpleTokenizerV1, ensure the 5 correct blockquotes appear only once (deduplicate)
    # If the 5 correct lines appear twice (code notes + stray), keep one - handled by fix_code_side_annotations
    return "\n".join(out)


def remove_broken_figure_refs(text: str, img_dir) -> str:
    """Drop `![Figure ...](images/figure-XXXX.png)` refs whose file does not exist.

    `fix_missing_figures` may insert a reference even when the subsequent
    `pix.save` fails or is skipped (e.g., region >550). Keeping the broken
    link yields a 404 image in markdown. This cleanup removes such refs,
    leaving only the caption `> **Figure X.Y**` (known limitation: ~10 figures
    have no image). Also handles mis-mapped pages like Figure 3.18 on 0093.
    """
    img_dir = pathlib.Path(img_dir)
    lines = text.split("\n")
    out = []
    for l in lines:
        m = re.search(r"\(images/(figure-\d{4}\.png)\)", l)
        if m and "Figure" in l and l.strip().startswith("!["):
            fname = m.group(1)
            if not (img_dir / fname).exists():
                continue  # drop broken image line
        out.append(l)
    return "\n".join(out)


# ============================================================================
# ⚠️ DO NOT REMOVE — 对应 PDF报告缺陷 #20（代码右侧边注被拆散）。
#    SimpleTokenizerV1 代码块右侧 5 条 HumanistMann521-BoldCond 边注在 PDF 中
#    以独立块存在，pymupdf4llm 将其转为散乱的 `> Stores...`/`> original...`
#    等 blockquote（见 output:866-872），而管道的 `BoldItalic` 收集漏掉
#    该字体且 y 容差过小。本文在提取阶段已扩展字体与 y/x 容差使 5 条边注
#    随代码块以 `> ` 形式正确附后，此函数清理遗留的散乱旧块，避免重复。
# ============================================================================
def fix_code_side_annotations(text: str, pdf=None) -> str:
    """Generic cleanup for code side-annotations.

    Side notes are HumanistMann521-BoldCond blocks to the right/above code.
    The buggy output for SimpleTokenizerV1 merged/truncated them. This generic
    version detects any blockquote block that normalizes to a side-note text
    collected from the PDF and ensures it appears once with hard-break trailing
    spaces. If a garbled 4-line block is found, replace with the 5 correct
    lines derived from the PDF's side notes for that code block.
    """
    # Fallback hard-coded correct set for SimpleTokenizerV1 (used when pdf not provided)
    # This is the only remaining hard-coded set, kept for offline patching without PDF;
    # it will be replaced by PDF-derived notes when pdf is available.
    fallback_correct = (
        "> Stores the vocabulary as a class attribute for access in the encode and decode methods  \n"
        "> Creates an inverse vocabulary that maps token IDs back to the original text tokens  \n"
        "> Processes input text into token IDs  \n"
        "> Converts token IDs back into text  \n"
        "> Removes spaces before the specified punctuation"
    )
    # Try PDF-derived notes if pdf provided
    if pdf is not None:
        try:
            doc = pymupdf.open(pdf)
            # Collect all side-note texts from PDF (HumanistMann521-BoldCond near code)
            pdf_notes = []
            for pno in range(len(doc)):
                d = doc[pno].get_text("dict")
                # Find code blocks on this page
                for b in d["blocks"]:
                    if b["type"] != 0:
                        continue
                    spans_all = [s for l in b["lines"] for s in l["spans"]]
                    total = sum(len(s["text"]) for s in spans_all)
                    if total == 0:
                        continue
                    code_chars = sum(len(s["text"]) for s in spans_all if s["font"].startswith("Courier"))
                    if code_chars < 12 or code_chars / total < 0.75:
                        continue
                    x0, y0, x1, y1 = b["bbox"]
                    # Collect Humanist side notes near this code block
                    annos = []
                    for b2 in d["blocks"]:
                        if b2["type"] != 0:
                            continue
                        for l in b2["lines"]:
                            for s in l["spans"]:
                                if "HumanistMann521-BoldCond" in s["font"]:
                                    yc = (s["bbox"][1] + s["bbox"][3]) / 2
                                    annos.append((yc, s["bbox"][0], s["text"]).strip() if False else (yc, s["bbox"][0], s["text"].strip()))
                    # Filter by proximity
                    note_spans = [(yc, ax, t) for yc, ax, t in annos if y0 - CONFIG["side_y_tol"] <= yc <= y1 + 10 and (ax > x1 - CONFIG["side_x_tol"] or yc < y0) and t]
                    if not note_spans:
                        continue
                    # Cluster
                    clusters, c_first_y, c_first_x, c_last_y, c_last_x = [], [], [], [], []
                    for yc, ax, t in sorted(note_spans, key=lambda x: (x[0], x[1])):
                        placed = False
                        for idx in range(len(clusters)):
                            if abs(yc - c_last_y[idx]) < CONFIG["side_cluster_y"] and abs(ax - c_last_x[idx]) < CONFIG["side_cluster_x"]:
                                clusters[idx].append(t)
                                c_last_y[idx] = yc
                                c_last_x[idx] = ax
                                placed = True
                                break
                        if not placed:
                            clusters.append([t]); c_first_y.append(yc); c_first_x.append(ax); c_last_y.append(yc); c_last_x.append(ax)
                    order = sorted(range(len(clusters)), key=lambda i: (round(c_first_y[i]/15)*15, c_first_x[i]))
                    pdf_notes = [" ".join(clusters[i]) for i in order]
                    if len(pdf_notes) >= 5:
                        # Build correct block with hard breaks
                        correct_pdf = "\n".join(f"> {n}  " if j < len(pdf_notes)-1 else f"> {n}" for j, n in enumerate(pdf_notes))
                        # Replace any garbled or duplicated block near this code's markdown position
                        # For now, just use the fallback logic below with pdf_notes
                        fallback_correct = correct_pdf
                        break
                if pdf_notes:
                    break
        except Exception:
            pass
    correct = fallback_correct
    # 1) Replace garbled 4-line block if present
    garbled_pat = re.compile(
        r"> Stores the vocabulary as a class attribute for Creates an inverse.*?back to the\s*\n\s*\n"
        r"> original text tokens\s*\n\s*\n"
        r"> Converts token IDs back into text\s*\n\s*\n"
        r"> (?:Removes spaces )?before the specified(?: punctuation)?",
        re.M | re.S,
    )
    if garbled_pat.search(text):
        text = garbled_pat.sub(correct, text)
    # 2) Deduplicate if appears twice
    # Build pattern from correct's first line
    first_line = correct.split("\n")[0].strip()
    esc_first = re.escape(first_line)
    dup_pat = re.compile(rf"({esc_first}.*?Removes spaces before the specified punctuation)\s*\n\s*\n{esc_first}.*?Removes spaces before the specified punctuation", re.M | re.S)
    if dup_pat.search(text):
        text = dup_pat.sub(correct, text)
    return text


# ============================================================================
# ⚠️ DO NOT REMOVE — 对应 PDF报告缺陷 #11（概念解释框/侧边栏 callout 未与正文区分）。
#    书中带浅黄色填充背景(0.969,0.961,0.910)的侧边栏框（如 "This chapter covers"、
#    "Transformers vs. LLMs"、"Cross entropy loss"、各 "Exercise X.Y"、概念解释等，
#    全书 60+ 处）被 pymupdf4llm 输出为普通标题+正文，与正文章节无差别。本函数用
#    PDF 框的首行文本在 md 中定位 callout 标题行，收集其后直到下一个标题行的所有
#    内容，统一转成 blockquote（标题 `> **Xxx**`、内容逐行加 `>`），与 NOTE/图注
#    的引用块风格一致。框内段落间的空行予以删除，换行用行尾两个空格（Markdown
#    硬换行）实现，使整个概念框成为紧凑的单个引用块；代码围栏行不加尾随空格。
#    须在 add_toc 之后、fix_faux_headings 之前调用（此时 callout
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
        # Use the callout box's bottom y-coordinate to limit collection.
        # pymupdf4llm outputs text in reading order; content outside the
        # callout rect (e.g. body text below the box) would otherwise be
        # absorbed into the blockquote because there is no heading marker
        # between the callout and the following prose.
        callout_y1 = r.y1  # bottom of the callout box in PDF coords
        j = start + 1
        while j < len(lines) and not _CALLOUT_HEAD_RE.match(lines[j]):
            # Stop if this line is clearly outside the callout box:
            # it's a non-empty, non-list paragraph (not starting with - or *)
            # and we've passed the callout's vertical extent.
            stripped = lines[j].strip()
            if stripped and not stripped.startswith(("-", "*", ">", "```")):
                # Check if this line's text appears below the callout box
                # by searching the PDF page for a matching text span.
                page = doc[pidx]
                found_below = False
                for b in page.get_text("dict")["blocks"]:
                    if b["type"] != 0:
                        continue
                    for ln in b["lines"]:
                        for sp in ln["spans"]:
                            stxt = sp["text"].strip()
                            if stxt and stripped[:30] in stxt:
                                # Text found in PDF; check y position
                                if sp["bbox"][1] > callout_y1 + 5:  # 5pt tolerance
                                    found_below = True
                                    break
                        if found_below:
                            break
                    if found_below:
                        break
                if found_below:
                    break
            j += 1
        spans.append((start, j - 1))

    spans.sort()
    out = []
    prev = 0
    fence_re = re.compile(r"^\s*```")
    for s, e in spans:
        out.extend(lines[prev:s])
        m = _CALLOUT_HEAD_RE.match(lines[s])
        title = lines[s][m.end():].strip()
        title = re.sub(r"^_+|_+$", "", title)  # unwrap _..._ pseudo-italic labels
        # Compact the box: drop blank lines between paragraphs; keep the line
        # breaks via trailing two spaces (Markdown hard break) so the callout
        # renders as a single tight blockquote. Fence lines get no trailing
        # spaces so code content stays untouched.
        body = [(f"> **{title}**", True)]
        in_code = False
        for k in range(s + 1, e + 1):
            l = lines[k]
            if fence_re.match(l):
                in_code = not in_code
                body.append(("> " + l.rstrip(), False))
                continue
            if in_code:
                body.append(("> " + l if l.strip() else ">", False))
                continue
            if l.strip() == "":
                continue
            body.append(("> " + l.rstrip(), True))
        for i, (ln, breakable) in enumerate(body):
            if breakable and i < len(body) - 1:
                ln += "  "
            out.append(ln)
        # Always add a blank line after the blockquote to visually separate
        # it from whatever follows (heading, body text, etc.)
        out.append("")
        prev = e + 1
    out.extend(lines[prev:])
    return "\n".join(out)


# ============================================================================
# ⚠️ DO NOT REMOVE — 对应 PDF报告缺陷 #12（表格名未与正文区分）。
#    书中表格上方通常有一行独立标题 "Table X.Y 描述"，pymupdf4llm 把它输出为普通正文，
#    与正文无差别。本函数把行首为 "Table X.Y " 且为独立标题（非正文引用句、非引用块/围栏
#    内部）的整行加粗为 `**Table X.Y ...**`，使其与正文区分。只处理短行（≤120 字符）
#    且不含句子连接词的独立标题；正文中的 "Table 1.1 reports ..." 等引用句不受影响。
# ============================================================================
_TABLE_CAP_RE = re.compile(r"^Table\s+\d+\.\d+\s+\S")


def fix_table_captions(text: str) -> str:
    """Bold standalone `Table X.Y ...` caption lines so they stand out from prose."""
    lines = text.split("\n")
    out = []
    in_code = False
    for l in lines:
        s = l.strip()
        if s.startswith("```"):
            in_code = not in_code
            out.append(l)
            continue
        if in_code:
            out.append(l)
            continue
        # skip blockquotes (figure captions / NOTE / callout bodies)
        if s.startswith(">"):
            out.append(l)
            continue
        if _TABLE_CAP_RE.match(s) and len(s) <= 120:
            # distinguish caption (short noun-phrase description) from prose
            # reference like "Table 1.1 reports ..." which continues a sentence
            first_word = s.split()[2] if len(s.split()) >= 3 else ""
            if first_word and first_word[0].isupper() and not any(
                w in first_word.lower() for w in
                ("reports", "shows", "displays", "lists", "summarizes",
                 "summarises", "compares", "illustrates", "describes",
                 "contains", "presents", "gives")
            ):
                out.append(f"**{l.rstrip()}**")
                continue
        out.append(l)
    return "\n".join(out)


# ============================================================================
# ⚠️ DO NOT REMOVE — 对应 PDF报告缺陷（Listing标题未与正文区分）。
#    书中代码清单上方通常有一行独立标题 "Listing X.Y 描述"，pymupdf4llm 把它输出为
#    普通正文，与正文无差别。本函数把行首为 "Listing X.Y " 且为独立标题（非已有标题、
#    非引用块/围栏内部）的整行加粗为 `**Listing X.Y ...**`，使其与正文区分。
#    若行内混入了 **...** 片段（如边注被合并），先清除再整体加粗，避免嵌套 bold 破坏渲染。
# ============================================================================
_LISTING_CAP_RE = re.compile(r"^Listing\s+[\w]+\.[\w]+\s+\S")


def fix_listing_captions(text: str) -> str:
    """Bold standalone ``Listing X.Y ...`` caption lines so they stand out from prose.

    Listing captions are always short natural-language descriptions placed
    immediately before a code block.  They never appear inside fenced code
    (they are titles *for* the code, not code themselves), so we skip the
    fragile in_code fence tracking that can desync on unbalanced fences and
    instead exclude only headings, blockquotes, and already-bold lines.
    """
    lines = text.split("\n")
    out = []
    for l in lines:
        s = l.strip()
        # skip lines already formatted as headings or inside blockquotes
        if s.startswith("#") or s.startswith(">") or s.startswith("**"):
            out.append(l)
            continue
        if _LISTING_CAP_RE.match(s):
            # strip any inner **...** fragments (merged margin notes) before
            # wrapping the whole line in bold to avoid nested-bold rendering bugs
            cleaned = re.sub(r"\*\*(.+?)\*\*", r"\1", l.rstrip())
            out.append(f"**{cleaned}**")
            continue
        out.append(l)
    return "\n".join(out)


# ============================================================================
# ⚠️ DO NOT REMOVE — 对应 PDF报告缺陷（图片文字混入图注正文）。
#    当 PDF 中图片（diagram）与图注文字位于同一水平行时，pymupdf4llm 会把图片内的
#    文字（通常为 bold 格式）与图注正文合并到同一行，导致图注中出现不属于正文的粗体片段。
#    本函数扫描 `> **Figure X.Y**` 图注行，解析其中的 bold 片段，移除词数 ≥4 的
#    bold 片段（这些几乎必然是图片内文字），保留短 bold 引用（变量名、类名、特殊 token
#    等，均 ≤3 词）。同时正确处理移除后的间距（避免双空格、句号前多余空格）。
# ============================================================================
_FIG_CAP_RE = re.compile(r"^>\s+\*\*Figure\s+\d+\.\d+\*\*")


def fix_figure_caption_diagram_text(text: str) -> str:
    """Remove diagram text (long bold segments) mixed into figure caption lines.

    When a PDF figure and its caption share the same horizontal band,
    pymupdf4llm merges in-image bold text into the caption line.  Diagram text
    fragments are typically ≥4 words (e.g. ``**The model is simply trained to**``),
    while legitimate bold references in captions (variable names, class names,
    special tokens) are ≤3 words.  This function removes the long bold segments
    and repairs spacing.
    """
    lines = text.split("\n")
    out = []
    for l in lines:
        s = l.strip()
        # Only process figure caption blockquote lines
        if not _FIG_CAP_RE.match(s):
            out.append(l)
            continue

        # Parse the line into (is_bold, text) segments
        segments = []  # list of (is_bold: bool, text: str)
        i = 0
        while i < len(l):
            if l[i:i+2] == "**":
                end = l.find("**", i + 2)
                if end == -1:
                    segments.append((False, l[i:]))
                    break
                segments.append((True, l[i+2:end]))
                i = end + 2
            else:
                j = l.find("**", i)
                if j == -1:
                    segments.append((False, l[i:]))
                    break
                segments.append((False, l[i:j]))
                i = j

        # Remove bold segments with >= 4 words (diagram text),
        # keep the initial **Figure X.Y** label and short bold references
        new_segments = []
        for idx, (is_bold, t) in enumerate(segments):
            if is_bold and idx > 0:  # skip the initial **Figure X.Y** label
                word_count = len(t.split())
                if word_count >= 4:
                    continue  # skip this diagram text segment
            new_segments.append((is_bold, t))

        if len(new_segments) == len(segments):
            out.append(l)  # no changes
            continue

        # Reconstruct the line, handling spacing around removed segments
        result_parts = []
        for idx, (is_bold, t) in enumerate(new_segments):
            if is_bold:
                result_parts.append(f"**{t}**")
            else:
                result_parts.append(t)

        result = "".join(result_parts)
        # Fix double spaces left by removed segments
        result = re.sub(r"  +", " ", result)
        # Fix space before sentence-ending punctuation (artifact of removal)
        result = re.sub(r"\s+([.!?])", r"\1", result)
        out.append(result)
    return "\n".join(out)


# ============================================================================
# ⚠️ DO NOT REMOVE — 内联代码标记（类似 SimpleTokenizerV1）。
#    PDF 中行内 `Courier`（如 SimpleTokenizerV1/V2、GPTModel 等）与正文
#    NewBaskerville 混排，pymupdf4llm 未转为 `` 包裹。此函数收集所有非代码块的
#    行内 Courier（含连字符跨行 Simple- + TokenizerV2 合并），在 md 中对未包裹的
#    同名 token 补 ``，跳过围栏/引用块内部，幂等。
# ============================================================================
def fix_inline_code(text: str, pdf=None) -> str:
    """Wrap inline Courier tokens (e.g., SimpleTokenizerV1) with ``."""
    # Collect inline Courier tokens from PDF if available, else fallback to common list
    candidates: set[str] = set()
    if pdf is not None:
        try:
            doc = pymupdf.open(pdf)
            for pno in range(len(doc)):
                d = doc[pno].get_text("dict")
                for b in d["blocks"]:
                    if b["type"] != 0:
                        continue
                    spans_all = [s for l in b["lines"] for s in l["spans"]]
                    total = sum(len(s["text"]) for s in spans_all)
                    if total == 0:
                        continue
                    code_chars = sum(len(s["text"]) for s in spans_all if s["font"].startswith("Courier"))
                    # Skip block-level code (already fenced)
                    if code_chars >= 12 and code_chars / total >= 0.75:
                        continue
                    # Collect Courier spans in this text block
                    for l in b["lines"]:
                        for s in l["spans"]:
                            if s["font"].startswith("Courier"):
                                t = s["text"].strip()
                                # Skip single punctuation / very short
                                if len(t) < 2 or t in ("--", ":", ".", ",", ";", "?", "!", "(", ")", "[", "]", "{", "}", '"', "'", "`"):
                                    continue
                                # Handle hyphenated line break: "Simple-" at line end will be merged later
                                if t.endswith("-") and len(t) > 2:
                                    # Keep as is for now, merging handled below
                                    candidates.add(t)
                                else:
                                    # Only keep plausible code identifiers (alnum + _)
                                    if re.search(r"[A-Za-z0-9_]", t):
                                        candidates.add(t)
            # Merge hyphenated splits: e.g., "Simple-" + "TokenizerV2" -> "SimpleTokenizerV2"
            # Look for candidates ending with "-" and another starting with continuation
            hyphenated = {c for c in candidates if c.endswith("-")}
            for h in list(hyphenated):
                prefix = h[:-1]
                for c in list(candidates):
                    if c != h and c.startswith(prefix[-3:]):  # rough
                        pass
            # More direct: scan PDF blocks for hyphenated Courier across lines
            for pno in range(len(doc)):
                d = doc[pno].get_text("dict")
                for b in d["blocks"]:
                    if b["type"] != 0:
                        continue
                    # Check for block where last line ends with "Simple-" and next block starts with "TokenizerV2"
                    # Handled via text merging in PDF blocks already: the block at y349 has "Simple-" and next block at y362 has "TokenizerV2"
                    # So we need to merge those two blocks' Courier tokens
                    pass
        except Exception:
            pass
    # Fallback: common inline code tokens that appear as plain text in md but should be `code`
    # This list is derived from PDF inline Courier collection above; keep minimal hard-coded for offline
    # NOTE: plain lowercase words like "tokenizer"/"vocab" are intentionally excluded – they are
    # normal English in the PDF (NewBaskerville) and must NOT be auto-wrapped; only PDF-derived
    # Courier tokens or CamelCase identifiers should be wrapped.
    fallback = {"SimpleTokenizerV1", "SimpleTokenizerV2", "SimpleTokenizerV2(vocab)", "GPTModel", "GPTDatasetV1", "TransformerBlock"}
    candidates |= fallback
    # Filter to only code-like identifiers, not common English words (e.g., "The")
    # Keep if: contains <| or _ or has >=2 capitals (CamelCase) or is in known list
    known = {"SimpleTokenizerV1", "SimpleTokenizerV2", "GPTModel", "GPTDatasetV1", "TransformerBlock"}
    filtered = set()
    for c in candidates:
        cc = c.strip(".,;:!?\"'()[]{}")
        if len(cc) < 2:
            continue
        if "<|" in cc:
            filtered.add(cc)
            continue
        if "_" in cc:
            filtered.add(cc)
            continue
        uppers = sum(1 for ch in cc if ch.isupper())
        if uppers >= 2:  # CamelCase with at least 2 caps, e.g., SimpleTokenizerV1
            filtered.add(cc)
            continue
        if cc in known:
            filtered.add(cc)
            continue
        # Also keep single CamelCase like "Hello" only if it's inside quotes in code? Skip common words
        # Common words like "The", "In" have only 1 capital and no _/<|, so they are excluded
    # Handle hyphenated split
    if "Simple-" in candidates and "TokenizerV2" in [c.strip(".,;:!?\"'()[]{}") for c in candidates]:
        filtered.add("SimpleTokenizerV2")
        filtered.discard("Simple-")
    candidates = filtered
    # Now wrap in md outside fences/quotes
    lines = text.split("\n")
    out_lines = []
    in_code = False
    for l in lines:
        stripped = l.strip()
        if stripped.startswith("```"):
            in_code = not in_code
            out_lines.append(l)
            continue
        if in_code or stripped.startswith(">"):
            out_lines.append(l)
            continue
        # For each candidate, wrap occurrences that are not already in ``
        # Use word boundaries; handle <|unk|> etc. specially
        new_l = l
        for cand in sorted(candidates, key=len, reverse=True):
            # Skip if already in ``
            if f"`{cand}`" in new_l:
                continue
            # Escape for regex
            esc = re.escape(cand)
            # Pattern: not preceded by ` or word char, not followed by ` or word char
            # For <|...|> tokens, word boundaries don't apply, use lookarounds for `
            pattern = re.compile(rf"(?<!`)(?<!\w){esc}(?!\w)(?!`)")
            # Only replace if candidate appears as plain text
            if pattern.search(new_l):
                new_l = pattern.sub(f"`{cand}`", new_l)
        out_lines.append(new_l)
    return "\n".join(out_lines)


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
    def _is_fence(l: str) -> bool:
        s = l.lstrip()
        if s.startswith(">"):
            s = s[1:].lstrip()
        return s.startswith("```")

    def _fence_kind(l: str) -> str | None:
        s = l.lstrip()
        if s.startswith(">"):
            s = s[1:].lstrip()
        if not s.startswith("```"):
            return None
        # opening has language tag python/bash, closing is plain ```
        if "python" in s or "bash" in s:
            return "open"
        return "close"

    lines = text.split("\n")
    out: list[str] = []
    in_code = False
    for i, line in enumerate(lines):
        # Fence lines update in_code and are never merged
        kind = _fence_kind(line)
        if kind is not None:
            out.append(line)
            if kind == "open":
                in_code = True
            else:
                in_code = False
            continue
        if i == 0 or not line.strip():
            out.append(line)
            continue
        if in_code:
            out.append(line)
            continue
        # line is non-empty prose candidate — check gap to last non-blank in out
        # Find last non-blank index in out (skip trailing blank lines left by
        # page-header/page-number removal which leaves 2-3 blanks).
        kb = len(out) - 1
        while kb >= 0 and out[kb].strip() == "":
            kb -= 1
        if kb < 0:
            out.append(line)
            continue
        before = out[kb].rstrip()
        has_blank_gap = kb != len(out) - 1
        if has_blank_gap:
            # A blank line normally ends a paragraph, so we don't merge across it.
            # EXCEPTION: pymupdf4llm splits a paragraph across a page boundary and
            # inserts blanks (page header + page number leave 2-3 blanks); if the
            # line *before* the blanks is plain prose (not ending in sentence
            # punctuation, not an index/citation entry) and the current line
            # starts lower-case, it is the same paragraph -> join and drop blanks.
            if (not before.endswith((":", ".", "!", "?", ";", '"', ")", "]", "}", "”"))
                    and not before.startswith(("#", "-", "*", ">", "1.", "2.", "3.", "```"))
                    and not re.search(r",\s*\d{1,3}\s*$", before)):
                stripped = line.lstrip()
                first_word = re.match(r"[A-Za-z]+", stripped)
                if first_word and first_word.group(0)[0].islower():
                    out[kb] = before + " " + stripped
                    del out[kb + 1:]  # drop all blank lines between the two halves
                    continue
            out.append(line)
            continue
        # no blank gap: direct hard break inside same page
        prev = out[-1]
        prev_stripped = prev.rstrip()
        # boundaries that must NOT be merged across
        if (prev_stripped.endswith((":", ".", "!", "?", ";", '"', ")", "]", "}", "”"))
                or prev_stripped.startswith(("#", "-", "*", ">", "1.", "2.", "3.", "```"))):
            out.append(line)
            continue
        stripped = line.lstrip()
        first_word = re.match(r"[A-Za-z]+", stripped)
        if first_word and first_word.group(0)[0].islower():
            out[-1] = prev.rstrip() + " " + stripped
        else:
            out.append(line)
    return "\n".join(out)


# ============================================================================
# ⚠️ DO NOT REMOVE — 对应 PDF报告缺陷 #8e（同页代码块被拆成两个围栏）。
#    pymupdf dict 会把同一视觉代码/输出块拆成多个 dict block（如 p44 输出
#    "I HAD ... fellow " + "enough--so it was no" 因换行缩进被拆成两个 block，
#    间距仅 2pt），导致流匹配产生两个相邻 ```python 围栏，中间仅空行。此函数
#    合并相邻围栏：若两个围栏语言相同、中间仅空行、且后块首行像续行（小写/数字/
#    括号/引号开头），则合并为单一围栏。作为提取阶段合并的幂等兜底。
# ============================================================================
def fix_split_code_fences(text: str) -> str:
    """Merge adjacent fenced code blocks that were split from the same PDF block.

    Two fences separated only by blank lines and sharing the same language tag
    are merged if the second fence's first content line looks like a continuation
    (lowercase, digit, bracket, quote, etc.) rather than a new statement. This
    is idempotent: already-merged blocks are not re-split.
    """
    lines = text.split("\n")
    out = []
    i = 0
    n = len(lines)
    fence_open_re = re.compile(r"^\s*```(python|bash)\s*$")
    fence_close_re = re.compile(r"^\s*```\s*$")
    while i < n:
        m_open = fence_open_re.match(lines[i])
        if not m_open:
            out.append(lines[i])
            i += 1
            continue
        lang = m_open.group(1)
        # find closing fence of this block
        j = i + 1
        while j < n and not fence_close_re.match(lines[j]):
            j += 1
        if j >= n:
            out.append(lines[i])
            i += 1
            continue
        # look ahead: skip blank lines after closing fence
        k = j + 1
        while k < n and lines[k].strip() == "":
            k += 1
        if k >= n:
            out.extend(lines[i:j+1])
            i = j + 1
            continue
        m_next_open = fence_open_re.match(lines[k])
        if not m_next_open or m_next_open.group(1) != lang:
            out.extend(lines[i:j+1])
            i = j + 1
            continue
        # find closing of next block
        l = k + 1
        while l < n and not fence_close_re.match(lines[l]):
            l += 1
        if l >= n:
            out.extend(lines[i:j+1])
            i = j + 1
            continue
        # iterative chain merge: keep merging while next block looks like continuation
        cur_open = i
        cur_close = j
        cur_lang = lang
        merged_content = lines[cur_open+1:cur_close]
        k_cur = cur_close + 1
        merged_any = False
        while True:
            # skip blank lines
            while k_cur < n and lines[k_cur].strip() == "":
                k_cur += 1
            if k_cur >= n:
                break
            m_next = fence_open_re.match(lines[k_cur])
            if not m_next or m_next.group(1) != cur_lang:
                break
            l_cur = k_cur + 1
            while l_cur < n and not fence_close_re.match(lines[l_cur]):
                l_cur += 1
            if l_cur >= n:
                break
            # heuristic for this next block
            first_content = ""
            for t in range(k_cur+1, l_cur):
                if lines[t].strip() != "":
                    first_content = lines[t].lstrip()
                    break
            if not first_content:
                break
            is_cont = False
            if first_content[0].islower() or first_content[0].isdigit() or first_content[0] in ('[', '(', '{', '"', "'", '-', '.', '/', '<', '#', ']', ')', '}'):
                is_cont = True
            next_block_lines = [lines[t] for t in range(k_cur+1, l_cur) if lines[t].strip() != ""]
            prev_block_lines = [l for l in merged_content if l.strip() != ""]
            if len(next_block_lines) == 1 and first_content[0].islower():
                is_cont = True
            if prev_block_lines and next_block_lines:
                if first_content.startswith(("import ", "from ", "def ", "class ", "with ", "for ", "if ", "print(", "self.", "return ", "raise ", "else", "elif ")):
                    is_cont = False
                # for lowercase continuation, require single line fragment (wrap case) to avoid merging distinct statements like "self.xxx ="
                if first_content[0].islower() and len(next_block_lines) > 1:
                    # allow bracket/digit continuations to be multi-line (tensor output), but not plain lowercase statements
                    if first_content[0] not in ('[', '(', '{', '"', "'", '-', '.', '/', '<'):
                        is_cont = False
            if not is_cont:
                break
            # merge this next block
            merged_content.extend(lines[k_cur+1:l_cur])
            cur_close = l_cur
            k_cur = l_cur + 1
            merged_any = True
        if merged_any:
            out.append(lines[cur_open])
            out.extend(merged_content)
            out.append(lines[cur_close])
            i = cur_close + 1
            continue
        else:
            out.extend(lines[i:j+1])
            i = j + 1
            continue
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


# ============================================================================
# ⚠️ DO NOT REMOVE — 误判为标题的短句（如 The output is）。
#    pymupdf4llm 将正文短句 `The output is` 误判为 `######` 标题（5 处），
#    其后紧跟代码块，实为段落标签而非结构标题。此函数将其转回普通段落。
# ============================================================================
def fix_false_output_headings(text: str) -> str:
    """Convert false headings like `#### The output is` back to plain text."""
    return re.sub(r"^#{1,6}\s+The output is\s*\n(\s*\n)?```", "The output is\n\n```", text, flags=re.M)


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
                    if "BoldItali" in s["font"] or "BoldItalic" in s["font"] or "HumanistMann521-BoldCond" in s["font"]:
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
                                if y0 - 30 <= yc <= y1 + 10 and (ax > x1 - 80 or yc < y0) and t)
            # Cluster note_spans by proximity in y and x (side annotations are multi-line)
            clusters: list[list[str]] = []
            cluster_first_y: list[float] = []
            cluster_first_x: list[float] = []
            cluster_last_y: list[float] = []
            cluster_last_x: list[float] = []
            for yc, ax, t in sorted(note_spans, key=lambda x: (x[0], x[1])):
                placed = False
                for idx in range(len(clusters)):
                    if abs(yc - cluster_last_y[idx]) < 15 and abs(ax - cluster_last_x[idx]) < 80:
                        clusters[idx].append(t)
                        cluster_last_y[idx] = yc
                        cluster_last_x[idx] = ax
                        placed = True
                        break
                if not placed:
                    clusters.append([t])
                    cluster_first_y.append(yc)
                    cluster_first_x.append(ax)
                    cluster_last_y.append(yc)
                    cluster_last_x.append(ax)
            # Sort clusters by y (top to bottom), and for similar y (<15) by x left to right
            order = sorted(range(len(clusters)), key=lambda i: (round(cluster_first_y[i] / 15) * 15, cluster_first_x[i]))
            notes = [" ".join(clusters[i]) for i in order]
            blks.append({"lines": lines, "flat": flat, "y0": y0, "y1": y1, "x0": x0,
                         "notes": notes, "keys": block_keys(lines), "used": False})
        blks.sort(key=lambda b: b["y0"])
        # --- merge adjacent Courier blocks that belong to same logical snippet ---
        # pymupdf dict sometimes splits a single visual code/output block into
        # multiple dict blocks due to line-wrap indent or block segmentation.
        # e.g., p44: "I HAD ... fellow " (y1=277) + "enough--so it was no" (y0=279,
        # gap=2pt, x0 shift 24pt) should be one fence but were emitted as two.
        # Other pages split class/method bodies with gap ~12pt.
        # Gap <15pt is < typical prose separation (>20pt) so safe.
        if len(blks) > 1:
            merged = []
            for blk in blks:
                if merged:
                    prev = merged[-1]
                    gap = blk["y0"] - prev["y1"]
                    # gap <15 covers wrapped output (2pt), tensor (4-6pt) and same-class splits (12pt, e.g., SimpleTokenizerV2 p53)
                    # larger gaps (>20) with prose between remain separate per known limitation.
                    if gap < 15:
                        prev["lines"].extend(blk["lines"])
                        prev["flat"] = norm("".join(prev["lines"]))
                        prev["keys"] = block_keys(prev["lines"])
                        prev["y1"] = blk["y1"]
                        prev["notes"].extend(blk["notes"])
                        continue
                merged.append(blk)
            blks = merged
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
    final = "\n\n".join(pages_text)
    final = final.replace("](pdf_to_md/output/images/", "](images/")
    final = final.replace("](output/images/", "](images/")
    # also handle bare output/images without leading ](
    final = re.sub(r"\(pdf_to_md/output/images/", "(images/", final)
    final = re.sub(r"\(output/images/", "(images/", final)
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
    final = fix_split_code_fences(final)   # [8e] merge split fences from same PDF block (e.g., p44 output)
    final = strip_page_headers(final)      # [4b] drop running-header words
    final = pair_figures_captions(final, pdf)   # [7] figure captions -> blockquotes (with PDF for truncation)
    final = fix_split_figures(final, pdf, img_dir)  # [8b] reassemble split figures
    final = fix_figure_label_headings(final, pdf, img_dir)  # [8c] restore vector-fig top labels
    final = fix_missing_figures(final, pdf, img_dir)       # [8d] caption-only figures -> extract image
    final = remove_text_inside_figures_and_side_notes(final, pdf)  # generic: remove figure/side-note duplicates now inside images
    final = remove_broken_figure_refs(final, img_dir)  # drop broken figure-*.png refs where file missing
    final = fix_code_side_annotations(final, pdf)  # generic side-note cleanup (fallback hard-coded kept for offline)
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
    # bold standalone `Table X.Y ...` caption lines so they stand out from prose
    final = fix_table_captions(final)        # [12] table captions -> bold
    # bold standalone `Listing X.Y ...` caption lines so they stand out from prose
    final = fix_listing_captions(final)      # [12c] listing captions -> bold
    # remove diagram text (long bold segments) mixed into figure caption lines
    final = fix_figure_caption_diagram_text(final)  # [13] figure caption diagram text
    final = fix_inline_code(final, pdf)  # inline `code` for Courier in body (e.g., SimpleTokenizerV1)
    # convert bold-italic labels misread as headings back to bold (**_label_**)
    final = fix_faux_headings(final)
    final = fix_false_output_headings(final)  # `#### The output is` -> plain text
    # fix heading levels (###### for sections -> ###/#### by structure)
    final = relevel_headings(final)
    final = fix_split_code_fences(final)   # [8e] second pass: catch any fences split by later steps
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