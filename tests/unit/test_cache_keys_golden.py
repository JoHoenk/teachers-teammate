"""The cache keys of a stored result, pinned to the values they have today.

A key of the cache is the identity of a stored result: it says what a result was made from, and
the run reads and proofreads a document again exactly when its key differs from the stored one.
The rules that derive a key move between layers; the keys themselves must not move with them,
because a changed key makes every stored result stale — every document is read and proofread
again, and what was sent cannot be unsent.

So the digests below may change only with an intended bump of ``AUTO_VERSION``,
``PROMPT_VERSION``, ``READER_VERSION`` or the reader's fingerprint — never with a move of the
code that computes them, and never with a refactoring.  When a bump is the reason, update the
value here in the same commit that bumps it, and say so in the commit message.
"""

from __future__ import annotations

import json
from pathlib import Path

from teachers_teammate.cache_keys import (
    correction_key,
    ocr_key,
    preview_key,
    privacy_key,
    review_key,
    sent_record,
)
from teachers_teammate.config import Config, OcrConfig
from teachers_teammate.domain.context_budget import RunWindows
from teachers_teammate.domain.setting_values import PreprocessMethod
from teachers_teammate.domain.stages import Stage
from teachers_teammate.infrastructure.key_facts import MachineKeyFacts
from teachers_teammate.infrastructure.onnx.store import ModelStore
from tests.conftest import fake_page_models_store, make_config, make_key_facts

_PNG = Path("page.png")
_PDF = Path("worksheet.pdf")
_TXT = Path("notes.txt")

#: The two windows a run may resolve, and the base one the settings alone ask for.
_BASE = RunWindows(proofreading=8192, evaluation=8192)
_WIDE = RunWindows(proofreading=16384, evaluation=32768)

#: ``name -> digest`` of every OCR key of the matrix.
_OCR_KEYS: dict[str, str] = {
    "ollama/auto/models/pdf-never": "eec6f680763dc3dad9fb2c697aa97186f3aebb7bc8e41e91ed6917e324d9b56f",
    "ollama/none/png": "3ca2accbeae2301a268e2638f8884ce21704967e755c6c94d89b156bdc2db077",
    "ppocr/auto/models/pdf": "e0993d8718232db1ca0ed1b2b1fd25670ad0ee7ffc4db29a85eaae524bfb723d",
    "ppocr/auto/no-models/png": "88221786341dd66d6d8d279b7c38676a763780d60dd33439b8b356985d7b5b13",
    "ppocr/none/txt": "c7ab3e15ad602101a3844922447ae117c61b5ff26e509224203166f70cee523b",
}

#: ``name -> digest`` of every proofreading key of the matrix.
_CORRECTION_KEYS: dict[str, str] = {
    "ollama/no-windows": "0106e1f68710073cc5bb967eb92a85706cdc922cba32f7b70bb4b373107bc963",
    "ollama/windows-base": "0106e1f68710073cc5bb967eb92a85706cdc922cba32f7b70bb4b373107bc963",
    "ollama/windows-wide": "d4b3fcadac944d2497bc1f474e3406c5f24d0d82b113ca4dd635f559a7812065",
    "ollama/notes": "27c3c63e171d625114abeeb23a72066e5cf01d2da8ff69f6154be65183fd22a9",
    "ollama/text-output": "7da7c04522040cee7d321562d38235ee00468e4a336a454c047efd7180c28271",
    "openai/no-windows": "9c37ff17e56f8e8a7844b8119d6846f8ab51836aba2c1f4e43dfd8f88e302042",
    "openai/text-output": "c5790cdb9090adf9471db6f3e761b6f3e011df5e3ec5d9f23dcaa6173e926228",
}

#: ``name -> digest`` of every content-review key of the matrix.
_REVIEW_KEYS: dict[str, str] = {
    "ollama/no-windows": "ab86335b6af98d84b773be708abdacd259f55b31407e06c013a5fd35c539568c",
    "ollama/windows-wide": "e75d968f5506adba7dffe7a6bffadf762245713a395fd4e2d9a3ce2f423feeb7",
    "openai/no-windows": "d7bb7e7b9f6325deb24bdfbd98e684ef1fc215a40f4a3db7c33ece55b9195d27",
}

