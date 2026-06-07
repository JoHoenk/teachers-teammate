py_library(
    name = "release_tools_lib",
    srcs = [
        "__init__.py",
        "stamp_versions.py",
    ],
    visibility = ["//visibility:public"],
)

# Extracts [project.dependencies] from pyproject.toml so the wheel's
# Requires-Dist is single-sourced from pyproject (see //teachers_teammate:BUILD).
py_binary(
    name = "extract_wheel_requires",
    srcs = ["extract_wheel_requires.py"],
    visibility = ["//visibility:public"],
)
