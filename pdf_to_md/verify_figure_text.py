#!/usr/bin/env python3
"""
Verification for the figure-internal text removal sub-module.

Mirrors the P0 image-channel verification style (V1-V4). Run after
pdf_text_stream.py (which now drops figure-internal text via
figure_text_detect.py).

    python verify_figure_text.py <book.pdf> <final_md>

Checks
------
V1 count   : dropped figure-text blocks in a sane range (>=300, <=2500).
V2 no-body : a sample of dropped blocks contains NO complete sentences
             (terminal punctuation) -- i.e. the sentence guard holds and we
             are not deleting body prose.
V3 keep-term: glossary terms that sit beside callout figures are still present
             in the final markdown (not deleted).
V4 drop-label: known figure labels (e.g. "STAGE 1", "Data preparation & sampling")
             are absent from the final markdown (correctly removed).
"""

import re
import sys
from pathlib import Path

import fitz

sys.path.insert(0, str(Path(__file__).parent))
from figure_text_detect import detect_page_figure_text


def main():
    if len(sys.argv) < 3:
        raise SystemExit("usage: verify_figure_text.py <book.pdf> <final_md>")
    pdf_path, md_path = sys.argv[1], sys.argv[2]

    # V1 + V2 : scan PDF for dropped blocks
    doc = fitz.open(pdf_path)
    dropped = []
    for pno in range(len(doc)):
        page = doc[pno]
        d, _ = detect_page_figure_text(page, page.rect.height)
        for b in d:
            txt = " ".join(s["text"] for l in b["lines"] for s in l["spans"]).strip()
            if txt:
                dropped.append(txt)
    doc.close()

    n = len(dropped)
    v1 = 300 <= n <= 2500
    print(f"[V1] dropped figure-text blocks: {n}  -> {'PASS' if v1 else 'FAIL'}")

    # V2 : no complete sentences among dropped (sample 200)
    sentence_like = [t for t in dropped if t and t[-1] in ".?!"]
    v2 = len(sentence_like) <= max(5, 0.02 * n)  # allow tiny residual
    print(f"[V2] dropped blocks ending with sentence punctuation: {len(sentence_like)} "
          f"-> {'PASS' if v2 else 'FAIL'}")

    import unicodedata
    md = Path(md_path).read_text(encoding="utf-8")
    # NFKC normalization handles ligatures (ﬁ→fi, ﬂ→fl, ﬀ→ff) and
    # typographic quotes (’→') so checks are robust.
    md_norm = unicodedata.normalize("NFKC", md)

    # V3 : glossary terms still present (NFKC-normalized, case-insensitive)
    terms = ["Machine learning", "artificial intelligence", "Deep learning",
             "Large language models", "GenAI"]
    md_lower = md_norm.lower()
    missing = [t for t in terms if t.lower() not in md_lower]
    v3 = not missing
    print(f"[V3] glossary terms retained: {len(terms) - len(missing)}/{len(terms)} "
          f"{'-> PASS' if v3 else 'FAIL (' + ','.join(missing) + ')'}")

    # V4 : figure labels with digits/& are unambiguously figure-internal and
    # must be gone. Pure-word labels (e.g. "Foundation model") may linger as
    # harmless redundancy by design (term-glossary guard avoids deleting real
    # glossary prose), so they are NOT failed here.
    labels = ["STAGE 1", "Data preparation & sampling", "STAGE 2 STAGE 3",
              "5) Training loop", "7) Load pretrained"]
    present = [l for l in labels if l in md]
    v4 = not present
    print(f"[V4] figure labels (digit/&) removed: {len(labels) - len(present)}/{len(labels)} "
          f"{'-> PASS' if v4 else 'FAIL (lingering: ' + ','.join(present) + ')'}")

    # ---- P5 cleanup verification (V5-V8) ---- #

    # V5 : P5 cleanup removed standalone figure labels. Some labels also
    # appear inside **Figure X.Y** captions (which are preserved), so we
    # verify that the labels that were ONLY standalone are now gone.
    p5_standalone = [
        "Fine-tunes the pretrained LLM to create a personal assistant or chat model",
        "Embedding model converts raw input into a vector representation",
        "Model input without instructions",
    ]
    p5_lingering = [l for l in p5_standalone if l in md_norm]
    v5 = not p5_lingering
    print(f"[V5] P5 standalone labels removed: {len(p5_standalone) - len(p5_lingering)}/{len(p5_standalone)} "
          f"{'-> PASS' if v5 else 'FAIL (lingering: ' + ','.join(repr(l) for l in p5_lingering) + ')'}")

    # V6 : key body text sentences preserved (not deleted).
    # Use NFKC-normalized text + typographic quote normalization so checks match.
    md_check = md_norm.replace("\u2019", "'").replace("\u2018", "'")
    body_sentences = [
        "This chapter covers",
        "The flowchart in figure 5.11",
        "Now, we create the PyTorch data loaders",
        "In stage 1, we will learn about the fundamental data preprocessing",
        "You'll learn how to prepare input text for training LLMs",
    ]
    body_missing = [s for s in body_sentences if s not in md_check]
    v6 = not body_missing
    print(f"[V6] key body sentences preserved: {len(body_sentences) - len(body_missing)}/{len(body_sentences)} "
          f"{'-> PASS' if v6 else 'FAIL (missing: ' + ','.join(repr(s) for s in body_missing) + ')'}")

    # V7 : no code lines deleted (lines with code keywords still present).
    code_keywords = ["def ", "class ", "import ", "torch.nn", "plt.show", "print("]
    code_missing = [k for k in code_keywords if k not in md]
    v7 = not code_missing
    print(f"[V7] code lines preserved: {len(code_keywords) - len(code_missing)}/{len(code_keywords)} "
          f"{'-> PASS' if v7 else 'FAIL (missing: ' + ','.join(code_missing) + ')'}")

    # V8 : code fences balanced (even number of ``` lines).
    fences = [l for l in md.split("\n") if l.strip().startswith("```")]
    v8 = len(fences) % 2 == 0
    print(f"[V8] code fences balanced: {len(fences)} fences -> {'PASS' if v8 else 'FAIL'}")

    ok = v1 and v2 and v3 and v4 and v5 and v6 and v7 and v8
    print("OVERALL:", "PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
