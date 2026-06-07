"""TRLC requirements targets for documentation use cases."""

load("@trlc//:trlc.bzl", "trlc_requirements", "trlc_requirements_test", "trlc_rst", "trlc_specification")

exports_files([
    "qualification_model.rsl",
    "use_cases.trlc",
])

trlc_specification(
    name = "qualification_spec",
    srcs = ["qualification_model.rsl"],
)

trlc_requirements(
    name = "use_cases",
    srcs = ["use_cases.trlc"],
    spec = [":qualification_spec"],
)

trlc_requirements_test(
    name = "use_cases_validate",
    reqs = [":use_cases"],
)

trlc_rst(
    name = "use_cases_rst",
    reqs = [":use_cases"],
    title = "Functional Requirements",
    visibility = ["//docs:__pkg__"],
)
