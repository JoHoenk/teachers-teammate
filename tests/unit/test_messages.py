"""The catalogue: every message key has one factory and one renderer, of the same shape.

``messages.py`` says *which* sentence a failure is and carries the data it needs;
``texts/messages.py`` words it.  This test holds the two lists together: one factory per key,
one renderer per key, the same parameter names on both sides, and a value for every key.  A
released key is written to disk with a result, so the golden list below may only grow.
"""

from __future__ import annotations

import ast
from collections.abc import Callable
import inspect
import os
from pathlib import Path
from typing import Any

import pytest

from teachers_teammate import messages
from teachers_teammate.messages import Message, MessageKey
from teachers_teammate.texts import messages as wording

#: The public functions of ``messages.py`` that are not a factory for one key.
_NOT_FACTORIES = frozenset({"message_of", "plain_text", "render_all", "set_renderer"})

#: A sample argument per parameter annotation, to call every factory and renderer with.
_SAMPLES: dict[str, object] = {
    "str": "sample",
    "int": 3,
    "float": 1.5,
    "int | None": 3,
    "float | None": 1.5,
    "Message": None,  # filled below: a message needs a key of its own
    "Message | None": None,
    "Path": Path("/tmp/page.png"),
}


def _root() -> Path:
    """Return the repository root, from the Bazel runfiles or from the source checkout."""
    runfiles = os.environ.get("TEST_SRCDIR", "")
    workspace = os.environ.get("TEST_WORKSPACE", "")
    if runfiles and workspace:
        return Path(runfiles) / workspace
    return Path(__file__).resolve().parents[2]


def _samples(function: Callable[..., object]) -> dict[str, object]:
    """Return a sample value for each parameter of *function*, by its annotation."""
    detail = Message(MessageKey.DETAIL, {"text": "the cause"})
    values = dict(_SAMPLES) | {"Message": detail, "Message | None": detail}
    return {
        name: values[str(parameter.annotation)]
        for name, parameter in inspect.signature(function).parameters.items()
    }


def _factories() -> dict[str, Message]:
    """Return the message each factory of ``messages.py`` builds, by factory name."""
    return {
        name: function(**_samples(function))
        for name, function in inspect.getmembers(messages, inspect.isfunction)
        if function.__module__ == messages.__name__
        and not name.startswith("_")
        and name not in _NOT_FACTORIES
    }


def _factory_of(key: MessageKey) -> str:
    """Return the name of the one factory that builds *key*."""
    names = [name for name, message in _factories().items() if message.key is key]
    assert len(names) == 1, f"{key} has {len(names)} factories: {names}"
    return names[0]


def test_every_key_has_one_factory() -> None:
    """
    Given  the keys of ``messages`` and its factories
    When   each factory is called with sample arguments
    Then   every key is built by exactly one factory, and no factory builds a second key
    """
    covered = {message.key for message in _factories().values()}
    assert covered == set(MessageKey)


def test_every_key_has_one_renderer() -> None:
    """
    Given  the keys of ``messages`` and the registry of ``texts/messages.py``
    When   both are collected
    Then   each key has a renderer there, and the registry holds no other key
    """
    assert set(wording._RENDERERS) == set(MessageKey)


def test_a_factory_and_its_renderer_take_the_same_parameters() -> None:
    """
    Given  a key, its factory and its renderer
    When   both signatures are read
    Then   they name the same parameters in the same order, so a call site only changes its
           import
    """
    for key in MessageKey:
        factory = getattr(messages, _factory_of(key))
        renderer = wording._RENDERERS[key]
        assert list(inspect.signature(factory).parameters) == list(
            inspect.signature(renderer).parameters
        ), key


def test_every_key_renders_from_sample_arguments() -> None:
    """
    Given  a key, its factory and sample arguments for every parameter
    When   the message is shown
    Then   it gives a sentence, not its key
    """
    for key in MessageKey:
        factory = getattr(messages, _factory_of(key))
        rendered = str(factory(**_samples(factory)))
        assert rendered and rendered != str(key), key


