# Ghost Data Still Exists

Deletion-audit predictor for the HackerRank Orchestrate October 2026 challenge. The code reads participant-facing files under `dataset/`, builds timestamped resource observations, applies `deletion_rules.csv`, and writes one decision per request to root `output.csv`.

## Setup

Use Python 3.9 or newer. From the repository root:

```bash
python3 -m pip install -r requirements.txt
```

The local multimodal server is configured in `config.json` for `mlx-community/Qwen3.5-9B-MLX-4bit`. Set `MLX_VLM_API_KEY` in the environment or in an uncommitted root `.env` file:

```text
MLX_VLM_API_KEY=<your local server credential>
```

The key is never written to predictions, reports, or the package. The `.env` file and `.model-cache/` are ignored by Git. Change `model` or `endpoint` in `config.json` if the local service differs. The `evaluation_dir` setting selects one report location; default is root `evaluation/`. Set it to `code/evaluation` if needed.

## Run

```bash
python3 code/evaluation/main.py
```

This generates root `output.csv`, public-sample predictions, `evaluation/evaluation_report.md`, and `evaluation/usage_report.md`. The full run makes local model calls for screenshots and non-ASCII requests, caches structured responses by input and prompt hash, and records actual server token counts. It makes no cloud calls.

For a parser-only baseline:

```bash
python3 code/evaluation/main.py --no-model
```

To choose the report destination for one run:

```bash
python3 code/evaluation/main.py --evaluation-dir code/evaluation
```

To generate only `output.csv`:

```bash
python3 code/main.py
```

To run the focused regression checks:

```bash
python3 -m unittest discover -s tests -v
```

## Decision path

1. Resolve the request email against current identity and audit history, including old emails and email hashes.
2. Read SQL, JSON, logs, text, PDFs, and screenshots into timestamped resource observations. A resource's file capture time and embedded observation time must both precede `audit_at`.
3. Link resources through identifiers, resource IDs, activity, and bounded timing where explicit links are missing. Prefer direct evidence over workflow claims.
4. Apply supplied deletion rules, identify remaining copies and active recreation paths, then order findings, remediation, and verification by the challenge's operation order.

`sample_output.csv` is used only by evaluation code. It is never loaded by `code/main.py` for predictions. The sample is disjoint from the 518 evaluation requests, so its score does not measure hidden-case accuracy.

## Package

After the final run, create `code.zip` from the repository root:

```bash
zip -r code.zip code config.json requirements.txt README.md .gitignore evaluation tests -x '*/__pycache__/*' '*.pyc'
```

Submit `code.zip`, root `output.csv`, and root `log.txt` separately. Do not include `dataset/`, `.env`, `.model-cache/`, or credentials in the archive. The challenge and submission page is https://www.hackerrank.com/contests/hackerrank-orchestrate-october26/challenges/ghost-data-still-exists.
