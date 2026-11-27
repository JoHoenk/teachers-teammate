"""The English wording of every key in :mod:`teachers_teammate.messages`.

One renderer per key, so :func:`render` is the only place a message becomes a sentence.  The
renderers are the functions and constants of the other modules of this package: a key's name
is the name of the sentence it stands for, and a test holds the two lists together.

:func:`install` hands the renderer to the package root, which then answers ``str(message)`` with
the sentence.  Nothing below the adapters imports this module.
"""

from __future__ import annotations

from collections.abc import Callable

from ..messages import Message, MessageKey, plain_text, render_all, set_renderer
from . import addons, compare, review, run, servers

#: The language the app speaks.  Only English exists so far.
ENGLISH = "en"


def _constant(sentence: str) -> Callable[[], str]:
    """Return the renderer of a sentence that has no variable part."""

    def phrase() -> str:
        return sentence

    return phrase


def _detail(text: str) -> str:
    """Return *text* as it stands: the own words of an error the app did not write."""
    return text


#: One renderer per key, in the order of ``MessageKey``.
_RENDERERS: dict[MessageKey, Callable[..., str]] = {
    MessageKey.DETAIL: _detail,
    # ── The reading engines, their downloads and the Add-ons page ─────────────
    MessageKey.ADDONS_UPDATE_NEEDS_INSTALL: _constant(addons.ADDONS_UPDATE_NEEDS_INSTALL),
    MessageKey.SPACY_REQUIRED: _constant(addons.SPACY_REQUIRED),
    MessageKey.SPACY_NOT_LOADABLE: addons.spacy_not_loadable,
    MessageKey.PROVIDER_NOT_LOADED: addons.provider_not_loaded,
    MessageKey.UNKNOWN_PROVIDER_ERROR: addons.unknown_provider_error,
    MessageKey.DOWNLOAD_CANCELLED: _constant(addons.DOWNLOAD_CANCELLED),
    MessageKey.DOWNLOAD_DAMAGED: addons.download_damaged,
    MessageKey.MODELS_NOT_DOWNLOADED: addons.models_not_downloaded,
    MessageKey.READER_MODEL_MISMATCH: addons.reader_model_mismatch,
    MessageKey.PACKAGE_NOT_INSTALLED: addons.package_not_installed,
    MessageKey.MODEL_PULL_FAILED: addons.model_pull_failed,
    # ── The saved work ────────────────────────────────────────────────────────
    MessageKey.WORK_SAVED_BY_NEWER_VERSION: review.work_saved_by_newer_version,
    # ── What a run says about a file and what it writes ───────────────────────
    MessageKey.IMAGE_NOT_ENCODED: run.image_not_encoded,
    MessageKey.IMAGE_NOT_WRITTEN: run.image_not_written,
    MessageKey.UNSUPPORTED_INPUT_SUFFIX: run.unsupported_input_suffix,
    MessageKey.PREVIEW_NEEDS_IMAGES: run.preview_needs_images,
    MessageKey.IMAGE_UNREADABLE: run.image_unreadable,
    MessageKey.EMPTY_OCR_ANSWER: _constant(run.EMPTY_OCR_ANSWER),
    MessageKey.CACHE_SAVE_FAILED: run.cache_save_failed,
    MessageKey.INPUT_READ_FAILED: run.input_read_failed,
    MessageKey.OUTPUT_OVERWRITES_INPUT: run.output_overwrites_input,
    MessageKey.OUTPUT_READ_BACK_AS_INPUT: run.output_read_back_as_input,
    MessageKey.OUTPUT_REPLACES_ANOTHER: run.output_replaces_another,
    MessageKey.PERSONAL_INFO_REMOVAL_FAILED: run.personal_info_removal_failed,
    MessageKey.PERSONAL_INFO_RESTORE_FAILED: run.personal_info_restore_failed,
    MessageKey.PREPROCESSING_FAILED: run.preprocessing_failed,
    MessageKey.PREVIEW_SAVE_FAILED: run.preview_save_failed,
    MessageKey.PERSONAL_INFO_SETUP_FAILED: run.personal_info_setup_failed,
    MessageKey.STORAGE_ROOT_UNRESOLVED: run.storage_root_unresolved,
    MessageKey.OCR_FAILED: run.ocr_failed,
    MessageKey.INPUT_WITHOUT_CONTENT: run.input_without_content,
    MessageKey.INPUT_WITHOUT_UNITS: run.input_without_units,
    # ── A server and the answers it gives ─────────────────────────────────────
    MessageKey.EMPTY_ANSWER: _constant(servers.EMPTY_ANSWER),
    MessageKey.EMPTY_ANSWER_CUT_OFF: _constant(servers.EMPTY_ANSWER_CUT_OFF),
    MessageKey.ANSWER_ABANDONED: servers.answer_abandoned,
    MessageKey.CANNOT_CONNECT: servers.cannot_connect,
    MessageKey.CANNOT_REACH: servers.cannot_reach,
    MessageKey.CONNECTION_TIMED_OUT: servers.connection_timed_out,
    MessageKey.CONTACT_FAILED: servers.contact_failed,
    MessageKey.HTTP_ERROR: servers.http_error,
    MessageKey.MODEL_NOT_FOUND: servers.model_not_found,
    MessageKey.NO_FIRST_TOKEN: servers.no_first_token,
    MessageKey.NO_MODELS_INSTALLED: servers.no_models_installed,
    MessageKey.UNREACHABLE: servers.unreachable,
    # ── Comparing writing models on one text ─────────────────────────────────
    MessageKey.MODEL_NOT_COMPARED: compare.model_not_compared,
    MessageKey.PROOFREADING_NOT_COMPARED: _constant(compare.PROOFREADING_NOT_COMPARED),
    MessageKey.MARKS_NOT_REPLACED: compare.marks_not_replaced,
    # ── The command line ─────────────────────────────────────────────────────
    MessageKey.CONFIG_FILE_MISSING: run.config_file_missing,
}


def render(message: Message) -> str:
    """Return the sentence of *message*, wording the messages it carries first."""
    phrase = _RENDERERS.get(message.key)
    if phrase is None:
        return plain_text(message)
    return phrase(**{name: _value(value) for name, value in message.args.items()})


def _value(value: object) -> object:
    """Return *value* as the renderer of a sentence takes it: a message becomes its words."""
    if isinstance(value, Message):
        return str(value)
    if isinstance(value, tuple):
        return render_all(value)
    return value


def install(language: str = ENGLISH) -> None:
    """Make ``str(message)`` answer with the sentence, in *language*; only English exists."""
    if language != ENGLISH:
        raise ValueError(f"no wording for {language}")
    set_renderer(render)
