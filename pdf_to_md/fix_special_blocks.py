#!/usr/bin/env python3
"""
fix_special_blocks.py - Fix distinct-format blocks (callouts, listings, NOTE admonitions) that were merged into prose.

Categories:
- Callouts (info/probability explanations, Exercises) with fill (0.969,0.961,0.91), title Demi 10.5 -> `> **Title**` + `> body`
- Listing headers with fill (0.438,0.652,0.801), white Demi 9 -> `**Listing X.Y Title**` before code fence
- NOTE admonitions: first span "NOTE" FranklinGothic-Demi 8.5 + body Roman 10 -> `> **NOTE** body` (distinct from inline "Note that" Roman 10)

Detection is based purely on PDF structural signals (color+size+font+overlap), no hard-coded strings.

Usage:
  python fix_special_blocks.py          # dry-run, preview changes
  python fix_special_blocks.py --apply  # apply to llms-from-scratch.md
"""
import re
import sys
import unicodedata
from pathlib import Path
import fitz

PDF = Path(__file__).parent / "Build a Large Language Model (From Scratch) (Sebastian Raschka).pdf"
MD = Path(__file__).parent / "llms-from-scratch.md"

CALLOUT_FILL = (0.969, 0.961, 0.91)
LISTING_FILL = (0.438, 0.652, 0.801)

def norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", s)
    s = s.replace("\u00AD", "")  # soft hyphen
    # dehyphenate: word- space word -> wordword (but keep self-attention etc where hyphen has no space)
    s = re.sub(r'(\w+)-\s+(\w+)', r'\1\2', s)
    s = re.sub(r'\s+', ' ', s).strip().lower()
    return s

def norm_keep_case(s: str) -> str:
    """NFKC + whitespace collapse but keep case, for display comparison."""
    s = unicodedata.normalize("NFKC", s)
    s = s.replace("\u00AD", "")
    s = re.sub(r'(\w+)-\s+(\w+)', r'\1\2', s)
    s = re.sub(r'\s+', ' ', s).strip()
    return s

def collect_concept_boxes(pdf_path=PDF):
    doc = fitz.open(str(pdf_path))
    boxes = []
    for pno in range(len(doc)):
        page = doc[pno]
        draws = [d for d in page.get_drawings() if d.get("fill") and tuple(round(v, 3) for v in d["fill"]) == CALLOUT_FILL]
        for dr in draws:
            r = dr["rect"]
            if r.width < 100 or r.height < 40:
                continue
            # collect overlapping text blocks
            blks = []
            for b in page.get_text("dict")["blocks"]:
                if "lines" not in b:
                    continue
                bx = fitz.Rect(b["bbox"])
                inter = bx & r
                if inter.is_empty:
                    continue
                if inter.get_area() / bx.get_area() < 0.20:
                    continue
                blks.append(b)
            if not blks:
                continue
            blks.sort(key=lambda b: b["bbox"][1])
            # skip This chapter covers (handled by fix_chapters)
            has_chapter = any("This chapter covers" in "".join("".join(s["text"] for s in line["spans"]) for line in b["lines"]) for b in blks)
            if has_chapter:
                continue
            # detect title: first block first span Demi 10.5
            first = blks[0]
            fs = first["lines"][0]["spans"][0] if first["lines"] and first["lines"][0]["spans"] else None
            has_title = fs and "Demi" in fs["font"] and abs(fs["size"] - 10.5) < 0.8
            title = ""
            bodies = []
            if has_title:
                title = "".join(s["text"] for s in first["lines"][0]["spans"]).strip()
                # bodies: remaining lines of first block + other blocks
                if len(first["lines"]) > 1:
                    body_first = " ".join("".join(s["text"] for s in line["spans"]) for line in first["lines"][1:]).strip()
                    if body_first and "\uf0a1" not in body_first:
                        bodies.append(body_first)
                for b in blks[1:]:
                    txt = " ".join("".join(s["text"] for s in line["spans"]) for line in b["lines"]).strip()
                    if not txt or "\uf0a1" in txt:
                        continue
                    # filter very short figure labels that slipped in (e.g., pure numbers)
                    bodies.append(txt)
            else:
                # untitled continuation box: all blocks are bodies
                for b in blks:
                    txt = " ".join("".join(s["text"] for s in line["spans"]) for line in b["lines"]).strip()
                    if not txt or "\uf0a1" in txt:
                        continue
                    bodies.append(txt)
            # filter empty
            if not title and not bodies:
                continue
            # For untitled, skip if it's actually table-like tiny? But keep all
            boxes.append({
                "page": pno + 1,
                "has_title": has_title,
                "title": title,
                "bodies": bodies,
                "rect": r,
            })
    doc.close()
    # Merge untitled continuations into previous titled? For now keep separate; caller will handle
    # But to avoid double-counting, we keep all; V1 counts titled only
    return boxes

