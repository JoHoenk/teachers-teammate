"Python linter aspects for ocr"

load("@aspect_rules_lint//lint:lint_test.bzl", "lint_test")
load("@aspect_rules_lint//lint:pylint.bzl", "lint_pylint_aspect")
load("@aspect_rules_lint//lint:ruff.bzl", "lint_ruff_aspect")
load("@aspect_rules_lint//lint:ty.bzl", "lint_ty_aspect")

pylint = lint_pylint_aspect(
    binary = Label("//third_party/lint:pylint"),
    config = Label("//:pyproject.toml"),
)

pylint_test = lint_test(aspect = pylint)

ruff = lint_ruff_aspect(
    # Pinned binary (//third_party/lint:tools.bzl) instead of the built-in
    # @aspect_rules_lint//lint:ruff_bin so the version is explicit/reproducible.
    binary = Label("//third_party/lint:ruff"),
    configs = [Label("//:pyproject.toml")],
)

ruff_test = lint_test(aspect = ruff)

ty = lint_ty_aspect(
    binary = Label("//third_party/lint:ty"),
    config = Label("//:pyproject.toml"),
)

ty_test = lint_test(aspect = ty)
