"""Extract Given/When/Then test specifications from tagged pytest test functions.

Scans test files for functions decorated with @pytest.mark.use_case("UC_NAME"),
extracts their Given/When/Then docstrings, and emits a structured RST page
grouped by use case name.  When a TRLC use-cases file is supplied via
--use-cases, each section also includes the use-case description.

Usage:
    python tools/extract_test_specs.py [test_file ...] --output path/to/test_specs.rst
    python tools/extract_test_specs.py [test_file ...] --output path/to/test_specs.rst \\
        --use-cases docs/requirements/use_cases.trlc
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
import textwrap
from collections import defaultdict
from pathlib import Path


# ---------------------------------------------------------------------------
# TRLC description parser
# ---------------------------------------------------------------------------


def parse_use_case_descriptions(trlc_path: Path) -> dict[str, str]:
    """Return {use_case_name: description_text} parsed from a TRLC file.

    Handles both single-quoted strings and triple-quoted strings.
    """
    descriptions: dict[str, str] = {}
    text = trlc_path.read_text(encoding="utf-8")

    # Match:  UseCase <Name> { ... description = "..." or '''...''' ... }
    block_re = re.compile(
        r"UseCase\s+(\w+)\s*\{([^}]*)\}",
        re.DOTALL,
    )
    triple_re = re.compile(r"description\s*=\s*'''(.*?)'''", re.DOTALL)
    single_re = re.compile(r'description\s*=\s*"(.*?)"', re.DOTALL)

    for block_match in block_re.finditer(text):
        name = block_match.group(1)
        body = block_match.group(2)

        m = triple_re.search(body) or single_re.search(body)
        if m:
            raw = m.group(1)
            # Dedent and collapse internal whitespace to a single paragraph.
            cleaned = textwrap.dedent(raw).strip()
            cleaned = re.sub(r"\s*\n\s*", " ", cleaned)
            descriptions[name] = cleaned

    return descriptions


# ---------------------------------------------------------------------------
# Test-spec collection
# ---------------------------------------------------------------------------


def _use_case_name(decorator: ast.expr) -> str | None:
    """Return the use_case argument string if *decorator* is @pytest.mark.use_case(...)."""
    if not isinstance(decorator, ast.Call):
        return None
    func = decorator.func
    if (
        isinstance(func, ast.Attribute)
        and func.attr == "use_case"
        and isinstance(func.value, ast.Attribute)
        and func.value.attr == "mark"
    ):
        if decorator.args and isinstance(decorator.args[0], ast.Constant):
            return str(decorator.args[0].value)
    return None


def _parse_gwt(docstring: str) -> tuple[str, str, str]:
    """Extract Given/When/Then lines from a docstring."""
    given = when = then = ""
    for line in docstring.splitlines():
        stripped = line.strip()
        if stripped.startswith("Given"):
            given = stripped[len("Given") :].strip()
        elif stripped.startswith("When"):
            when = stripped[len("When") :].strip()
        elif stripped.startswith("Then"):
            then = stripped[len("Then") :].strip()
    return given, when, then


def collect_specs(test_files: list[Path]) -> dict[str, list[tuple[str, str, str, str]]]:
    """Return {use_case_name: [(func_name, given, when, then), ...]} from *test_files*."""
    specs: dict[str, list[tuple[str, str, str, str]]] = defaultdict(list)
    for path in test_files:
        try:
            source = path.read_text(encoding="utf-8")
        except OSError as exc:
            print(f"warning: cannot read {path}: {exc}", file=sys.stderr)
            continue
        try:
            tree = ast.parse(source, filename=str(path))
        except SyntaxError as exc:
            print(f"warning: syntax error in {path}: {exc}", file=sys.stderr)
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            if not node.name.startswith("test_"):
                continue
            for dec in node.decorator_list:
                uc = _use_case_name(dec)
                if uc is None:
                    continue
                raw_doc = ast.get_docstring(node) or ""
                doc = textwrap.dedent(raw_doc).strip()
                given, when, then = _parse_gwt(doc)
                specs[uc].append((node.name, given, when, then))
    return dict(specs)


# ---------------------------------------------------------------------------
# RST renderer
# ---------------------------------------------------------------------------


def _underline(text: str, char: str) -> str:
    return char * len(text)


def _human_name(use_case_id: str) -> str:
    """Convert 'Batch_OCR_Processing' → 'Batch OCR Processing'."""
    return use_case_id.replace("_", " ")


def render_rst(
    specs: dict[str, list[tuple[str, str, str, str]]],
    descriptions: dict[str, str] | None = None,
) -> str:
    title = "Test Specifications"
    lines: list[str] = [
        _underline(title, "="),
        title,
        _underline(title, "="),
        "",
        "This page lists all automated test cases that verify the application's "
        "functional requirements. Tests are grouped by the requirement they cover. "
        "Each entry shows the test scenario in Given / When / Then form.",
        "",
    ]

    for uc_id in sorted(specs):
        human = _human_name(uc_id)
        lines += [human, _underline(human, "-"), ""]

        # Include the requirement description when available.
        if descriptions and uc_id in descriptions:
            desc = descriptions[uc_id]
            lines += [
                f"*{desc}*",
                "",
            ]

        for func_name, given, when, then in specs[uc_id]:
            # Use a definition-list style: term on its own line, body indented.
            lines.append(f"**{func_name}**")
            lines.append("")
            if given:
                lines.append(f"   :Given: {given}")
            if when:
                lines.append(f"   :When: {when}")
            if then:
                lines.append(f"   :Then: {then}")
            lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("files", nargs="*", type=Path, help="Test source files to scan")
    parser.add_argument("--output", type=Path, required=True, help="Output RST file path")
    parser.add_argument(
        "--use-cases",
        type=Path,
        default=None,
        metavar="TRLC_FILE",
        help="TRLC use-cases file; when provided each section includes the description",
    )
    args = parser.parse_args(argv)

    descriptions: dict[str, str] | None = None
    if args.use_cases is not None:
        try:
            descriptions = parse_use_case_descriptions(args.use_cases)
        except OSError as exc:
            print(f"warning: cannot read use-cases file {args.use_cases}: {exc}", file=sys.stderr)

    specs = collect_specs(args.files)
    rst = render_rst(specs, descriptions)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rst, encoding="utf-8")
    print(f"Wrote {len(specs)} use-case section(s) to {args.output}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