def collect_listing_headers(pdf_path=PDF):
    doc = fitz.open(str(pdf_path))
    listings = []
    for pno in range(len(doc)):
        page = doc[pno]
        draws = [d for d in page.get_drawings() if d.get("fill") and tuple(round(v, 3) for v in d["fill"]) == LISTING_FILL]
        for dr in draws:
            r = dr["rect"]
            # header text overlapping rect
            header = ""
            header_block = None
            for b in page.get_text("dict")["blocks"]:
                if "lines" not in b:
                    continue
                bx = fitz.Rect(b["bbox"])
                inter = bx & r
                if inter.is_empty or inter.get_area() < 5:
                    continue
                txt = "".join("".join(s["text"] for s in line["spans"]) for line in b["lines"]).strip()
                if txt:
                    header = txt
                    header_block = b
                    break
            if not header:
                continue
            # parse Listing X.Y Title (handle missing space after number)
            m = re.match(r'(Listing\s+[A-Za-z0-9]+\.\d+)\s*(.*)', header)
            if m:
                num = m.group(1).strip()
                tit = m.group(2).strip()
                full = f"{num} {tit}".strip()
            else:
                full = header
                num = header.split()[0] if header else ""
            # find next Courier block after header as anchor
            # collect blocks sorted by y
            all_blocks = [b for b in page.get_text("dict")["blocks"] if "lines" in b]
            all_blocks.sort(key=lambda b: b["bbox"][1])
            header_y = r.y1
            anchor = ""
            anchor_block_text = ""
            found = False
            for b in all_blocks:
                if b["bbox"][1] < header_y - 1:
                    continue
                has_courier = any("Courier" in s["font"] for line in b["lines"] for s in line["spans"])
                if not has_courier:
                    continue
                txt = " ".join("".join(s["text"] for s in line["spans"]) for line in b["lines"]).strip()
                if not txt or "Listing" in txt:
                    continue
                # skip if same as header
                if norm(txt) == norm(header):
                    continue
                anchor = txt[:120].strip()
                anchor_block_text = txt
                found = True
                break
            if not found and pno + 1 < len(doc):
                nxt_page = doc[pno + 1]
                nxt_blocks = [b for b in nxt_page.get_text("dict")["blocks"] if "lines" in b]
                nxt_blocks.sort(key=lambda b: b["bbox"][1])
                for b in nxt_blocks:
                    has_courier = any("Courier" in s["font"] for line in b["lines"] for s in line["spans"])
                    if not has_courier:
                        continue
                    txt = " ".join("".join(s["text"] for s in line["spans"]) for line in b["lines"]).strip()
                    if not txt:
                        continue
                    anchor = txt[:120].strip()
                    anchor_block_text = txt
                    break
            listings.append({
                "page": pno + 1,
                "full": full,  # e.g., "Listing 2.1 Reading ..."
                "anchor": anchor,
                "anchor_full": anchor_block_text,
                "rect": r,
            })
            break  # one listing per draw rect, but per page usually one; break to avoid duplicate rects for same header
    doc.close()
    # Deduplicate by full (some headers have two rects for same listing)
    seen = set()
    uniq = []
    for lst in listings:
        if lst["full"] not in seen:
            uniq.append(lst)
            seen.add(lst["full"])
    return uniq

