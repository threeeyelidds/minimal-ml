# Minimal ML

Concise, executable notes for understanding machine learning from first principles.

- Website: <https://threeeyelidds.github.io/minimal-ml/>
- Repository: <https://github.com/threeeyelidds/minimal-ml>

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
