"""Sphinx tooling binaries."""

load("@rules_java//java:defs.bzl", "java_binary")

java_binary(
    name = "plantuml",
    main_class = "net.sourceforge.plantuml.Run",
    visibility = ["//visibility:public"],
    runtime_deps = [
        "@blueprint_maven_dependencies//:net_sourceforge_plantuml_plantuml",
    ],
)
