"""The Ollama models the app knows about and the settings the catalog gives them."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .data_location import PROVIDER_OLLAMA, is_ollama
from .model_tags import normalize_tag
from .request_profile import DEFAULT_REQUEST_PROFILE, RequestProfile, profile_for

ModelCategory = Literal["ocr", "general"]


#: The tasks a model may be used for: a catalog model has exactly one, a model the
#: Ollama server reports as able to read images has both.
ModelUses = frozenset[ModelCategory]


#: Static default models (config DEFAULTS, CLI, provider metadata).  They apply
#: regardless of hardware; the per-profile picks in :data:`RECOMMENDED` only drive
#: the hardware-aware GUI recommendations.
DEFAULT_OCR_MODEL = "hf.co/prithivMLmods/chandra-ocr-2-GGUF:Q8_0"


#: The default writing model must be one that passes the structured-output check:
#: proofreading explains every change, which needs an answer the model can be constrained to.
#: It is the model for settings that name none, not a pick: it runs on every computer type, and a
#: teacher who has it keeps it (see the ``catalog-roles`` gate record).
DEFAULT_WRITING_MODEL = "gemma4:e4b"


@dataclass(frozen=True)
class AnswerLength:
    """How long a model's answers are: 10th percentile, median, 90th percentile.

    Writing models: answer tokens per 100 words of student text, proofreading in review mode.
    Reading models: answer tokens per page.

    A range rather than one figure because the middle 80 % of one model's answers on one test
    set are about three times apart — how many errors a text has decides most of it, and no
    test can know that.
    """

    low: int
    typical: int
    high: int


@dataclass(frozen=True)
class ReferenceSpeed:
    """How fast a model runs on the reference machine, for models that are not installed here.

    The machine factor (measured speed ÷ reference speed) transfers these figures to another
    computer.  It only holds while the model fits the graphics card on both computers: a model
    that spills to the processor breaks the ratio.
    """

    write_tps: float
    """Answer tokens per second, proofreading in review mode."""
    read_tps: float | None = None
    """Request tokens per second; ``None`` when only the writing rate was measured."""


@dataclass(frozen=True)
class ModelLicence:
    """The terms a catalogued model's weights come under.

    The values are taken from each model's card.  No date of the last check is recorded: re-read
    the card (and write the date here) when a licence is added or changed.  ``gemma4`` is
    Apache-2.0 since Gemma 4; the Gemma Terms of Use cover the older models.

    The app never ships a model: the teacher pulls it from Ollama, so the licence only matters
    for *use*, not for redistribution.  A permissive licence (MIT, Apache-2.0) therefore gets no
    sentence — it adds duties for whoever passes the file on, and that is not us.  A licence that
    restricts use gets one, in :attr:`note`, shown next to the model in Settings → Add-ons and in
    the Setup Assistant.
    """

    #: The licence's name, as its authors write it.
    name: str
    #: Where the terms can be read.
    url: str
    #: One sentence for the model's row; empty when the licence does not restrict use.
    note: str = ""


@dataclass(frozen=True)
class OllamaModelSpec:
    """One catalogued Ollama model with approximate resource needs."""

    #: Normalised model tag, always including the ``:tag`` suffix.
    tag: str
    #: "ocr" (reading model) or "general" (writing model).  Settings → Add-ons
    #: lists the model in this group.
    category: ModelCategory
    #: Memory (GB) the model needs in total while running at the app's proofreading window of
    #: 8192 tokens: weights, context and working buffers, on the card and in main memory
    #: together.  Measured where a model was run (see ``evaluations/TOOLS.md``, *Model sizes and the memory agent*),
    #: otherwise estimated.
    memory_gb: float
    #: Approximate download size (GB), shown before the model is pulled.
    download_gb: float
    #: Plain-language one-liner shown as tooltip.
    note: str
    #: Further tasks the model is verified for besides ``category`` (a writing
    #: model that can also read images).
    also_uses: ModelUses = frozenset()
    #: Whether the model was checked to answer the correction review's JSON schema reliably
    #: (``tools/verify_model_catalog.py --structured-output``).  It only decides the
    #: ``explanations ✓`` badge; it never switches the proofreading mode.
    structured_output: bool = False
    #: Extra memory (GB) per additional 8192 tokens of context window, measured with
    #: ``verify_model_catalog fit`` at 8192, 16384 and 32768 tokens (see ``evaluations/TOOLS.md``,
    #: *Model sizes and the memory agent*).
    #: ``None`` — not measured, or a model the user added — means the window stays at 8192
    #: tokens.  It cannot be derived from ``memory_gb``: how fast a context cache grows depends
    #: on the model's attention design (sliding-window or linear-attention layers grow much
    #: more slowly).
    context_gb_per_8k: float | None = None
    #: How the app asks this model to proofread (see :class:`RequestProfile`).  Every catalog
    #: model starts on the default; a gate-1 run decides a value per model.
    request_profile: RequestProfile = DEFAULT_REQUEST_PROFILE
    #: How long this model's answers are (see :class:`AnswerLength`).  ``None`` — not measured;
    #: the estimate then takes the median of the catalogued models of the same kind.
    answer_length: AnswerLength | None = None
    #: How fast this model runs on the reference machine (see :class:`ReferenceSpeed`), so a
    #: model that is not installed here can still get an expected speed band.  ``None`` — not
    #: measured.
    reference_speed: ReferenceSpeed | None = None
    #: The terms the model's weights come under.  ``None`` — a model the user added, whose
    #: licence the app does not know.
    licence: ModelLicence | None = None


# Download sizes (decimal GB) were checked against the Ollama registry manifests
# with ``tools/verify_model_catalog.py`` on 2026-10-12 (the Hugging Face entry
# against the repository file list).  ``memory_gb`` cannot be read from a manifest.
#
# ``answer_length`` and ``reference_speed`` were measured on ``rx9070xt-server`` (Radeon
# RX 9070 XT) on 2026-11-13, review mode, window 8192, over 31 German, English and French
# learner texts, at the temperature the app sends for that model.  ``answer_length`` is answer
# tokens per 100 words of student text (10th percentile, median, 90th percentile);
# ``reference_speed`` is the middle of the measured writing rate.  A model without them was not
# measured: the estimate takes the median of its kind, and no expected band is shown for it.
#
# ``gemma4:e2b`` was measured on the same machine on 2026-11-27: ``fit`` gave its memory and its
# growth per 8k tokens, and its writing rate is the middle of the rate its stored answers reached.
# ``answer_length`` is left out for it: the stored runs send the app's exchange of three steps,
# which is not the one-request review the figures above come from.
OLLAMA_CATALOG: tuple[OllamaModelSpec, ...] = (
    OllamaModelSpec(
        tag=DEFAULT_OCR_MODEL,
        category="ocr",
        memory_gb=6.5,
        download_gb=5.5,
        note="Built for full pages with tables and handwriting. Keeps the page layout.",
        licence=ModelLicence(
            name="Modified OpenRAIL-M",
            url="https://huggingface.co/datalab-to/chandra-ocr-2/blob/main/LICENSE",
            # Read from the licence on 2026-11-18: personal use and research are free; other use
            # is free below $2M revenue or funding and when it does not compete with Datalab.
            # Schools are not named, so the note asks to check rather than claiming either way.
            note="Free for personal use and research. Other use is free only for organisations "
            "under $2M in revenue or funding; a school using it as an institution should "
            "check the licence.",
        ),
    ),
    OllamaModelSpec(
        tag="glm-ocr:q8_0",
        category="ocr",
        memory_gb=2.5,
        download_gb=1.6,
        note="Small and fast. Good for printed and mixed-language pages.",
        licence=ModelLicence(name="MIT", url="https://huggingface.co/zai-org/GLM-OCR"),
    ),
    OllamaModelSpec(
        tag="qwen3-vl:8b",
        category="ocr",
        memory_gb=7.0,
        download_gb=6.1,
        note="Best overall for handwriting.",
        licence=ModelLicence(
            name="Apache-2.0", url="https://huggingface.co/Qwen/Qwen3-VL-8B-Instruct"
        ),
    ),
    OllamaModelSpec(
        tag="deepseek-ocr:latest",
        category="ocr",
        memory_gb=7.0,
        download_gb=6.7,
        note="Strong on dense, structured pages.",
        licence=ModelLicence(name="MIT", url="https://huggingface.co/deepseek-ai/DeepSeek-OCR"),
    ),
    OllamaModelSpec(
        tag="qwen2.5vl:7b",
        category="ocr",
        memory_gb=7.0,
        download_gb=6.0,
        note="Older version of qwen3-vl.",
        licence=ModelLicence(
            name="Apache-2.0", url="https://huggingface.co/Qwen/Qwen2.5-VL-7B-Instruct"
        ),
    ),
    OllamaModelSpec(
        tag="qwen3:4b",
        category="general",
        memory_gb=4.0,
        download_gb=2.5,
        # Fails gate 1 (2026-11-12): it answers "nothing to correct" in streaks, about one run in
        # six, so the explanations badge stays off.
        note="Small writing model for computers without a graphics card. Slow: it thinks for "
        "several minutes per page.",
        licence=ModelLicence(name="Apache-2.0", url="https://huggingface.co/Qwen/Qwen3-4B"),
        context_gb_per_8k=1.23,
        answer_length=AnswerLength(low=791, typical=1086, high=1828),
        reference_speed=ReferenceSpeed(write_tps=131.0),
    ),
    OllamaModelSpec(
        tag="qwen3.5:9b",
        category="general",
        memory_gb=7.2,
        download_gb=6.6,
        note="Writing model for 8 GB and 16 GB graphics cards. Can also read scanned pages.",
        licence=ModelLicence(name="Apache-2.0", url="https://huggingface.co/Qwen/Qwen3.5-9B"),
        also_uses=frozenset({"ocr"}),
        structured_output=True,
        context_gb_per_8k=0.28,
    ),
    OllamaModelSpec(
        tag="gemma4:e2b",
        category="general",
        memory_gb=3.6,
        download_gb=4.6,
        # Passes gate 1, gate 2 and the fit (2026-11-28), on both test sets and on either machine.
        note="Smallest Gemma 4 build: the faster writing model, and the one for a computer "
        "without a graphics card.",
        licence=ModelLicence(
            name="Apache-2.0", url="https://ai.google.dev/gemma/docs/gemma_4_license"
        ),
        structured_output=True,
        context_gb_per_8k=0.08,
        reference_speed=ReferenceSpeed(write_tps=255.0),
    ),
    OllamaModelSpec(
        tag="gemma4:e4b",
        category="general",
        memory_gb=5.0,
        download_gb=6.6,
        note="Fast writing model that fits an 8 GB graphics card.",
        licence=ModelLicence(
            name="Apache-2.0", url="https://ai.google.dev/gemma/docs/gemma_4_license"
        ),
        structured_output=True,
        context_gb_per_8k=0.14,
        answer_length=AnswerLength(low=549, typical=1068, high=1824),
        reference_speed=ReferenceSpeed(write_tps=96.0),
    ),
    OllamaModelSpec(
        tag="gemma4:12b",
        category="general",
        memory_gb=9.5,
        download_gb=8.0,
        note="Best writing model for a 16 GB graphics card.",
        licence=ModelLicence(
            name="Apache-2.0", url="https://ai.google.dev/gemma/docs/gemma_4_license"
        ),
        structured_output=True,
        context_gb_per_8k=0.17,
        answer_length=AnswerLength(low=380, typical=1017, high=1879),
        reference_speed=ReferenceSpeed(write_tps=118.0),
    ),
    OllamaModelSpec(
        tag="mistral:latest",
        category="general",
        memory_gb=5.5,
        download_gb=4.4,
        note="General-purpose writing model. It does not reliably follow the review format.",
        licence=ModelLicence(
            name="Apache-2.0", url="https://huggingface.co/mistralai/Mistral-7B-v0.3"
        ),
    ),
    OllamaModelSpec(
        tag="mistral-nemo:12b",
        category="general",
        memory_gb=8.5,
        download_gb=7.1,
        note="General-purpose writing model. It does not reliably follow the review format.",
        licence=ModelLicence(
            name="Apache-2.0", url="https://huggingface.co/mistralai/Mistral-Nemo-Base-2407"
        ),
    ),
    OllamaModelSpec(
        tag="gpt-oss:20b",
        category="general",
        memory_gb=14.0,
        download_gb=13.8,
        # Passes gate 1 (2026-11-12) since the app asks it for the lowest reasoning level
        # (``THINK_LEVELS``, read through ``request_profile``); at its default level it reasoned past the window on a full page.
        note="Large model for a 16 GB graphics card. The app asks it to think as little as "
        "possible, which suits proofreading.",
        licence=ModelLicence(name="Apache-2.0", url="https://huggingface.co/openai/gpt-oss-20b"),
        structured_output=True,
        answer_length=AnswerLength(low=581, typical=1075, high=1897),
        reference_speed=ReferenceSpeed(write_tps=104.0),
    ),
)


def find_spec(tag: str) -> OllamaModelSpec | None:
    """Return the catalog entry for *tag* (normalised), or ``None`` when unknown."""
    wanted = normalize_tag(tag)
    for spec in OLLAMA_CATALOG:
        if spec.tag == wanted:
            return spec
    return None


def licence_note(tag: str) -> str:
    """Return the sentence a model's licence needs in its row, or ``""`` when it needs none.

    Only a licence that restricts *use* gets one (see :class:`ModelLicence`); a model the app
    does not know, or one under a permissive licence, says nothing.
    """
    spec = find_spec(tag)
    return spec.licence.note if spec is not None and spec.licence is not None else ""


def request_profile(tag: str, provider: str = PROVIDER_OLLAMA) -> RequestProfile:
    """Return how the app asks *tag* to proofread: the exchange, its steps and their settings.

    The one place the app, the instructions dialog, the cache key and the evaluation tools ask,
    so a profile change reaches all of them and no two can disagree about the request that is
    sent.  A model the catalog does not know gets the default request.  Step ① runs at its
    measured temperature (:data:`STEP_ONE_TEMPERATURES`), steps ② and ③ at the maker's
    (:data:`MAKER_TEMPERATURES`), because no measurement has compared theirs yet.
    """
    spec = find_spec(tag) if is_ollama(provider) else None
    base = DEFAULT_REQUEST_PROFILE if spec is None else spec.request_profile
    return profile_for(base, tag, provider)