def find_fence_containing_anchor(anchor, fences, lines):
    """Find fence index whose content contains anchor substring (normalized)."""
    if not anchor:
        return None
    na = norm(anchor)
    # use first 40 chars of anchor for robust substring
    key = na[:40].strip()
    if len(key) < 10:
        key = na
    best = None
    best_score = -1
    for idx, (s, e, content) in enumerate(fences):
        nc = norm(content)
        if key and key in nc:
            # score by position proximity to expected? Just pick first with key
            # prefer longer key match
            score = len(key)
            if score > best_score:
                best = idx
                best_score = score
    return best

def extract_fences(lines):
    fences = []  # list of (start_idx, end_idx, content)
    i = 0
    while i < len(lines):
        if lines[i].strip().startswith("```"):
            s = i
            i += 1
            content_lines = []
            while i < len(lines) and not lines[i].strip().startswith("```"):
                content_lines.append(lines[i])
                i += 1
            e = i  # closing fence line
            content = "\n".join(content_lines)
            fences.append((s, e, content))
        i += 1
    return fences

def fix_blockquote_blanks(lines):
    """Ensure every blank line *inside* a callout (between two `> ` blocks) is `>`."""
    fixes = 0
    for i in range(1, len(lines) - 1):
        if lines[i].strip() == "" and lines[i - 1].strip().startswith(">") and lines[i + 1].strip().startswith(">"):
            # If next is a new callout title, keep blank as separator (between callouts)
            if lines[i + 1].strip().startswith("> **"):
                continue
            lines[i] = ">"
            fixes += 1
    return fixes


