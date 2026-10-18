# Contributing to Teacher's Teammate

Thanks for your interest in improving Teacher's Teammate! This page gets you from a fresh
clone to a passing check run. The detailed developer documentation lives in `docs/`:

| Guide | Contents |
|---|---|
| [docs/development.md](docs/development.md) | Architecture, storage/caching, error handling, extension points |
| [docs/testing.md](docs/testing.md) | Test stack, markers, coverage, Given-When-Then convention |
| [docs/deployment.md](docs/deployment.md) | CI pipeline, building wheels/binaries/installers, license report |

## Prerequisites

- Python 3.12
- [Bazel](https://bazel.build/) — build, lint, test, and docs harness
- [uv](https://docs.astral.sh/uv/) (optional — faster venv creation)

## Set up

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e ".[dev,test,llm]"   # or: uv pip install -e ".[dev,test,llm]"
```

The venv is for running the app and your editor; Bazel manages its own toolchain.

## Everyday commands

```bash
# Tests
bazel test //tests:unit_tests --test_output=errors   # fast, no external services
bazel test //tests/... --test_output=errors           # full suite (skips Tesseract/Ollama if absent)

# Lint + type check (ruff, pylint, ty)
bazel test //:lint --test_output=errors

# Format and apply safe lint fixes
bazel run //:format.fix

# Docs
bazel build //docs:html
bazel run //docs:html.serve
```

Run linters **through Bazel only** — ruff and ty versions are pinned there, and a locally
installed copy may report different results.

## Conventions

- **Style:** line length 100, double quotes (configured in `pyproject.toml`).
- **Type checking:** `ty`. Suppress with `# ty: ignore[<rule>]` — mypy-style `# type: ignore`
  is ignored by ty.
- **Lazy imports** (inside functions): add `# noqa: PLC0415`.
- **Tests:** every test needs a `"""Given … / When … / Then …"""` docstring. Build configs with
  `make_config(tmp_path, **overrides)` from `tests/conftest.py`, and patch at the import site.
  GUI code is tested with pytest-qt. See [docs/testing.md](docs/testing.md).
- **Architecture:** the GUI and CLI go through the application layer — don't put discovery or
  availability logic in widgets. See [docs/development.md](docs/development.md).

## Pull requests

1. Branch off `main`.
2. Make sure `bazel test //:lint` and `bazel test //tests/...` pass.
3. Open a PR against `main` — CI runs the same checks.

## License

By contributing, you agree that your contributions are licensed under the
[Apache License 2.0](LICENSE).
