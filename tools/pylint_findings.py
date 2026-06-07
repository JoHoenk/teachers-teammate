#!/usr/bin/env python3

import re
import sys
import subprocess
from pathlib import Path
from collections import defaultdict

try:
    from pylint.lint import PyLinter
except ImportError:
    print("Please install pylint:")
    print("pip install pylint")
    sys.exit(1)


EXCLUDED_DIRS = {
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "node_modules",
    "dist",
    "build",
}


def build_pylint_message_maps():
    linter = PyLinter()
    linter.load_default_plugins()

    by_code = {}
    by_symbol = {}

    for msg in linter.msgs_store.messages:
        info = {
            "msgid": msg.msgid,
            "symbol": msg.symbol,
            "description": msg.msg,
        }

        by_code[msg.msgid] = info
        by_symbol[msg.symbol] = info

    return by_code, by_symbol


ruff_cache = {}


def resolve_ruff_rule(rule):
    """
    Resolve Ruff rule description via:

        ruff rule F401
        ruff rule PLC0414
        ruff rule PLR0912
    """

    if rule in ruff_cache:
        return ruff_cache[rule]

    try:
        result = subprocess.run(
            ["ruff", "rule", rule],
            capture_output=True,
            text=True,
            timeout=5,
        )

        if result.returncode != 0:
            ruff_cache[rule] = None
            return None

        output = result.stdout.strip()

        lines = [line.strip() for line in output.splitlines() if line.strip()]

        if not lines:
            ruff_cache[rule] = None
            return None

        description = lines[0]

        info = {
            "code": rule,
            "symbol": rule,
            "description": description,
        }

        ruff_cache[rule] = info
        return info

    except Exception:
        return None


def should_skip(path: Path):
    return any(part in EXCLUDED_DIRS for part in path.parts)


def scan_file(path: Path):
    suppressions = []

    pylint_pattern = re.compile(
        r"#\s*pylint:\s*disable(?:-next)?\s*=\s*([^\n#]+)",
        re.IGNORECASE,
    )

    noqa_pattern = re.compile(
        r"#\s*noqa(?:\s*:\s*([^\n#]+))?",
        re.IGNORECASE,
    )

    with open(path, encoding="utf-8", errors="ignore") as f:
        for lineno, line in enumerate(f, start=1):
            for match in pylint_pattern.finditer(line):
                content = match.group(1).strip()

                for value in content.split(","):
                    value = value.strip()

                    if value:
                        suppressions.append(
                            {
                                "type": "pylint",
                                "value": value,
                                "file": str(path),
                                "line": lineno,
                            }
                        )

            for match in noqa_pattern.finditer(line):
                content = match.group(1)

                if not content:
                    suppressions.append(
                        {
                            "type": "noqa",
                            "value": "NOQA",
                            "file": str(path),
                            "line": lineno,
                        }
                    )
                    continue

                for value in content.split(","):
                    value = value.strip()

                    if value:
                        suppressions.append(
                            {
                                "type": "noqa",
                                "value": value,
                                "file": str(path),
                                "line": lineno,
                            }
                        )

    return suppressions


def resolve_suppression(
    value,
    pylint_by_code,
    pylint_by_symbol,
):
    #
    # Pylint numeric code
    #
    if value in pylint_by_code:
        info = pylint_by_code[value]

        return (
            info["msgid"],
            info["symbol"],
            info["description"],
        )

    #
    # Pylint symbolic name
    #
    if value in pylint_by_symbol:
        info = pylint_by_symbol[value]

        return (
            info["msgid"],
            info["symbol"],
            info["description"],
        )

    #
    # Ruff rule
    #
    ruff_info = resolve_ruff_rule(value)

    if ruff_info:
        return (
            ruff_info["code"],
            ruff_info["symbol"],
            ruff_info["description"],
        )

    #
    # Unknown
    #
    return (
        value,
        value,
        "Unknown suppression",
    )


def main(root_folder):
    root = Path(root_folder)

    if not root.exists():
        print(f"Folder not found: {root}")
        sys.exit(1)

    pylint_by_code, pylint_by_symbol = build_pylint_message_maps()

    grouped = defaultdict(list)

    print("Scanning...\n")

    for py_file in root.rglob("*.py"):
        if should_skip(py_file):
            continue

        try:
            for suppression in scan_file(py_file):
                code, symbol, description = resolve_suppression(
                    suppression["value"],
                    pylint_by_code,
                    pylint_by_symbol,
                )

                key = (
                    suppression["type"],
                    code,
                    symbol,
                    description,
                )

                grouped[key].append(suppression)

        except Exception as ex:
            print(f"Failed to read {py_file}: {ex}")

    total = 0

    print("\n=== SUPPRESSION REPORT ===\n")

    for (
        suppression_type,
        code,
        symbol,
        description,
    ), usages in sorted(
        grouped.items(),
        key=lambda x: len(x[1]),
        reverse=True,
    ):
        total += len(usages)

        print("=" * 100)
        print(f"Type        : {suppression_type}")
        print(f"Code        : {code}")
        print(f"Symbol      : {symbol}")
        print(f"Description : {description}")
        print(f"Count       : {len(usages)}")
        print()

        for usage in usages:
            print(f"  {usage['file']}:{usage['line']}")

        print()

    print("=" * 100)
    print(f"Unique suppression types : {len(grouped)}")
    print(f"Total suppressions       : {total}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(f"Usage: {Path(sys.argv[0]).name} <folder>")
        sys.exit(1)

    main(sys.argv[1])
