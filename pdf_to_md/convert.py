"""Convert the bundled book PDF to cleaned Markdown.

This is the entry-point wrapper around `fix_code_blocks.process_pdf_to_markdown`,
which extracts code blocks from PDF fonts, restores fenced code, and applies the
full cleanup pipeline (math, annotations, prose, image captions, fences).

Usage:
    python convert.py
"""
import pathlib
import sys

import fix_code_blocks

BASE = pathlib.Path(__file__).parent
PDF_PATH = BASE / "Build a Large Language Model (From Scratch) (Sebastian Raschka) (z-library.sk, 1lib.sk, z-lib.sk).pdf"
MD_PATH = BASE / "output" / "Build-a-LLM-from-scratch.md"
IMG_DIR = BASE / "output" / "images"


def main() -> int:
    if not PDF_PATH.exists():
        print(f"PDF not found: {PDF_PATH}", file=sys.stderr)
        return 1
    out = fix_code_blocks.process_pdf_to_markdown(PDF_PATH, MD_PATH, IMG_DIR)
    print(f"Markdown written to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())