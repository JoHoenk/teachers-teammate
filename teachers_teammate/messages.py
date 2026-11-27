"""What a failure says below the adapters: the key of a sentence and the data it needs.

A message is not a sentence: the :class:`Message` says *which* sentence and carries the values
it needs, and ``texts`` words it (``texts/messages.py``, one renderer per key).  A failure that
``infrastructure`` raises carries one, so its wording cannot drift from the call site and
nothing compares two failures as text.  A message holds only text, numbers, ``None`` and other
messages, so it can be written as JSON.

``str(message)`` gives the sentence once ``teachers_teammate.texts`` has installed a language.
Until then it gives ``"key: args"``, which keeps a traceback, a log line and a test readable
without the wording.

A failure is logged by :func:`log_failure`, the one place that tells an expected error — one
carrying a message — from a bug.  :func:`log_once` is the same call for an error another catch
point may have written already.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
import logging
from pathlib import Path
import traceback
from types import MappingProxyType


class MessageKey(StrEnum):
    """The sentence a message stands for.  A value names that sentence, so it never changes."""

    #: A foreign error's own words, unchanged: there is nothing else to say.
    DETAIL = "detail"

    # ── The reading engines, their downloads and the Add-ons page ─────────────────
    ADDONS_UPDATE_NEEDS_INSTALL = "addons_update_needs_install"
    SPACY_REQUIRED = "spacy_required"
    SPACY_NOT_LOADABLE = "spacy_not_loadable"
    PROVIDER_NOT_LOADED = "provider_not_loaded"
    UNKNOWN_PROVIDER_ERROR = "unknown_provider_error"
    DOWNLOAD_CANCELLED = "download_cancelled"
    DOWNLOAD_DAMAGED = "download_damaged"
    MODELS_NOT_DOWNLOADED = "models_not_downloaded"
    READER_MODEL_MISMATCH = "reader_model_mismatch"
    PACKAGE_NOT_INSTALLED = "package_not_installed"
    MODEL_PULL_FAILED = "model_pull_failed"

    # ── The saved work ────────────────────────────────────────────────────────────
    WORK_SAVED_BY_NEWER_VERSION = "work_saved_by_newer_version"

    # ── What a run says about a file and what it writes ───────────────────────────
    IMAGE_NOT_ENCODED = "image_not_encoded"
    IMAGE_NOT_WRITTEN = "image_not_written"
    UNSUPPORTED_INPUT_SUFFIX = "unsupported_input_suffix"
    PREVIEW_NEEDS_IMAGES = "preview_needs_images"
    IMAGE_UNREADABLE = "image_unreadable"
    EMPTY_OCR_ANSWER = "empty_ocr_answer"
    CACHE_SAVE_FAILED = "cache_save_failed"
    INPUT_READ_FAILED = "input_read_failed"
    OUTPUT_OVERWRITES_INPUT = "output_overwrites_input"
    OUTPUT_READ_BACK_AS_INPUT = "output_read_back_as_input"
    OUTPUT_REPLACES_ANOTHER = "output_replaces_another"
    PERSONAL_INFO_REMOVAL_FAILED = "personal_info_removal_failed"
    PERSONAL_INFO_RESTORE_FAILED = "personal_info_restore_failed"
    PREPROCESSING_FAILED = "preprocessing_failed"
    PREVIEW_SAVE_FAILED = "preview_save_failed"
    PERSONAL_INFO_SETUP_FAILED = "personal_info_setup_failed"
    STORAGE_ROOT_UNRESOLVED = "storage_root_unresolved"
    OCR_FAILED = "ocr_failed"
    INPUT_WITHOUT_CONTENT = "input_without_content"
    INPUT_WITHOUT_UNITS = "input_without_units"

    # ── A server and the answers it gives ─────────────────────────────────────────
    EMPTY_ANSWER = "empty_answer"
    EMPTY_ANSWER_CUT_OFF = "empty_answer_cut_off"
    ANSWER_ABANDONED = "answer_abandoned"
    CANNOT_CONNECT = "cannot_connect"
    CANNOT_REACH = "cannot_reach"
    CONNECTION_TIMED_OUT = "connection_timed_out"
    CONTACT_FAILED = "contact_failed"
    HTTP_ERROR = "http_error"
    MODEL_NOT_FOUND = "model_not_found"
    NO_FIRST_TOKEN = "no_first_token"
    NO_MODELS_INSTALLED = "no_models_installed"
    UNREACHABLE = "unreachable"

    # ── Comparing writing models on one text ─────────────────────────────────────
    MODEL_NOT_COMPARED = "model_not_compared"
    PROOFREADING_NOT_COMPARED = "proofreading_not_compared"
    MARKS_NOT_REPLACED = "marks_not_replaced"

    # ── The command line ──────────────────────────────────────────────────────────
    CONFIG_FILE_MISSING = "config_file_missing"


@dataclass(frozen=True)
class Message:
    """One sentence a teacher can read, as its key and the values it needs."""

    key: MessageKey
    args: Mapping[str, Arg] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Refuse a value that could neither be stored nor rendered, and freeze a copy."""
        for name, value in self.args.items():
            if not _storable(value):
                raise TypeError(f"{self.key}: {name} holds a {type(value).__name__}")
        object.__setattr__(self, "args", MappingProxyType(dict(self.args)))

    def __hash__(self) -> int:
        """Return a hash of the key and the data, so a message can sit in a set."""
        return hash((self.key, tuple(self.args.items())))

    def __str__(self) -> str:
        """Return the sentence, or its plain form while no language is installed."""
        render = _Words.render
        return plain_text(self) if render is None else render(self)