def test_the_value_of_a_key_only_grows() -> None:
    """
    Given  the released keys of ``messages``
    When   their values are listed
    Then   each one is unchanged, because a stored result spells its warning with them
    """
    assert {key.name: key.value for key in MessageKey} == {
        "DETAIL": "detail",
        "ADDONS_UPDATE_NEEDS_INSTALL": "addons_update_needs_install",
        "SPACY_REQUIRED": "spacy_required",
        "SPACY_NOT_LOADABLE": "spacy_not_loadable",
        "PROVIDER_NOT_LOADED": "provider_not_loaded",
        "UNKNOWN_PROVIDER_ERROR": "unknown_provider_error",
        "DOWNLOAD_CANCELLED": "download_cancelled",
        "DOWNLOAD_DAMAGED": "download_damaged",
        "MODELS_NOT_DOWNLOADED": "models_not_downloaded",
        "READER_MODEL_MISMATCH": "reader_model_mismatch",
        "PACKAGE_NOT_INSTALLED": "package_not_installed",
        "MODEL_PULL_FAILED": "model_pull_failed",
        "LEFT_OUT_WARNING": "left_out_warning",
        "LEFT_OUT_WARNING_ONE": "left_out_warning_one",
        "WORK_SAVED_BY_NEWER_VERSION": "work_saved_by_newer_version",
        "IMAGE_NOT_ENCODED": "image_not_encoded",
        "IMAGE_NOT_WRITTEN": "image_not_written",
        "UNSUPPORTED_INPUT_SUFFIX": "unsupported_input_suffix",
        "PREVIEW_NEEDS_IMAGES": "preview_needs_images",
        "IMAGE_UNREADABLE": "image_unreadable",
        "EMPTY_OCR_ANSWER": "empty_ocr_answer",
        "CACHE_SAVE_FAILED": "cache_save_failed",
        "INPUT_READ_FAILED": "input_read_failed",
        "OUTPUT_OVERWRITES_INPUT": "output_overwrites_input",
        "OUTPUT_READ_BACK_AS_INPUT": "output_read_back_as_input",
        "OUTPUT_REPLACES_ANOTHER": "output_replaces_another",
        "PERSONAL_INFO_REMOVAL_FAILED": "personal_info_removal_failed",
        "PERSONAL_INFO_RESTORE_FAILED": "personal_info_restore_failed",
        "PREPROCESSING_FAILED": "preprocessing_failed",
        "PREVIEW_SAVE_FAILED": "preview_save_failed",
        "PERSONAL_INFO_SETUP_FAILED": "personal_info_setup_failed",
        "STORAGE_ROOT_UNRESOLVED": "storage_root_unresolved",
        "OCR_FAILED": "ocr_failed",
        "INPUT_WITHOUT_CONTENT": "input_without_content",
        "INPUT_WITHOUT_UNITS": "input_without_units",
        "EMPTY_ANSWER": "empty_answer",
        "EMPTY_ANSWER_CUT_OFF": "empty_answer_cut_off",
        "ANSWER_ABANDONED": "answer_abandoned",
        "CANNOT_CONNECT": "cannot_connect",
        "CANNOT_REACH": "cannot_reach",
        "CONNECTION_TIMED_OUT": "connection_timed_out",
        "CONTACT_FAILED": "contact_failed",
        "HTTP_ERROR": "http_error",
        "MODEL_NOT_FOUND": "model_not_found",
        "NO_FIRST_TOKEN": "no_first_token",
        "NO_MODELS_INSTALLED": "no_models_installed",
        "UNREACHABLE": "unreachable",
        "CONFIG_FILE_MISSING": "config_file_missing",
        "CLI_ERROR": "cli_error",
    }


def test_a_message_without_a_language_prints_its_key_and_data() -> None:
    """
    Given  no language installed, as in a test of ``domain`` or ``infrastructure`` alone
    When   a message is shown
    Then   it gives its key and its data, so a traceback or a log line stays readable
    """
    messages.set_renderer(None)
    try:
        shown = str(Message(MessageKey.DOWNLOAD_DAMAGED, {"name": "reader.onnx"}))
    finally:
        wording.install()
    assert shown == "download_damaged: name=reader.onnx"


