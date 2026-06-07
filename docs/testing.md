# Testing Guide

## Audience and scope

This guide covers how to run the test suite, understand test conventions, and generate coverage reports.
For linting, type checking, and formatting commands, see {doc}`Development <development>`.
End-user runtime usage is documented in {doc}`overview <index>` and {doc}`advanced_user_guide <advanced_user_guide>`.

## Test stack

| Package | Role |
|---|---|
| `pytest` | Test runner |
| `pytest-mock` | `mocker` fixture — convenient wrapper around `unittest.mock` |
| `pytest-cov` | Coverage reporting |
| `Pillow` | Generate synthetic test images in-memory (no numpy required) |

---

## Running the tests

Bazel is the standard test runner. All test commands below assume the virtualenv is active (`source .venv/bin/activate`).

```bash
# Core/unit-oriented suite
bazel test //tests:unit_tests --test_output=errors

# GUI-oriented suite (pytest-qt in headless/offscreen mode under Bazel)
bazel test //tests:gui_tests --test_output=errors

# Combined suite used for broad regression checks
bazel test //tests:coverage_tests --test_output=errors

# Single test file
bazel test //tests:test_config --test_output=errors
```

Tesseract integration tests skip automatically when `tesseract` is not on `PATH` — no manual intervention required.

You can also invoke pytest directly (without Bazel) for quick ad-hoc runs, but Bazel is preferred in CI and for coverage because it applies the correct sandbox and dependency isolation.

### Coverage report

```bash
# Bazel coverage across unit + GUI suites (preferred)
bazel coverage //tests:coverage_tests \
    --instrumentation_filter="//teachers_teammate" \
    --combined_report=lcov

# Optional HTML report (requires lcov/genhtml)
genhtml bazel-out/_coverage/_coverage_report.dat -o coverage_html/
```

---

## Test markers

| Marker | Meaning |
|---|---|
| `needs_tesseract` | Requires the `tesseract` binary on `PATH` |
| `needs_ollama` | Requires a running Ollama instance |

Run only tests that do not require Ollama (direct pytest, ad-hoc use only):

```bash
pytest tests/ -m "not needs_ollama" -v
```

---


## Test case specification — Given-When-Then

Every test function documents its preconditions, action, and expected outcome as a
**Given / When / Then** docstring following the
[Gherkin convention](https://en.wikipedia.org/wiki/Given-When-Then):

```
Given  <the preconditions and starting state>
When   <the action or call under test>
Then   <the expected outcome>
```

This keeps test intent readable as plain prose and makes each test self-describing
without requiring knowledge of the implementation.

Tests that cover a formal use case are tagged with `@pytest.mark.use_case("UC_NAME")`.
A generated catalogue linking each use case to its tagged test scenarios is available in
{doc}`Test Specifications by Use Case <test_specs>`.
