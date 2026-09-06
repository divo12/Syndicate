# Syndicate

Project workspace for Syndicate.

## Python foundation

Python 3.13.7 and uv 0.12.5 are the development toolchain. Install the
fully pinned runtime, development and build dependencies, then the local package:

```sh
uv venv
uv pip sync requirements.lock
uv pip install --no-deps --no-build-isolation -e .
.venv/bin/python scripts/quality.py
```

The quality command runs Ruff format/lint checks, strict mypy, pytest and
Radon. It stops on failure and rejects C+ complexity (CC > 10), including
methods and nested functions, in `src`, `scripts` and `tests`. Analysis errors
and missing/empty input paths also fail. Module responsibility remains a review
check; splitting functions to satisfy a number is insufficient.

Run a focused test with `.venv/bin/python -m pytest tests/test_quality.py`.
Inspect a Python path with
`.venv/bin/python scripts/quality.py --complexity-only src`.
Pydantic is available for typed validated boundaries; this foundation adds no
product contracts or model/service calls.

To intentionally refresh transitive pins, regenerate and review the compact lock:

```sh
uv pip compile pyproject.toml --extra dev --group build --universal \
  --no-header --no-annotate -o requirements.lock
```