#: ``name -> digest`` of every privacy fingerprint of the matrix.
_PRIVACY_KEYS: dict[str, str] = {
    "hiding-off": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    "hiding-on/patterns": "0de9f711afff76a02b5bff982568f9bb6e7bde55fb56824a68e91572af65037d",
    "hiding-on/primary-model": "0fcbe8330400f0993b073501449b0e3d7b1f5740b88d8eda35cf594a5f8efa7f",
    "hiding-on/kept": "c31c9dc870b8919bd340e813ded91b4681ee57035715f486ed1c9746a7fe3549",
    "hiding-on/hidden": "b5c73d07cdad1f337acecce556a95bcddca901e033b67c610416aeed99fee094",
}

#: ``name -> how it was sent``, as the stored record holds it.
_SENT_RECORDS: dict[str, str] = {
    "content-review": (
        '{"endpoint": "", "hiding_on": true, "model": "gpt-4o-mini", "names_hidden": 0,'
        ' "privacy": "4768ee8973597f42e93af721fa17eede2b402fe50d81906855accadfd138038d",'
        ' "provider": "openai"}'
    ),
    "proofreading": (
        '{"endpoint": "192.168.0.7", "hiding_on": true, "model": "qwen2.5:7b",'
        ' "names_hidden": 3,'
        ' "privacy": "4768ee8973597f42e93af721fa17eede2b402fe50d81906855accadfd138038d",'
        ' "provider": "ollama"}'
    ),
}

#: ``name -> digest`` of every preview key of the matrix.
_PREVIEW_KEYS: dict[str, str] = {
    "ollama/auto/models/pdf-never": "c7ce1b10ad34ed4ef1c0bde3fb3ad1fa19ef49693a53eb8035f672f83ee31124",
    "ppocr/auto/models/pdf": "0f42aae7ffafe8c95b4bb3c00a0e302585a79b19983fb66348049338214069c7",
}


# ── The matrix ─────────────────────────────────────────────────────────────


def _ocr_configs(tmp_path: Path) -> dict[str, tuple[Config, Path, ModelStore]]:
    """Return ``name -> (config, source, model store)`` for the OCR keys of the matrix."""
    empty = ModelStore(tmp_path / "empty")
    pages = fake_page_models_store(tmp_path)
    auto_ppocr = OcrConfig(engine="ppocr", preprocess_method=PreprocessMethod.AUTO)
    plain_ppocr = OcrConfig(engine="ppocr", preprocess_method=PreprocessMethod.NONE)
    plain_ollama = OcrConfig(
        engine="ollama",
        model="llava:13b",
        temperature=0.2,
        preprocess_method=PreprocessMethod.NONE,
    )
    never = OcrConfig(
        engine="ollama",
        model="llava:13b",
        temperature=0.2,
        preprocess_method=PreprocessMethod.AUTO,
        pdf_text_layer="never",
    )
    return {
        "ppocr/auto/no-models/png": (make_config(tmp_path, ocr=auto_ppocr), _PNG, empty),
        "ppocr/auto/models/pdf": (make_config(tmp_path, ocr=auto_ppocr), _PDF, pages),
        "ollama/auto/models/pdf-never": (make_config(tmp_path, ocr=never), _PDF, pages),
        "ppocr/none/txt": (make_config(tmp_path, ocr=plain_ppocr), _TXT, empty),
        "ollama/none/png": (make_config(tmp_path, ocr=plain_ollama), _PNG, empty),
    }


