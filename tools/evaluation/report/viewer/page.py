"""The report folder: one page, its script and style, and one data file per text.

The folder opens from ``file://`` after unzipping: no server, nothing from the network, no build
step.  A browser blocks ``fetch()`` of a local file, but a ``<script src="data/text-de-01.js">``
works everywhere, so every data file assigns to ``window.REPORT`` instead of being JSON.  The
overview opens at once from ``data/index.js``; a text's edits from all variants and repeats load
when that text is opened.

The command refuses an output inside a git work tree: a report holds corpus text, and the app
repository holds none.  The report itself carries no licence notes and no advice, only the data.
"""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
from pathlib import Path
import re
import shutil
from typing import Any

from .model import ReportModel

INDEX = "index.html"
SINGLE = "report.html"
SCRIPT = "viewer.js"
STYLE = "viewer.css"
DATA = "data"
INDEX_DATA = "index.js"
"""The names of the files a report folder holds."""

_HERE = Path(__file__).parent
"""Where the script and the style sit, next to this module, so a build copies them unchanged."""


def build(
    model: ReportModel,
    out: Path,
    *,
    single_file: bool = False,
    zip_it: bool = False,
) -> Path:
    """Write the report of *model* to the folder *out* and return what was written.

    Args:
        model: The report to write.
        out: The folder to write it to; it is created, and an existing report is overwritten.
        single_file: Write everything into one ``report.html`` instead of a folder of data files.
        zip_it: Also write ``<out>.zip`` next to the folder, for mailing or moving it.

    Raises:
        ValueError: if *out* is inside a git work tree, which holds no corpus text, or is a
            folder with other content than an earlier report.
    """
    _refuse_work_tree(out)
    _refuse_foreign_folder(out)
    out.mkdir(parents=True, exist_ok=True)
    _remove_old_report(out)
    data = model.to_dict()
    if single_file:
        (out / SINGLE).write_text(_single_page(data), encoding="utf-8")
    else:
        _write_folder(data, out)
    if zip_it:
        shutil.make_archive(str(out), "zip", root_dir=out.parent, base_dir=out.name)
    return out


def _remove_old_report(out: Path) -> None:
    """Remove what an earlier build left in *out*, so a rebuild holds only the new report.

    Only the files a build writes are removed: the page, the single file, the script, the style,
    the zip of the folder and the data files.  Anything else in the folder is somebody's.
    """
    for name in (INDEX, SINGLE, SCRIPT, STYLE):
        (out / name).unlink(missing_ok=True)
    data = out / DATA
    if data.is_dir():
        for path in data.glob("*.js"):
            if path.name == INDEX_DATA or path.name.startswith("text-"):
                path.unlink()
        if not any(data.iterdir()):
            data.rmdir()


def _write_folder(data: Mapping[str, Any], out: Path) -> None:
    """Write the page, its script and style, and one data file per text into *out*."""
    (out / DATA).mkdir(exist_ok=True)
    stamp = _build_stamp(data)
    index_data = _assign("index", _index_data(data, stamp))
    (out / INDEX).write_text(_index_page(stamp), encoding="utf-8")
    (out / SCRIPT).write_text(_asset(SCRIPT), encoding="utf-8")
    (out / STYLE).write_text(_asset(STYLE), encoding="utf-8")
    (out / DATA / INDEX_DATA).write_text(index_data, encoding="utf-8")
    for text_id, found in _all_texts(data).items():
        (out / DATA / f"text-{_file_name(text_id)}.js").write_text(
            _assign(f"text[{_json(text_id)}]", found), encoding="utf-8"
        )


def _all_texts(data: Mapping[str, Any]) -> dict[str, Any]:
    """Return every text's file under its id: the text and every version a run answered.

    The versions are grouped in one pass, so the cost grows with the versions, not with texts
    times versions.
    """
    found: dict[str, Any] = {
        text["id"]: {"text": text, "items": {}} for text in data["texts"].values()
    }
    for key, item in data["items"].items():
        found[item["text"]]["items"][key] = item
    return found


def _index_data(data: Mapping[str, Any], stamp: str) -> dict[str, Any]:
    """Return what the overview needs: the inputs, the variants, the numbers and the texts.

    A text is listed by what the overview shows of it, not by its rows: the rows of every version
    are in the text's own file, which is loaded when the text is opened.  The stamp is what the
    script puts after a text file's name, so a rebuilt report is not read from the browser's cache.
    """
    return {
        "stamp": stamp,
        "deciding": data["deciding"],
        "beta": data["beta"],
        "inputs": data["inputs"],
        "variants": data["variants"],
        "numbers": data["numbers"],
        "texts": [
            {
                "id": text["id"],
                "label": text["label"],
                "testset": text["testset"],
                "language": text["language"],
                "band": text["band"],
                "kind": text["kind"],
                "rows": len(text["rows"]),
                "ref_edits": len(text["ref_edits"]),
            }
            for text in data["texts"].values()
        ],
        "cells": _cells(data),
        "errors": data["errors"],
        "eval_versions": data["eval_versions"],
        "eval_notes": data["eval_notes"],
        "prompts": data["prompts"],
    }


