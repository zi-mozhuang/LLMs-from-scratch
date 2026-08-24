# AGENTS.md

Companion repository for the book *Build a Large Language Model (From Scratch)* by Sebastian Raschka. Educational codebase implementing GPT-like LLMs in PyTorch from scratch.

## Repository Structure

- **Chapters**: `ch02/` through `ch07/` + `appendix-A/` through `appendix-E/`
- **Main code**: Each chapter has `01_main-chapter-code/` with `.ipynb` notebooks and `.py` scripts
- **Bonus material**: Additional subfolders (e.g., `02_bonus_*`, `03_*`) for extended content
- **PyPI package**: `pkg/llms_from_scratch/` - installable package exporting chapter functions
- **Submodule**: `reasoning-from-scratch/` is a separate repo (`rasbt/reasoning-from-scratch`). Don't edit it as part of this repo; it has its own pyproject/tests.

## Development Setup

**Primary tool**: `uv` (preferred)
```bash
# Install uv first: curl -LsSf https://astral.sh/uv/install.sh | sh
uv sync --dev                    # Install dev dependencies
uv pip install -r requirements.txt
```

**Alternative tools**:
- `pip`: `pip install -r requirements.txt`
- `pixi`: `pixi install` (conda-forge based)

**Editable package install**:
```bash
uv add --editable . --dev
# or: pip install -e .
```

## Running Code

Notebooks are the primary format. Each main chapter code folder contains:
- `chXX.ipynb` - Main chapter notebook
- `exercise-solutions.ipynb` - Exercise solutions
- Supporting `.py` scripts (e.g., `gpt_train.py`, `gpt_generate.py`)

## Testing

Tests are distributed across chapters, not centralized.

```bash
# Core tests (run with uv/pip environment active)
pytest setup/02_installing-python-libraries/tests.py
pytest ch03/02_bonus_efficient-multihead-attention/tests/test_mha_implementations.py
pytest ch04/01_main-chapter-code/tests.py
pytest ch04/03_kv-cache/tests.py
pytest ch05/01_main-chapter-code/tests.py
pytest ch06/01_main-chapter-code/tests.py
pytest pkg/llms_from_scratch/tests/

# Notebook validation
pytest --nbval ch02/01_main-chapter-code/dataloader.ipynb
pytest --nbval ch03/01_main-chapter-code/multihead-attention.ipynb
```

CI (`.github/workflows/basic-tests-linux-uv.yml`) is the authoritative test list; it also runs ch05 bonus tests (llama32, qwen3, gemma3, olmo3) and needs `uv pip install -r ch05/07_gpt_to_llama/tests/test-requirements-extra.txt` plus `pytest-ruff nbval`.

## Linting

```bash
ruff check .        # Configured in pyproject.toml (line-length: 140)
```

## Package Import Reference

```python
from llms_from_scratch.ch02 import GPTDatasetV1, create_dataloader_v1
from llms_from_scratch.ch03 import MultiHeadAttention, SelfAttention_v1
from llms_from_scratch.ch04 import GPTModel, TransformerBlock
from llms_from_scratch.ch05 import train_model_simple, generate
from llms_from_scratch.ch06 import SpamDataset, train_classifier_simple
from llms_from_scratch.ch07 import InstructionDataset, format_input
```

## pdf_to_md/ Pipeline

Local PDF→Markdown conversion of the book (tracked in git). Entry point: `bash pdf_to_md/run_pipeline.sh` (P0 images → extract → classify → merge → render → verify). `pdf_to_md/pdf-to-md-plan.md` is the index + invariants doc; read it before touching the pipeline.

Hard rules from the plan:
- Deterministic output; validate with `pipeline/verify.py` and compare against `golden/` baselines.
- No hardcoded strings/page numbers/coordinates - decisions come from structural signals (font/color/position).
- Don't delete "suspected dead code" without an A/B test: monkeypatch to identity, full re-render, MD hash must be unchanged.
- Legacy patch scripts are archived read-only in `.backup/legacy_scripts/` - don't revive them.

## Key Constraints

- **Python**: 3.10+ required
- **PyTorch**: Core dependency (>=2.2.2), auto-uses CUDA if available
- **No Apple MPS**: Intentionally not fully supported (unstable training results). Use CPU for book-matching behavior or CUDA for speed.
- **No external LLM libraries**: Pure PyTorch implementation
- **Contributions**: Main chapter code is frozen (book consistency). Bonus material accepts contributions.

## Important Notes

- Notebooks load images from external host (`sebastianraschka.com`) - requires internet for full rendering
- Main chapter notebooks are JSON files; Git diffs can be messy. Copy before editing.
- Bonus materials (ch05/07_gpt_to_llama/, ch05/11_qwen3/, etc.) include Llama3, Qwen3, Gemma3 implementations
