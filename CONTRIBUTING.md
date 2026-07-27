# Contributing

Thanks for improving the parser. Keep changes local-first and reproducible: match data must come from the demo, not from hidden network enrichment.

## Development setup

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
python -m pip install -e ".[dev]"
```

## Before opening a pull request

```bash
python -m compileall -q config.py main.py launcher.py pipeline.py cs2parser scripts tests
python -m pytest -q
python -m build
python -m twine check dist/*
```

When parser logic changes, include a focused regression test and describe the real-demo validation you performed. Do not commit demos, generated Parquet output, local databases, logs, or absolute paths.

## Commit and pull-request scope

Prefer small, reviewable commits. Explain metric-definition changes precisely, especially for trades, KAST, clutches, economy, event filtering, and rating calibration. UI changes should remain usable when optional remote item icons are unavailable.