def _proofreading_configs(tmp_path: Path) -> dict[str, Config]:
    """Return ``name -> config`` for the proofreading keys of the matrix."""
    ollama = make_config(
        tmp_path,
        correction_enabled=True,
        correction_provider="ollama",
        correction_model="qwen2.5:7b",
    )
    online = make_config(
        tmp_path,
        correction_enabled=True,
        correction_provider="openai",
        correction_model="gpt-4o-mini",
    )
    notes = make_config(
        tmp_path,
        correction_enabled=True,
        correction_provider="ollama",
        correction_model="qwen2.5:7b",
        correction_notes="  Please be kind.  ",
    )
    text_only = make_config(
        tmp_path,
        correction_enabled=True,
        correction_provider="ollama",
        correction_model="qwen2.5:7b",
        correction_output="text",
    )
    return {
        "ollama/no-windows": ollama,
        "ollama/windows-base": ollama,
        "ollama/windows-wide": ollama,
        "ollama/notes": notes,
        "ollama/text-output": text_only,
        "openai/no-windows": online,
        "openai/text-output": make_config(
            tmp_path,
            correction_enabled=True,
            correction_provider="openai",
            correction_model="gpt-4o-mini",
            correction_output="text",
            correction_notes="Please be kind.",
        ),
    }


def _review_configs(tmp_path: Path) -> dict[str, Config]:
    """Return ``name -> config`` for the content-review keys of the matrix."""
    ollama = make_config(
        tmp_path,
        evaluation_enabled=True,
        evaluate_provider="ollama",
        evaluate_model="qwen2.5:3b",
    )
    online = make_config(
        tmp_path,
        evaluation_enabled=True,
        evaluate_provider="openai",
        evaluate_model="gpt-4o-mini",
    )
    return {
        "ollama/no-windows": ollama,
        "ollama/windows-wide": ollama,
        "openai/no-windows": online,
    }


def _hidden_configs(tmp_path: Path) -> dict[str, Config]:
    """Return ``name -> config`` for the privacy fingerprints and the sent records."""
    patterns = [("EMAIL", r"[\w.+-]+@[\w-]+\.[\w.]+"), ("PHONE", r"\+?\d[\d /-]{5,}")]
    return {
        "hiding-off": make_config(tmp_path),
        # Not ``auto:system``: which language this computer is set to is not part of the key
        # written here (``test_cache_keys.py`` holds the automatic extra model).
        "hiding-on/patterns": make_config(
            tmp_path,
            anonymization_enabled=True,
            anonymizer_patterns=patterns,
            anonymizer_secondary_model="none",
        ),
        "hiding-on/primary-model": make_config(
            tmp_path,
            anonymization_enabled=True,
            anonymizer_patterns=patterns,
            anonymizer_primary_model="xx_ent_wiki_sm",
            anonymizer_secondary_model="none",
        ),
        "hiding-on/hidden": make_config(
            tmp_path,
            anonymization_enabled=True,
            anonymizer_patterns=patterns,
            anonymizer_hide=["Anna"],
            anonymizer_secondary_model="none",
        ),
        "hiding-on/kept": make_config(
            tmp_path,
            anonymization_enabled=True,
            anonymizer_patterns=patterns,
            anonymizer_keep=["Anna Müller"],
            anonymizer_secondary_model="none",
        ),
    }


# ── The keys ───────────────────────────────────────────────────────────────


def _ocr_keys(tmp_path: Path) -> dict[str, str]:
    """Return the OCR key of every case of the matrix."""
    return {
        name: ocr_key(config, source, MachineKeyFacts(store))
        for name, (config, source, store) in _ocr_configs(tmp_path).items()
    }


def _preview_keys(tmp_path: Path) -> dict[str, str]:
    """Return the preview key of the cases a preview is stored for."""
    configs = _ocr_configs(tmp_path)
    keys: dict[str, str] = {}
    for name in ("ppocr/auto/models/pdf", "ollama/auto/models/pdf-never"):
        config, source, store = configs[name]
        keys[name] = preview_key(config, source, "6f1c2a", MachineKeyFacts(store))
    return keys


def _correction_keys(tmp_path: Path) -> dict[str, str]:
    """Return the proofreading key of every case of the matrix."""
    windows = {
        "ollama/windows-base": _BASE,
        "ollama/windows-wide": _WIDE,
    }
    return {
        name: correction_key(config, windows.get(name), make_key_facts())
        for name, config in _proofreading_configs(tmp_path).items()
    }