def _cells(data: Mapping[str, Any]) -> dict[str, dict[str, dict[str, Any]]]:
    """Return the heat map: per variant and text, the mean of the stored F-scores.

    The mean is taken over the repeats that answered the text, and a text a repeat did not answer
    is left out rather than counted as zero.  Nothing is recomputed here: the F-score of a version
    is the one the row stores.  A text with no reference error has no F-score; its cell says in
    how many of the repeats that answered it the version changed anything (``changed`` of
    ``answered``), because leaving a correct text alone is what it is asked.
    """
    found: dict[str, dict[str, dict[str, Any]]] = {}
    for item in data["items"].values():
        cell = found.setdefault(item["variant"], {}).setdefault(
            item["text"], {"scores": [], "failed": 0, "fallback": 0, "changed": 0, "answered": 0}
        )
        cell["answered"] += 1
        cell["changed"] += 1 if item["edits"] else 0
        if item["f_beta"] is not None:
            cell["scores"].append(item["f_beta"])
        cell["failed"] += 1 if item["failed"] else 0
        cell["fallback"] += 1 if item["fallback"] else 0
    return {
        variant: {
            text: {
                "f_beta": sum(cell["scores"]) / len(cell["scores"]) if cell["scores"] else None,
                "failed": cell["failed"],
                "fallback": cell["fallback"],
                "changed": cell["changed"],
                "answered": cell["answered"],
            }
            for text, cell in texts.items()
        }
        for variant, texts in found.items()
    }


def _index_page(stamp: str) -> str:
    """Return the shell of the report folder: the top bar and the screens are the script's."""
    return (
        "<!DOCTYPE html>\n"
        '<html lang="en">\n'
        "<head>\n"
        '<meta charset="utf-8">\n'
        "<title>Evaluation report</title>\n"
        f'<link rel="stylesheet" href="{_stamp(STYLE, stamp)}">\n'
        "</head>\n"
        "<body>\n"
        '<div id="app"></div>\n'
        f'<script src="{_stamp(f"{DATA}/{INDEX_DATA}", stamp)}"></script>\n'
        f'<script src="{_stamp(SCRIPT, stamp)}"></script>\n'
        "</body>\n"
        "</html>\n"
    )


def _build_stamp(data: Mapping[str, Any]) -> str:
    """Return a short hash of everything the report holds, which every URL of it carries.

    That is the data, the script and the style: a build that changes only the script must not
    keep its old URLs either.

    A browser caches a file by its URL.  Without the stamp a rebuilt report would keep running the
    script, the style and the data of the build before it until the reader forces a reload.
    """
    digest = hashlib.sha256(_json(data).encode("utf-8"))
    for name in (SCRIPT, STYLE):
        digest.update(_asset(name).encode("utf-8"))
    return digest.hexdigest()[:8]


def _stamp(name: str, stamp: str) -> str:
    """Return *name* with the build's stamp after it."""
    return f"{name}?{stamp}"


def _single_page(data: Mapping[str, Any]) -> str:
    """Return the whole report as one HTML file, for mailing a small one."""
    return (
        "<!DOCTYPE html>\n"
        '<html lang="en">\n'
        "<head>\n"
        '<meta charset="utf-8">\n'
        "<title>Evaluation report</title>\n"
        f"<style>\n{_asset(STYLE)}</style>\n"
        "</head>\n"
        "<body>\n"
        '<div id="app"></div>\n'
        f"<script>{_assign('index', _index_data(data, ''))}</script>\n"
        f"<script>{_assign('text', _all_texts(data))}</script>\n"
        f"<script>{_asset(SCRIPT)}</script>\n"
        "</body>\n"
        "</html>\n"
    )


def _assign(key: str, value: Any) -> str:
    """Return a data file that puts *value* under ``window.REPORT.<key>``.

    A key with a bracket (``text["de-01"]``) lives in a table of its own, which the file creates
    when it is the first of its kind: a text file is loaded by itself, long after ``index.js``, and
    ``window.REPORT.text`` does not exist until then.
    """
    table = key.split("[", 1)[0] if "[" in key else ""
    created = f"window.REPORT.{table} = window.REPORT.{table} || {{}};\n" if table else ""
    return (
        f"window.REPORT = window.REPORT || {{}};\n{created}window.REPORT.{key} = {_json(value)};\n"
    )


def _json(value: Any) -> str:
    """Return *value* as JSON that is safe inside a script tag."""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")


def _asset(name: str) -> str:
    """Return the script or the style, as it sits next to this module."""
    return (_HERE / name).read_text(encoding="utf-8")


def _file_name(text_id: str) -> str:
    """Return a text's id as a file name, so a data file can be loaded by name."""
    return re.sub(r"[^A-Za-z0-9._-]", "_", text_id)


def _refuse_foreign_folder(out: Path) -> None:
    """Refuse an existing folder that is not empty and holds no earlier report.

    A rebuild removes the files of the earlier report, so it must be sure it is looking at one.

    Raises:
        ValueError: if *out* holds files but neither ``index.html`` nor ``report.html``.
    """
    if (
        out.is_dir()
        and any(out.iterdir())
        and not (out / INDEX).exists()
        and not (out / SINGLE).exists()
    ):
        raise ValueError(f"{out}: not empty and not an earlier report; name a new folder")


def _refuse_work_tree(out: Path) -> None:
    """Refuse an output inside a git work tree, which holds no corpus text.

    Raises:
        ValueError: if *out* or one of its parents is a work tree.
    """
    for directory in (out, *out.resolve().parents):
        if (directory / ".git").exists():
            raise ValueError(
                f"{out}: inside the git work tree {directory}; a report holds corpus text, "
                "which the repository does not"
            )


__all__ = [
    "DATA",
    "INDEX",
    "INDEX_DATA",
    "SCRIPT",
    "SINGLE",
    "STYLE",
    "build",
]
