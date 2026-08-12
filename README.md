# ML Interview Notes

Quarto + PyTorch notes for senior MLE / research engineer interview preparation.

## Setup

Install Quarto and uv, then:

```bash
uv sync
uv run quarto preview
```

If `quarto` is installed system-wide rather than inside the Python environment, use:

```bash
uv sync
quarto preview
```

## Suggested workflow

- Use `.qmd` for conceptual notes with math + short executable examples.
- Use `.ipynb` for exploratory experiments.
- Use `.py` for implementations you want to test like real software.
