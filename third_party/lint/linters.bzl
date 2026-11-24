"Linter aspects for ocr: Python (ruff, ty), shell (shellcheck) and PowerShell (PSScriptAnalyzer)"

load("@aspect_rules_lint//lint:lint_test.bzl", "lint_test")
load("@aspect_rules_lint//lint:ps_script_analyzer.bzl", "lint_ps_script_analyzer_aspect")
load("@aspect_rules_lint//lint:ruff.bzl", "lint_ruff_aspect")
load("@aspect_rules_lint//lint:shellcheck.bzl", "lint_shellcheck_aspect")
load("@aspect_rules_lint//lint:ty.bzl", "lint_ty_aspect")

ruff = lint_ruff_aspect(
    # rules_lint's built-in ruff, the same binary the formatter uses
    # (@aspect_rules_lint//format:ruff), so lint and format cannot drift apart.
    binary = Label("@aspect_rules_lint//lint:ruff_bin"),
    configs = [Label("//:pyproject.toml")],
)

ruff_test = lint_test(aspect = ruff)

ty = lint_ty_aspect(
    binary = Label("@aspect_rules_lint//lint:ty_bin"),
    config = Label("//:pyproject.toml"),
)

ty_test = lint_test(aspect = ty)

shellcheck = lint_shellcheck_aspect(
    # rules_lint's built-in shellcheck; the runner image's copy is never used.
    binary = Label("@aspect_rules_lint//lint:shellcheck_bin"),
    config = Label("//third_party/lint:shellcheckrc"),
)

shellcheck_test = lint_test(aspect = shellcheck)

# PowerShell: the installer scripts. The binary and the two modules are the archives in
# MODULE.bazel (rules_lint ships none).
ps_script_analyzer = lint_ps_script_analyzer_aspect(
    binary = Label("@pwsh//:pwsh"),
    config = Label("//third_party/lint:PSScriptAnalyzerSettings.psd1"),
    converttosarif = Label("@converttosarif//:files"),
    psscriptanalyzer = Label("@psscriptanalyzer//:files"),
)

ps_script_analyzer_test = lint_test(aspect = ps_script_analyzer)