def fix_callouts(lines, boxes):
    """Patch lines in-place for callout boxes. Returns fix count and details."""
    fixes = 0
    details = []
    # track which line indices have been converted to blockquote to avoid double
    converted = set()
    for box in boxes:
        title = box["title"]
        bodies = box["bodies"]
        has_title = box["has_title"]
        page = box["page"]
        if not has_title:
            # untitled continuation: convert each body paragraph that is plain and found in md
            for body in bodies:
                nb = norm(body)
                if len(nb) < 20:
                    continue
                key = nb[:50]
                found = None
                for idx, line in enumerate(lines):
                    if idx in converted:
                        continue
                    if line.strip().startswith(">") or line.strip().startswith("**") or line.strip().startswith("#") or line.strip().startswith("```") or line.strip().startswith("!["):
                        continue
                    if not line.strip():
                        continue
                    if key and key in norm(line):
                        found = idx
                        break
                if found is not None:
                    # convert this body line to blockquote
                    if lines[found].strip().startswith(">"):
                        continue
                    lines[found] = f"> {lines[found].strip()}"
                    converted.add(found)
                    fixes += 1
                    details.append(f"p{page} untitled body -> > at md line {found+1}: {body[:60]!r}")
            continue

        # titled box
        nt = norm(title)
        # search for title line
        found_idx = None
        already_blockquote = False
        for idx, line in enumerate(lines):
            if idx in converted:
                continue
            if not line.strip():
                continue
            if nt and nt in norm(line):
                if line.strip().startswith(">"):
                    already_blockquote = True
                    break
                found_idx = idx
                break
        if already_blockquote:
            # already converted to blockquote, skip
            continue
        if found_idx is None:
            # title missing, try to find first body instead and insert title before it
            if not bodies:
                details.append(f"p{page} MISSING title {title!r} (no bodies)")
                continue
            # search for first body
            nb0 = norm(bodies[0])
            key0 = nb0[:50] if len(nb0) > 50 else nb0
            body_idx = None
            for idx, line in enumerate(lines):
                if idx in converted:
                    continue
                if not line.strip() or line.strip().startswith(">") or line.strip().startswith("#"):
                    continue
                if key0 and key0 in norm(line):
                    body_idx = idx
                    break
            if body_idx is not None:
                # insert title before body line as blockquote
                # insert at body_idx position
                insertion = [f"> **{title}**", ">", f"> {lines[body_idx].strip()}"]
                # replace body line with blockquote body, and insert title lines before
                lines[body_idx] = insertion[2]
                lines[body_idx:body_idx] = insertion[:2]  # insert title lines before
                # mark converted
                # note indices shifted after insertion; we need to adjust tracking
                # For simplicity, just count and record
                fixes += 1
                details.append(f"p{page} MISSING title inserted before body at md line {body_idx+1}: {title!r} (body {bodies[0][:40]!r})")
                # Need to handle remaining bodies[1..] if any, but we'll handle them as separate untitled? For now, bodies[1..] will be handled in next iterations as following lines matching
                # For bodies[1..], try to find subsequent lines
                for k in range(1, len(bodies)):
                    nb = norm(bodies[k])
                    key = nb[:50]
                    found_body = None
                    # search after body_idx + offset (need to account for inserted lines)
                    search_start = body_idx + 1 + k*2  # heuristic blank separated
                    # instead do linear search after body_idx
                    for idx2 in range(body_idx+1, min(len(lines), body_idx+30)):
                        if idx2 in converted:
                            continue
                        if not lines[idx2].strip() or lines[idx2].strip().startswith(">"):
                            continue
                        if key and key in norm(lines[idx2]):
                            found_body = idx2
                            break
                    if found_body is not None:
                        lines[found_body] = f"> {lines[found_body].strip()}"
                        converted.add(found_body)
                        fixes += 1
                        details.append(f"p{page} title {title!r} body {k+1} -> > at line {found_body+1}")
            else:
                details.append(f"p{page} MISSING both title and body {title!r} bodies[0]={bodies[0][:40]!r} not found")
            continue

        # title found plain
        line_content = lines[found_idx]
        # extract suffix after title (if fused)
        # case-insensitive locate title in line_content
        lower_line = line_content.lower()
        lower_title = title.lower()
        pos = lower_line.find(lower_title)
        suffix = ""
        if pos != -1:
            suffix = line_content[pos + len(title):].strip()
            # remove leading punct like : or -  
            suffix = suffix.lstrip(" :—-").strip()
        # Build replacement for title line
        # If suffix non-empty, it is first body paragraph fused; we will create blockquote with title + suffix
        # Mark original line for replacement
        # Create new blockquote lines to replace single line
        new_block = [f"> **{title}**", ">"]
        if suffix:
            # suffix may still contain rest of body0; ensure not empty
            # If suffix length is tiny (<20) maybe it's incomplete due to title truncation, use bodies[0] instead
            if len(suffix) < 20 and bodies:
                # use bodies[0] as more complete
                suffix_to_use = bodies[0]
            else:
                suffix_to_use = suffix
            new_block.append(f"> {suffix_to_use}")
        else:
            # title line had no body suffix (title alone), first body is separate paragraph(s) after
            # We'll add bodies[0] as next blockquote line if exists, but that body is at next md line; we will handle it separately via following lines conversion
            # For now, just title block without body in same line
            pass

        # Replace title line with new_block (1 line -> 2 or 3 lines)
        # new_block length n, we need to replace 1 line with n lines
        lines[found_idx:found_idx+1] = new_block
        # Adjust converted indices shift: any converted indices > found_idx should be offset by len(new_block)-1
        shift = len(new_block) - 1
        # update converted set
        new_converted = set()
        for c in converted:
            if c > found_idx:
                new_converted.add(c + shift)
            else:
                new_converted.add(c)
        converted = new_converted
        # mark new lines as converted
        for off in range(len(new_block)):
            converted.add(found_idx + off)
        fixes += 1
        details.append(f"p{page} titled {title!r} -> > block at md line {found_idx+1} (suffix {suffix[:40]!r})")

        # Now handle remaining bodies[1..] (since bodies[0] already handled via suffix if present)
        # If suffix was used and bodies[0] corresponds to suffix, remaining bodies are bodies[1:]
        # If suffix empty, remaining bodies are bodies[0:]
        remaining_bodies = bodies[1:] if suffix else bodies
        # For each remaining body, find next plain paragraph matching it and convert to > 
        # Search forward from found_idx+len(new_block)
        search_base = found_idx + len(new_block)
        for k, body in enumerate(remaining_bodies):
            nb = norm(body)
            if len(nb) < 20:
                continue
            key = nb[:50]
            found_body = None
            # scan next ~30 lines after search_base + k*2
            for idx2 in range(search_base, min(len(lines), search_base + 40)):
                if idx2 in converted:
                    continue
                if not lines[idx2].strip():
                    continue
                if lines[idx2].strip().startswith(">") or lines[idx2].strip().startswith("#") or lines[idx2].strip().startswith("```") or lines[idx2].strip().startswith("!["):
                    continue
                if key and key in norm(lines[idx2]):
                    found_body = idx2
                    break
            if found_body is not None:
                lines[found_body] = f"> {lines[found_body].strip()}"
                converted.add(found_body)
                fixes += 1
                details.append(f"p{page} titled {title!r} body {k+1} extra -> > at line {found_body+1}: {body[:60]!r}")
            else:
                # body not found as separate line, maybe fused or missing; log
                details.append(f"p{page} titled {title!r} body {k+1} NOT FOUND: {body[:60]!r}")

    return fixes, details