#: What one message carries: a value JSON holds, a nested message, or a tuple of messages.
Arg = str | int | float | None | Message | tuple[Message, ...]

#: What separates the messages of one line, in the order they were produced.
SEPARATOR = " "


class _Words:
    """The one that words a message; empty until ``texts`` installs a language."""

    render: Callable[[Message], str] | None = None


def set_renderer(render: Callable[[Message], str] | None) -> None:
    """Install *render* as the worder of every message, or ``None`` to drop it."""
    _Words.render = render


def plain_text(message: Message, *, own_words: bool = True) -> str:
    """Return *message* as ``"key: args"``: for the log, and for a message with no wording.

    A ``detail`` holds a foreign error's own words, which can quote a document.  The log passes
    ``own_words=False`` and gets the bare key for it.
    """
    if message.key is MessageKey.DETAIL and not own_words:
        return str(message.key)
    if not message.args:
        return str(message.key)
    values = ", ".join(
        f"{name}={_plain_value(value, own_words)}" for name, value in message.args.items()
    )
    return f"{message.key}: {values}"


def message_of(error: BaseException) -> Message:
    """Return the message *error* was raised with, or the own words of a foreign error."""
    if error.args and isinstance(error.args[0], Message):
        return error.args[0]
    return detail(str(error))


def log_failure(logger: logging.Logger, what: str, error: BaseException) -> None:
    """Write *error*, named by *what*, to *logger*: an expected error or a bug.

    Expected is an error that carries a message — ``infrastructure`` worded it, so the line
    holds that message at WARNING, with no traceback.  Anything else is a bug: its kind and
    its frames at ERROR.  Neither line holds a foreign error's own words, which can quote the
    text that choked it; the kinds of the errors it was raised from say what they were.

    The one that handles an error logs it: catching it and not re-raising, or handing it to a
    caller that shows it.  Raising it, or passing it on, logs nothing.
    """
    causes = _causes_of(error)
    _mark_logged(error)
    if _is_expected(error):
        said = plain_text(message_of(error), own_words=False)
        if causes:
            said += f" (caused by {', '.join(_kind_of(cause) for cause in causes)})"
        logger.warning("%s: %s", what, said)
        return
    lines = [f"{what}: {_kind_of(error)}\n{_frames_of(error)}"]
    lines += [f"caused by {_kind_of(cause)}\n{_frames_of(cause)}" for cause in causes]
    logger.error("%s", "".join(lines))


def log_once(logger: logging.Logger, what: str, error: BaseException) -> None:
    """Write *error* like :func:`log_failure`, unless a catch point already wrote it.

    A failure travels: the task that raised it writes it, and the one that shows it to the
    teacher would write the same failure a second time.  The error carries the mark of its line
    (``__dict__``), so the second one is skipped and the file keeps one line per failure.
    """
    if getattr(error, LOGGED, False):
        return
    log_failure(logger, what, error)


