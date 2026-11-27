"""The catalogue: every message key has one factory and one renderer, of the same shape.

``messages.py`` says *which* sentence a failure is and carries the data it needs;
``texts/messages.py`` words it.  This test holds the two lists together: one factory per key,
one renderer per key, the same parameter names on both sides, and a value for every key.  A
key's value names its sentence for good, so the golden list below may only grow.
"""

from __future__ import annotations

import ast
from collections.abc import Callable
import inspect
import logging
import os
from pathlib import Path
from typing import Any

import pytest

from teachers_teammate import messages
from teachers_teammate.messages import Message, MessageKey
from teachers_teammate.texts import messages as wording

#: The public functions of ``messages.py`` that are not a factory for one key.
_NOT_FACTORIES = frozenset(
    {"log_failure", "log_once", "message_of", "plain_text", "render_all", "set_renderer"}
)

#: Factories ``messages.py`` itself calls, so no other module needs to.
_BASE_CASES = frozenset({"detail"})

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
    "bool": True,
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
    Then   each one is unchanged, because a message that is kept names its sentence by it
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


def test_a_message_refuses_a_tuple_of_anything_but_messages() -> None:
    """
    Given  a tuple of plain text, which the wording could not render
    When   a message is built with it
    Then   it is refused, so a value is never dropped silently when the sentence is worded
    """
    bad: Any = ("a", "b")
    with pytest.raises(TypeError):
        Message(MessageKey.DETAIL, {"text": bad})


def test_a_message_keeps_its_own_copy_of_its_data() -> None:
    """
    Given  a message built from a dict that the caller changes afterwards
    When   the message is read and compared
    Then   it still holds the values it was built with, equals a twin and hashes like it
    """
    data: dict[str, Any] = {"url": "http://host"}
    message = messages.cannot_connect("http://host")
    built = Message(MessageKey.CANNOT_CONNECT, data)
    data["url"] = object()

    assert built == message
    assert hash(built) == hash(message)
    assert built.args["url"] == "http://host"
    assert len({built, message}) == 1


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


def test_an_expected_error_is_a_warning_with_its_message(caplog) -> None:
    """
    Given  an error that carries a message, as infrastructure raises them
    When   log_failure writes it
    Then   the line is a WARNING holding that message and no traceback
    """
    logger = logging.getLogger("tests.messages")
    with caplog.at_level(logging.WARNING, logger="tests.messages"):
        messages.log_failure(logger, "Reading page 3", ValueError(messages.no_first_token(2.5)))

    written = caplog.records[-1]
    assert written.levelno == logging.WARNING
    assert written.getMessage() == "Reading page 3: no_first_token: seconds=2.5"


def test_a_bug_is_an_error_with_its_type_and_frames_but_not_its_message(caplog) -> None:
    """
    Given  an error that carries no message and quotes the text it choked on
    When   log_failure writes it
    Then   the line is an ERROR naming the type and the frames, and not the message
    """

    quoted = "the student wrote forty-two apples"

    def read() -> None:
        raise ValueError(quoted)

    logger = logging.getLogger("tests.messages")
    try:
        read()
    except ValueError as error:
        with caplog.at_level(logging.ERROR, logger="tests.messages"):
            messages.log_failure(logger, "Reading page 1", error)

    written = caplog.records[-1].getMessage()
    assert written.startswith("Reading page 1: ValueError")
    assert "in read" in written
    assert "forty-two apples" not in written


def test_a_failed_operating_system_call_adds_its_code_and_reason(caplog) -> None:
    """
    Given  the error of a file that is not there
    When   log_failure writes it
    Then   the line holds the type, the errno and the system's reason
    """
    logger = logging.getLogger("tests.messages")
    try:
        Path("/nowhere/page.png").read_bytes()
    except OSError as error:
        reason = error.strerror or ""
        with caplog.at_level(logging.ERROR, logger="tests.messages"):
            messages.log_failure(logger, "Reading page 1", error)

    written = caplog.records[-1].getMessage()
    assert written.startswith("Reading page 1: FileNotFoundError (errno 2: ")
    assert reason in written


def test_an_expected_error_wrapping_a_foreign_one_logs_the_bare_key(caplog) -> None:
    """
    Given  an expected error whose message wraps a foreign error's own words, which quote a text
    When   log_failure writes it
    Then   the line holds the key of those words, not the words, and names the foreign kind
    """
    quoted = "Anna Mueller wrote this"
    try:
        try:
            raise KeyError(quoted)
        except KeyError as foreign:
            raise ValueError(
                messages.personal_info_restore_failed(messages.message_of(foreign))
            ) from foreign
    except ValueError as error:
        logger = logging.getLogger("tests.messages")
        with caplog.at_level(logging.WARNING, logger="tests.messages"):
            messages.log_failure(logger, "Restoring", error)

    written = caplog.records[-1]
    assert written.levelno == logging.WARNING
    assert written.getMessage() == (
        "Restoring: personal_info_restore_failed: reason=detail (caused by KeyError)"
    )
    assert "Anna" not in written.getMessage()