def fix_notes(lines):
    """Fix distinct NOTE admonitions: `^NOTE ` (all caps, Demi 8.5) -> `> **NOTE** body`.

    Inline `Note that` (Roman 10, capital N only) is NOT converted – it has no
    Demi prefix and stays plain. This is checked by matching only `^NOTE ` (all caps).
    """
    fixes = 0
    details = []
    for i, l in enumerate(lines):
        if re.match(r'^NOTE\s', l):
            rest = l[4:].lstrip()
            lines[i] = f"> **NOTE** {rest}"
            fixes += 1
            details.append(f"NOTE at md line {i+1} -> > **NOTE** ({rest[:60]!r})")
        elif re.match(r'^> NOTE\s', l):
            # already blockquote but without bold, fix to > **NOTE**
            rest = re.sub(r'^>\s*NOTE\s*', '', l).lstrip()
            lines[i] = f"> **NOTE** {rest}"
            fixes += 1
            details.append(f"NOTE (already >) at md line {i+1} -> > **NOTE**")
    return fixes, details


def fix_listings(lines, listings):
    fixes = 0
    details = []
    fences = extract_fences(lines)
    # map fence index -> start line
    # For each listing, check if md already has **Listing
    existing = set()
    for idx, line in enumerate(lines):
        m = re.match(r'\*\*Listing\s+[A-Za-z0-9]+\.\d+', line.strip())
        if m:
            # extract number like Listing 2.1
            num_m = re.search(r'Listing\s+([A-Za-z0-9]+\.\d+)', line)
            if num_m:
                existing.add(num_m.group(1))
    for lst in listings:
        full = lst["full"]
        # full like "Listing 2.1 Reading ..."
        num_m = re.search(r'Listing\s+([A-Za-z0-9]+\.\d+)', full)
        num = num_m.group(1) if num_m else full
        if num in existing:
            continue  # already present
        anchor = lst["anchor"]
        # find fence containing anchor
        fence_idx = find_fence_containing_anchor(anchor, fences, lines) if anchor else None
        if fence_idx is None:
            # fallback: try with anchor_full if anchor was truncated
            anchor_full = lst.get("anchor_full", "")
            if anchor_full:
                fence_idx = find_fence_containing_anchor(anchor_full, fences, lines)
        if fence_idx is None:
            # fallback to order: listings appear sequentially; map nth listing to nth fence in order
            # Find nth fence that hasn't been assigned?
            # For now, log missing
            details.append(f"p{lst['page']} listing {full!r} anchor {anchor!r} NOT FOUND in md fences")
            continue
        s, e, content = fences[fence_idx]
        # insert caption before fence start s
        caption = f"**{full}**"
        # insert with blank line? Format: caption line, blank line, fence
        # Check if already caption just before fence
        if s > 0 and re.match(r'\*\*Listing', lines[s-1].strip()):
            continue
        # Insert
        lines[s:s] = [caption, ""]
        fixes += 1
        details.append(f"p{lst['page']} listing {full!r} -> inserted before fence at md line {s+1} (anchor {anchor[:40]!r})")
        # update fences indices after insertion
        fences = extract_fences(lines)
        existing.add(num)
    return fixes, details