#: How many errors of a chain a line names: a chain longer than this is a loop in disguise.
_MAX_CAUSES = 5
#: The attribute an error carries once a line about it was written; see :func:`log_once`.
LOGGED = "_teachers_teammate_logged"


def _mark_logged(error: BaseException) -> None:
    """Let *error* say that a line about it was written, when its kind lets it."""
    try:
        setattr(error, LOGGED, True)
    except AttributeError:
        pass  # an error of a kind without a __dict__: the mark is only a saving, not a rule


def _causes_of(error: BaseException) -> list[BaseException]:
    """Return the errors *error* was raised from or while handling, nearest first."""
    causes: list[BaseException] = []
    seen = {id(error)}
    current = error
    while len(causes) < _MAX_CAUSES:
        nxt = current.__cause__ or (None if current.__suppress_context__ else current.__context__)
        if nxt is None or id(nxt) in seen:
            break
        causes.append(nxt)
        seen.add(id(nxt))
        current = nxt
    return causes


def _frames_of(error: BaseException) -> str:
    """Return the frames *error* travelled through, without its own words."""
    return "".join(traceback.format_tb(error.__traceback__))


def _is_expected(error: BaseException) -> bool:
    """Whether *error* carries a message, which makes it expected rather than a bug."""
    return bool(error.args) and isinstance(error.args[0], Message)


def _kind_of(error: BaseException) -> str:
    """Return what a line says of *error*: its kind, and an ``OSError``'s code and reason."""
    if not isinstance(error, OSError) or error.errno is None:
        return type(error).__name__
    return f"{type(error).__name__} (errno {error.errno}: {error.strerror})"


def _plain_value(value: Arg, own_words: bool) -> str:
    """Return *value* for the plain form of a message."""
    if isinstance(value, Message):
        return plain_text(value, own_words=own_words)
    if isinstance(value, tuple):
        return SEPARATOR.join(_plain_value(item, own_words) for item in value)
    return str(value)


def _storable(value: object) -> bool:
    """Whether *value* is one of the values a message may carry."""
    if isinstance(value, str | int | float | Message) or value is None:
        return True
    return isinstance(value, tuple) and all(isinstance(item, Message) for item in value)


def render_all(messages: Iterable[Message]) -> str:
    """Return *messages* as one line, in the order they were produced, each worded."""
    return SEPARATOR.join(str(message) for message in messages)


# ── The base cases ────────────────────────────────────────────────────────────


def detail(text: str) -> Message:
    """Return the own words of an error the app did not write."""
    return Message(MessageKey.DETAIL, {"text": text})


# ── The reading engines, their downloads and the Add-ons page ────────────────


def addons_update_needs_install() -> Message:
    """Return the refusal to update add-ons in a bundled copy of the app."""
    return Message(MessageKey.ADDONS_UPDATE_NEEDS_INSTALL)


def spacy_required() -> Message:
    """What the app says about anonymization without spaCy: the library is required for it."""
    return Message(MessageKey.SPACY_REQUIRED)


def spacy_not_loadable(reason: Message) -> Message:
    """Return what the app says about a spaCy that is installed but cannot be imported."""
    return Message(MessageKey.SPACY_NOT_LOADABLE, {"reason": reason})


def provider_not_loaded(provider: str) -> Message:
    """Return what the app says about a provider whose own package cannot be loaded."""
    return Message(MessageKey.PROVIDER_NOT_LOADED, {"provider": provider})


def unknown_provider_error(provider: str, available: str) -> Message:
    """Return what the app says about a provider name no module of the app has."""
    return Message(
        MessageKey.UNKNOWN_PROVIDER_ERROR,
        {
            "provider": provider,
            "available": available,
        },
    )


def download_cancelled() -> Message:
    """Return the note about a download the teacher cancelled."""
    return Message(MessageKey.DOWNLOAD_CANCELLED)


def download_damaged(name: str) -> Message:
    """Return what a download says about a file whose checksum does not match."""
    return Message(MessageKey.DOWNLOAD_DAMAGED, {"name": name})


def models_not_downloaded(repo: str) -> Message:
    """Return what the app says about the model files of *repo* while they are missing."""
    return Message(MessageKey.MODELS_NOT_DOWNLOADED, {"repo": repo})