def _review_keys(tmp_path: Path) -> dict[str, str]:
    """Return the content-review key of every case of the matrix."""
    windows = {"ollama/windows-wide": _WIDE}
    return {
        name: review_key(config, windows.get(name))
        for name, config in _review_configs(tmp_path).items()
    }


def _privacy_keys(tmp_path: Path) -> dict[str, str]:
    """Return the privacy fingerprint of every case of the matrix."""
    facts = make_key_facts()
    return {name: privacy_key(config, facts) for name, config in _hidden_configs(tmp_path).items()}


def _sent_records(tmp_path: Path) -> dict[str, str]:
    """Return how a proofreading and a content review are sent, as their record is stored."""
    config = make_config(
        tmp_path,
        anonymization_enabled=True,
        anonymizer_patterns=[("EMAIL", r"[\w.+-]+@[\w-]+\.[\w.]+")],
        anonymizer_secondary_model="none",
        correction_provider="ollama",
        correction_model="qwen2.5:7b",
        evaluate_provider="openai",
        evaluate_model="gpt-4o-mini",
        ollama_url="http://192.168.0.7:11434/",
    )
    return {
        "proofreading": json.dumps(
            sent_record(config, Stage.CORRECTION, names_hidden=3, facts=make_key_facts()).to_dict(),
            sort_keys=True,
        ),
        "content-review": json.dumps(
            sent_record(config, Stage.EVALUATION, facts=make_key_facts()).to_dict(),
            sort_keys=True,
        ),
    }


# ── The tests ──────────────────────────────────────────────────────────────


def test_the_ocr_keys_are_unchanged(tmp_path: Path) -> None:
    """
    Given  the readers, the preparations and the input formats a run may read a page with
    When   the OCR key of each is computed
    Then   every key is the one a stored result of today was made under, so the move of the
           rules reads no document again
    """
    actual = _ocr_keys(tmp_path)

    assert actual == _OCR_KEYS, json.dumps(actual, indent=4, sort_keys=True)


def test_the_preview_keys_are_unchanged(tmp_path: Path) -> None:
    """
    Given  the settings that decide the pixels of a prepared page
    When   the preview key of each is computed
    Then   every key is the stored one, so a preview is not prepared again after the move
    """
    actual = _preview_keys(tmp_path)

    assert actual == _PREVIEW_KEYS, json.dumps(actual, indent=4, sort_keys=True)


def test_the_proofreading_keys_are_unchanged(tmp_path: Path) -> None:
    """
    Given  a local and an online proofreading, the notes, the output modes and the windows
    When   the proofreading key of each is computed
    Then   every key is the stored one, so no proofreading is sent again after the move
    """
    actual = _correction_keys(tmp_path)

    assert actual == _CORRECTION_KEYS, json.dumps(actual, indent=4, sort_keys=True)


def test_the_content_review_keys_are_unchanged(tmp_path: Path) -> None:
    """
    Given  a local and an online content review, with the window the settings alone ask for
           and the one this computer allows
    When   the review key of each is computed
    Then   every key is the stored one, so no review is sent again after the move
    """
    actual = _review_keys(tmp_path)

    assert actual == _REVIEW_KEYS, json.dumps(actual, indent=4, sort_keys=True)


def test_the_privacy_fingerprints_are_unchanged(tmp_path: Path) -> None:
    """
    Given  personal-info removal off, on with patterns, on with a chosen model, and on with a
           hidden text and a kept text
    When   the fingerprint of each is computed
    Then   every fingerprint is the stored one, so a window keeps telling a result made with
           other privacy settings from one made with today's
    """
    actual = _privacy_keys(tmp_path)

    assert actual == _PRIVACY_KEYS, json.dumps(actual, indent=4, sort_keys=True)


def test_how_a_result_is_sent_is_unchanged(tmp_path: Path) -> None:
    """
    Given  a run whose names are hidden, on a server of the teacher's network
    When   the record of what a proofreading and a content review send is asked for
    Then   it names the same provider, model, endpoint, count and fingerprint as before, so
           what an earlier run really sent still reads right
    """
    actual = _sent_records(tmp_path)

    assert actual == _SENT_RECORDS, json.dumps(actual, indent=4, sort_keys=True)
