# Contributing

## Set up

Needs Python 3.14 and [Bazel](https://bazel.build/).

```bash
python tools/build/make_venv.py .venv --for dev
source .venv/bin/activate          # Windows: .venv\Scripts\activate
```

The venv uses the pinned constraints. It is for running the app and your editor; Bazel brings its
own toolchain. Run the script again to recreate it.

## Commands

```bash
bazel test //tests:unit_tests --test_output=errors   # fast, no external services
bazel test //tests/... --test_output=errors           # full suite
bazel test //:lint --test_output=errors               # ruff + ty
bazel run //:format.fix                               # format + safe lint fixes
bazel test //:lock_tests --test_output=errors         # after a dependency change
bazel build //docs:html                               # docs (serve: bazel run //docs:html.serve)
```

Run ruff and ty through Bazel only; a local copy can be another version.

## Rules

**Architecture**
- Layers, innermost first: `domain` → `texts` / core (`config.py`, `interfaces.py`, …) →
  `infrastructure` → `application` → `gui` / `cli.py`. A layer imports only inner ones;
  `tests/unit/test_layer_imports.py` checks it. Diagrams: [docs/development.md](docs/development.md).
- GUI and CLI go through the application services and never import `infrastructure`; only the
  composition modules wire concrete classes.
- No discovery or availability logic in widgets: it belongs in the service that owns the topic.

**Privacy**
- Text recognition always runs locally. Nothing is downloaded and no network search runs unless
  the user asks.
- Log lines never hold a document's text, a prompt or a key.

**Text and errors**
- Every sentence a teacher reads lives in `teachers_teammate/texts/`, never as a literal at the
  call site. Message boxes and file pickers go through `gui/_dialogs.py`.
- An error raised in `infrastructure` carries a `Message` (a key and its data), never a sentence.

**Code**
- `bazel run //:format.fix` formats; line length 100.
- Type checker `ty`: suppress with `# ty: ignore[<rule>]`; it ignores `# type: ignore`.
- A lazy import inside a function gets `# noqa: PLC0415`.

**Tests**
- Every test has a `"""Given … / When … / Then …"""` docstring.
- Build configs with `make_config(tmp_path, **overrides)` from `tests/conftest.py`; patch at the
  import site. GUI code is tested with pytest-qt. No test touches the network.
- More in [docs/testing.md](docs/testing.md).

**Docs**
- Short and plain; current state only, no history. A number lives in the code, the doc points to
  it. `README.md`, `index.md`, `using_the_app.md` and `advanced_user_guide.md` are for teachers,
  the rest for developers.

How to add a stage, provider, OCR engine, setting and more:
[docs/extension_points.md](docs/extension_points.md).

## Pull requests

1. Branch off `main` (`joho_<topic>`); commit subjects are `[area, area] Sentence`.
2. `bazel test //:lint` and `bazel test //tests/...` pass.
3. Open the PR against `main`; CI runs the same checks.

## Developer documentation

| Guide | Contents |
|---|---|
| [docs/development.md](docs/development.md) | Architecture, model catalog, speed estimates, storage and caching, proofreading exchange, error handling |
| [docs/gui.md](docs/gui.md) | Main window, Check tab, Settings window, Setup Assistant |
| [docs/extension_points.md](docs/extension_points.md) | One section per extension task |
| [docs/testing.md](docs/testing.md) | Test stack, markers, coverage |
| [docs/deployment.md](docs/deployment.md) | CI, wheels, bundles, installers, license report |
| [evaluations/README.md](evaluations/README.md) | How models are recommended: gates, test sets, metrics |
| [evaluations/TOOLS.md](evaluations/TOOLS.md) | The evaluation commands and their code |
| [evaluations/FORMAT.md](evaluations/FORMAT.md) | The evaluation store's layout and rules |

## License

Contributions are licensed under the project's license,
[GPL-3.0-or-later](LICENSE) (inbound = outbound).