def test_the_plain_form_keeps_the_own_words_of_a_detail_unless_asked_not_to() -> None:
    """
    Given  a message wrapping a foreign error's own words, nested in another message
    When   it is written plain, once as it is and once for the log
    Then   the first holds the words, the second only their key
    """
    nested = messages.model_pull_failed(messages.detail("disk full"))

    assert messages.plain_text(nested) == "model_pull_failed: reason=detail: text=disk full"
    assert messages.plain_text(nested, own_words=False) == "model_pull_failed: reason=detail"


def test_a_bug_names_the_errors_it_was_raised_from(caplog) -> None:
    """
    Given  a bug raised from another error, which was raised while handling a third
    When   log_failure writes it
    Then   the line names the kinds and the frames of each, and none of their words
    """

    quoted = "the student wrote forty-two apples"

    def parse() -> None:
        raise ValueError(quoted)

    def read() -> None:
        try:
            parse()
        except ValueError as cause:
            raise RuntimeError(quoted) from cause

    logger = logging.getLogger("tests.messages")
    try:
        read()
    except RuntimeError as error:
        with caplog.at_level(logging.ERROR, logger="tests.messages"):
            messages.log_failure(logger, "Reading page 1", error)

    written = caplog.records[-1].getMessage()
    assert written.startswith("Reading page 1: RuntimeError")
    assert "caused by ValueError" in written
    assert "in parse" in written
    assert "forty-two apples" not in written


def test_a_chain_that_loops_back_ends_at_its_start(caplog) -> None:
    """
    Given  two errors that are each other's cause
    When   log_failure writes one of them
    Then   it returns, naming the other once
    """
    first, second = ValueError("a"), KeyError("b")
    first.__cause__ = second
    second.__cause__ = first

    logger = logging.getLogger("tests.messages")
    with caplog.at_level(logging.ERROR, logger="tests.messages"):
        messages.log_failure(logger, "Looping", first)

    assert caplog.records[-1].getMessage().count("caused by KeyError") == 1


def test_a_failure_the_catch_point_wrote_is_not_written_a_second_time(caplog) -> None:
    """
    Given  a failure that the task which raised it wrote, and the one that shows it to the teacher
    When   log_once is asked for a line about it
    Then   nothing more is written: the file keeps one line per failure
    """
    error = ValueError(messages.no_first_token(2.5))
    logger = logging.getLogger("tests.messages")
    with caplog.at_level(logging.WARNING, logger="tests.messages"):
        messages.log_failure(logger, "Reading page 3", error)
        messages.log_once(logger, "Reading page 3", error)

    assert len(caplog.records) == 1


def test_a_failure_nobody_wrote_is_written_by_log_once_once(caplog) -> None:
    """
    Given  a failure that no catch point wrote
    When   log_once is asked for a line about it twice
    Then   the first gets its line and the second none, so showing it writes it once
    """
    error = ValueError(messages.no_first_token(2.5))
    logger = logging.getLogger("tests.messages")
    with caplog.at_level(logging.WARNING, logger="tests.messages"):
        messages.log_once(logger, "Reading page 3", error)
        messages.log_once(logger, "Reading page 3", error)

    assert [r.getMessage() for r in caplog.records] == [
        "Reading page 3: no_first_token: seconds=2.5"
    ]


def _imported_names() -> set[str]:
    """Return every name the sources outside ``domain`` and ``texts`` import from ``messages``.

    A name that only looks the same (a local variable, a function of ``texts``) is not a use.
    """
    imported: set[str] = set()
    package = _root() / "teachers_teammate"
    for path in package.rglob("*.py"):
        relative = path.relative_to(package).as_posix()
        if relative.startswith(("domain/", "texts/")):
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (
                isinstance(node, ast.ImportFrom)
                and (node.module or "").split(".")[-1] == "messages"
            ):
                imported.update(alias.name for alias in node.names)
    return imported


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
    When   the names the rest of the package imports from ``messages`` are collected
    Then   no factory is left over, so a key nothing reports does not linger
    """
    unused = sorted(set(_factories()) - _imported_names() - _NOT_FACTORIES - _BASE_CASES)
    assert not unused, f"no call site reports these messages: {unused}"


def test_a_message_carried_by_another_is_worded_first() -> None:
    """
    Given  a message whose reason is another message
    When   it is shown
    Then   the sentence carries the words of the inner one, not its key
    """
    shown = messages.preprocessing_failed(reason=Message(MessageKey.DETAIL, {"text": "no space"}))
    assert str(shown) == "Preprocessing failed: no space"
