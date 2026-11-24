# Contributing to Teacher's Teammate

Thanks for your interest in improving Teacher's Teammate! This page gets you from a fresh
clone to a passing check run. The detailed developer documentation lives in `docs/`:

| Guide | Contents |
|---|---|
| [docs/development.md](docs/development.md) | Architecture, model catalog and the evaluation tools, storage/caching, GUI structure (Settings, Check tab, Setup Assistant), error handling, downloads |
| [evaluations/README.md](evaluations/README.md) | How the model recommendations are made: the gates, test sets, metrics, comparison |
| [docs/testing.md](docs/testing.md) | Test stack, markers, coverage, Given-When-Then convention |
| [docs/deployment.md](docs/deployment.md) | CI pipeline, building wheels/binaries/installers, license report |

## Prerequisites

- Python 3.14
- [Bazel](https://bazel.build/) — build, lint, test, and docs harness

## Set up

```bash
python tools/build/make_venv.py .venv --for dev
source .venv/bin/activate          # Windows: .venv\Scripts\activate
```

The venv installs against the pinned constraints, so it matches what Bazel tests with. Running the
script again recreates it. It is for running the app and your editor; Bazel manages its own
toolchain.

## Everyday commands

```bash
# Tests
bazel test //tests:unit_tests --test_output=errors   # fast, no external services
bazel test //tests/... --test_output=errors           # full suite (skips Ollama tests if absent)

# After a dependency change: the three locks against pyproject.toml
bazel test //:lock_tests --test_output=errors

# Lint + type check (ruff, ty)
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

Your contributions come in under the license the project goes out under (inbound = outbound):
the [GNU General Public License, version 3 or later](LICENSE).