def test_a_message_refuses_a_value_it_could_not_store() -> None:
    """
    Given  a message and a value that is neither text, a number, nothing, nor a message
    When   the message is built
    Then   it is refused, so a stored result can always be written as JSON
    """
    bad: Any = object()
    with pytest.raises(TypeError):
        Message(MessageKey.DETAIL, {"text": bad})


def test_the_message_an_error_carries_comes_back() -> None:
    """
    Given  an error raised with a message and a foreign error that has only its own words
    When   each is asked for its message
    Then   the first gives the message it was raised with, the second its text as detail
    """
    own = Message(MessageKey.EMPTY_ANSWER)
    assert messages.message_of(ValueError(own)) is own
    assert messages.message_of(ValueError("plain words")) == Message(
        MessageKey.DETAIL, {"text": "plain words"}
    )


def _used_names() -> set[str]:
    """Return every name and attribute the sources outside ``domain`` and ``texts`` use."""
    used: set[str] = set()
    package = _root() / "teachers_teammate"
    for path in package.rglob("*.py"):
        relative = path.relative_to(package).as_posix()
        if relative.startswith(("domain/", "texts/")):
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Name):
                used.add(node.id)
            elif isinstance(node, ast.Attribute):
                used.add(node.attr)
    return used


def _called_name(node: ast.AST) -> str:
    """Return the name a call goes by, without its module."""
    if isinstance(node, ast.Name):
        return node.id
    return getattr(node, "attr", "")


def _uses_the_caught_error(node: ast.AST, name: str) -> bool:
    """Say whether *node* turns the error bound to *name* into text or passes it on."""
    if isinstance(node, ast.Call) and _called_name(node.func) == "message_of":
        return False
    if isinstance(node, ast.Name):
        return node.id == name
    return any(_uses_the_caught_error(child, name) for child in ast.iter_child_nodes(node))


def _factory_calls_with_the_caught_error(path: Path) -> list[str]:
    """Return one line per message factory in *path* that is given the caught error itself."""
    factories = set(_factories())
    found: list[str] = []
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for handler in ast.walk(tree):
        if not isinstance(handler, ast.ExceptHandler) or handler.name is None:
            continue
        for node in ast.walk(handler):
            if not isinstance(node, ast.Call) or _called_name(node.func) not in factories:
                continue
            given = [*node.args, *(keyword.value for keyword in node.keywords)]
            if any(_uses_the_caught_error(argument, handler.name) for argument in given):
                found.append(
                    f"{path}:{node.lineno}: {_called_name(node.func)}(…) is given {handler.name}"
                )
    return found


def test_no_factory_is_given_a_caught_error() -> None:
    """
    Given  every module of ``infrastructure`` and ``application``
    When   the message-factory calls inside an ``except … as name`` are read
    Then   none is given ``str(name)``, an f-string holding it or the error itself: a caught
           error arrives as ``message_of(name)``, so its words are not glued into a sentence
    """
    package = _root() / "teachers_teammate"
    offenders = sorted(
        line
        for folder in ("infrastructure", "application")
        for path in (package / folder).rglob("*.py")
        for line in _factory_calls_with_the_caught_error(path)
    )
    assert not offenders, "\n".join(offenders)


def test_every_factory_is_used() -> None:
    """
    Given  the factories of ``messages.py``
    When   the names the rest of the package calls are collected
    Then   no factory is left over, so a key nothing reports does not linger
    """
    unused = sorted(set(_factories()) - _used_names() - _NOT_FACTORIES)
    assert not unused, f"no call site reports these messages: {unused}"


def test_a_message_carried_by_another_is_worded_first() -> None:
    """
    Given  a message whose reason is another message
    When   it is shown
    Then   the sentence carries the words of the inner one, not its key
    """
    shown = messages.preprocessing_failed(reason=Message(MessageKey.DETAIL, {"text": "no space"}))
    assert str(shown) == "Preprocessing failed: no space"