def main():
    apply = "--apply" in sys.argv
    md_text = MD.read_text(encoding="utf-8")
    lines = md_text.split("\n")
    print(f"Loaded md: {len(lines)} lines, {len([l for l in lines if l.strip().startswith('```')])} fence markers")

    print("Collecting concept boxes from PDF...")
    boxes = collect_concept_boxes()
    titled = [b for b in boxes if b["has_title"]]
    untitled = [b for b in boxes if not b["has_title"]]
    print(f"  found {len(boxes)} boxes (titled {len(titled)} untitled {len(untitled)})")

    print("Collecting listing headers from PDF...")
    listings = collect_listing_headers()
    print(f"  found {len(listings)} listings")

    # Fix callouts
    print("\n--- Fixing callouts ---")
    c_fixes, c_details = fix_callouts(lines, boxes)
    for d in c_details[:40]:
        print("  ", d)
    if len(c_details) > 40:
        print(f"  ... {len(c_details)-40} more")
    # Fix blank lines inside callouts (every line inside a callout must be `>`)
    b_fixes = fix_blockquote_blanks(lines)
    if b_fixes:
        print(f"  Fixed {b_fixes} blank lines inside callouts to `>`")
        c_fixes += b_fixes

    # Fix NOTE admonitions (distinct from inline Note that)
    print("\n--- Fixing NOTE admonitions ---")
    n_fixes, n_details = fix_notes(lines)
    for d in n_details[:20]:
        print("  ", d)
    if len(n_details) > 20:
        print(f"  ... {len(n_details)-20} more")

    # Fix listings
    print("\n--- Fixing listings ---")
    l_fixes, l_details = fix_listings(lines, listings)
    for d in l_details[:40]:
        print("  ", d)
    if len(l_details) > 40:
        print(f"  ... {len(l_details)-40} more")

    print(f"\nSummary: callout fixes {c_fixes}, NOTE fixes {n_fixes}, listing fixes {l_fixes}, total {c_fixes + n_fixes + l_fixes}")

    # Verification
    # V1 titled callout count
    v1_cnt = sum(1 for l in lines if re.match(r'>\s*\*\*', l.strip()) and any(norm(b["title"]) in norm(l) for b in titled if b["has_title"]))
    # More accurate: count blocks starting with > **
    v1_blocks = sum(1 for l in lines if l.strip().startswith("> **"))
    print(f"V1 titled callout blocks in md: {v1_blocks} (expected {len(titled)})")
    v2_cnt = sum(1 for l in lines if re.match(r'\*\*Listing\s+[A-Za-z0-9]+\.\d+', l.strip()))
    print(f"V2 listing bold lines: {v2_cnt} (expected {len(listings)})")
    # V4
    fences = [l for l in lines if l.strip().startswith("```")]
    print(f"V4 fences: {len(fences)} even={len(fences)%2==0}")
    # §3 assertions
    text = "\n".join(lines)
    import re as re2
    has_pua = bool(re2.search(r"[\uF000-\uF0FF]", text))
    print(f"V5 PUA present: {has_pua} (should be False)")
    # check inline Note not converted
    inline_note_converted = 0
    for i, l in enumerate(lines):
        if l.strip().startswith(">") and "note that" in l.lower():
            # check if this was inline note (should be few, but our callouts that start with Note that are legitimate callout bodies, not inline)
            # For verification, count plain inline notes that are now blockquote incorrectly
            # Inline notes are those where title is not callout but body starts with Note that and page not in callout
            inline_note_converted += 1
    print(f"V3 inline Note converted to blockquote (should be 0 or only legitimate callout bodies): {inline_note_converted} (manual review)")

    if apply:
        MD.write_text("\n".join(lines), encoding="utf-8")
        print(f"\nApplied: wrote {MD}")
    else:
        print("\nDry-run done. Use --apply to write.")

if __name__ == "__main__":
    main()