def reader_model_mismatch(reason: Message | None = None) -> Message:
    """Return the same sentence for a reader that failed as it loaded, naming *reason*."""
    return Message(MessageKey.READER_MODEL_MISMATCH, {"reason": reason})


def package_not_installed(package: str, *, installed_app: bool) -> Message:
    """Return the install hint of a provider package that is missing."""
    return Message(
        MessageKey.PACKAGE_NOT_INSTALLED, {"package": package, "installed_app": installed_app}
    )


def model_pull_failed(reason: Message) -> Message:
    """Return what a download of a model from the AI server says when it breaks off."""
    return Message(MessageKey.MODEL_PULL_FAILED, {"reason": reason})


# ── The saved work ─────────────────────────────────────────────────────────


def work_saved_by_newer_version(name: str) -> Message:
    """Return why the saved work of *name* is left alone: a newer version of the app wrote it."""
    return Message(MessageKey.WORK_SAVED_BY_NEWER_VERSION, {"name": name})


# ── What a run says about a file and what it writes ────────────────────────


def image_not_encoded(path: Path) -> Message:
    """Return what a run says about a page image that could not be encoded."""
    return Message(MessageKey.IMAGE_NOT_ENCODED, {"path": str(path)})


def image_not_written(path: Path, reason: Message) -> Message:
    """Return what a run says about a page image that could not be written to disk."""
    return Message(MessageKey.IMAGE_NOT_WRITTEN, {"path": str(path), "reason": reason})


def unsupported_input_suffix(suffix: str, supported: str) -> Message:
    """Return what a run says about an input file whose type no provider reads."""
    return Message(MessageKey.UNSUPPORTED_INPUT_SUFFIX, {"suffix": suffix, "supported": supported})


def preview_needs_images(name: str) -> Message:
    """Return what a preview says about a file whose pages are not images."""
    return Message(MessageKey.PREVIEW_NEEDS_IMAGES, {"name": name})


def image_unreadable(path: Path) -> Message:
    """Return what a run says about a page image it could not load."""
    return Message(MessageKey.IMAGE_UNREADABLE, {"path": str(path)})


def empty_ocr_answer() -> Message:
    """What a run says about a page the model answered with nothing at all."""
    return Message(MessageKey.EMPTY_OCR_ANSWER)


def cache_save_failed(reason: Message) -> Message:
    """Return what a run says about the text of a file it read but could not keep."""
    return Message(MessageKey.CACHE_SAVE_FAILED, {"reason": reason})


def input_read_failed(reason: Message) -> Message:
    """Return what a run says about an input file it could not read from disk."""
    return Message(MessageKey.INPUT_READ_FAILED, {"reason": reason})


def output_overwrites_input(name: str) -> Message:
    """Return what a run says about the text file that would replace its own input document."""
    return Message(MessageKey.OUTPUT_OVERWRITES_INPUT, {"name": name})


def output_read_back_as_input(shown: str) -> Message:
    """Return what a run says about an output file the next run would read as an input."""
    return Message(MessageKey.OUTPUT_READ_BACK_AS_INPUT, {"shown": shown})


def output_replaces_another(shown: str, owner: str) -> Message:
    """Return what a run says about two documents that would write the same output file."""
    return Message(MessageKey.OUTPUT_REPLACES_ANOTHER, {"shown": shown, "owner": owner})


def personal_info_removal_failed(reason: Message) -> Message:
    """Return what a run says about a file whose personal data could not be hidden."""
    return Message(MessageKey.PERSONAL_INFO_REMOVAL_FAILED, {"reason": reason})


def personal_info_restore_failed(reason: Message) -> Message:
    """Return what a run says about a file whose personal data could not be put back."""
    return Message(MessageKey.PERSONAL_INFO_RESTORE_FAILED, {"reason": reason})


def preprocessing_failed(reason: Message) -> Message:
    """Return what a run says about a page it could not prepare."""
    return Message(MessageKey.PREPROCESSING_FAILED, {"reason": reason})


def preview_save_failed(reason: Message) -> Message:
    """Return what a run says about a preview image it could not store."""
    return Message(MessageKey.PREVIEW_SAVE_FAILED, {"reason": reason})


def personal_info_setup_failed(reason: Message) -> Message:
    """Return what a run says about the anonymizer it could not build."""
    return Message(MessageKey.PERSONAL_INFO_SETUP_FAILED, {"reason": reason})


def storage_root_unresolved(reasons: str) -> Message:
    """Return what the app says when it found no folder it may write to, *reasons* one per line."""
    return Message(MessageKey.STORAGE_ROOT_UNRESOLVED, {"reasons": reasons})


def ocr_failed(page: int | None, reason: Message) -> Message:
    """Return what a run says about one *page* the reader could not read."""
    return Message(MessageKey.OCR_FAILED, {"page": page, "reason": reason})


def input_without_content(name: str) -> Message:
    """Return what a run says about an input file that had neither an image nor text."""
    return Message(MessageKey.INPUT_WITHOUT_CONTENT, {"name": name})


def input_without_units(name: str) -> Message:
    """Return what a run says about an input file that yielded no unit at all."""
    return Message(MessageKey.INPUT_WITHOUT_UNITS, {"name": name})


# ── A server and the answers it gives ──────────────────────────────────────


def empty_answer() -> Message:
    """What a request says about a model that answered with nothing at all."""
    return Message(MessageKey.EMPTY_ANSWER)


def empty_answer_cut_off() -> Message:
    """The same, for a model whose reasoning used up its output limit before any text."""
    return Message(MessageKey.EMPTY_ANSWER_CUT_OFF)


def answer_abandoned(seconds: float) -> Message:
    """Return what a request says about an answer still being written after *seconds*."""
    return Message(MessageKey.ANSWER_ABANDONED, {"seconds": seconds})


def cannot_connect(url: str) -> Message:
    """Return what the app says about a server it cannot open a connection to."""
    return Message(MessageKey.CANNOT_CONNECT, {"url": url})


def cannot_reach(url: str, reason: Message) -> Message:
    """Return what the app says about a server whose answer could not be read."""
    return Message(MessageKey.CANNOT_REACH, {"url": url, "reason": reason})


def connection_timed_out(url: str) -> Message:
    """Return what the app says about a server that answered too late."""
    return Message(MessageKey.CONNECTION_TIMED_OUT, {"url": url})


def contact_failed(url: str, reason: Message) -> Message:
    """Return what the app says about a server that failed in a way it cannot name."""
    return Message(MessageKey.CONTACT_FAILED, {"url": url, "reason": reason})


def http_error(reason: Message) -> Message:
    """Return what a request says about an answer the server refused with an HTTP error."""
    return Message(MessageKey.HTTP_ERROR, {"reason": reason})


def model_not_found(model: str, available: str) -> Message:
    """Return what the app says about a model the server does not hold, naming *available*."""
    return Message(MessageKey.MODEL_NOT_FOUND, {"model": model, "available": available})


def no_first_token(seconds: float) -> Message:
    """Return what a request says about a model that sent nothing within *seconds*."""
    return Message(MessageKey.NO_FIRST_TOKEN, {"seconds": seconds})


def no_models_installed(url: str) -> Message:
    """Return what the app says about a server that answered but holds no model."""
    return Message(MessageKey.NO_MODELS_INSTALLED, {"url": url})


def unreachable(url: str, reason: Message) -> Message:
    """Return what a proofreading request says about the server it cannot reach."""
    return Message(MessageKey.UNREACHABLE, {"url": url, "reason": reason})


# ── Comparing writing models on one text ────────────────────────────────────


def model_not_compared(model: str, reason: Message) -> Message:
    """Return why a model took no part in a comparison: it could not answer this text."""
    return Message(MessageKey.MODEL_NOT_COMPARED, {"model": model, "reason": reason})


def proofreading_not_compared() -> Message:
    """Return why no model can answer: the settings have proofreading switched off."""
    return Message(MessageKey.PROOFREADING_NOT_COMPARED)


def marks_not_replaced(*, locked: bool) -> Message:
    """Return why the teacher's verdicts on a text are left as they are: they cannot be read.

    *locked* says the file cannot be read now; otherwise a newer version of the app wrote it.
    """
    return Message(MessageKey.MARKS_NOT_REPLACED, {"locked": locked})


# ── The command line ───────────────────────────────────────────────────────


def config_file_missing(path: Path) -> Message:
    """Return what ``--config`` says about a file that is not there."""
    return Message(MessageKey.CONFIG_FILE_MISSING, {"path": str(path)})
