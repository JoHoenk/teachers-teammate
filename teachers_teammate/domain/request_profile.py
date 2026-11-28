"""What the app sends a model: the request profile of a proofreading step and its settings."""

from __future__ import annotations

from dataclasses import dataclass, replace

from .correction_prompts import correct_system, prompt_identity
from .data_location import PROVIDER_OLLAMA, is_ollama
from .model_tags import measured_value, tag_value
from .proofreading.result import ExchangeKind, StepName, stored_exchange_id

DEFAULT_CORRECTION_TEMPERATURE = 0.2
"""The proofreading temperature of a step whose model has no measured or recommended value.

The app's long-time value, and what every online service gets in every step.
"""


DEFAULT_REVIEW_TEMPERATURE = 0.7
"""The content-review temperature of a model whose maker states none."""


OCR_TEMPERATURE = 0.0
"""Text recognition is deterministic: reading a page has one right answer."""


MAKER_TEMPERATURES: dict[str, float] = {
    "gemma4:e4b": 1.0,
    "gemma4:12b": 1.0,
    "gemma4:e2b": 1.0,
    "qwen3:4b": 0.7,
    "qwen3.5:9b": 0.7,
    "qwen3.5:4b": 0.7,
    "ministral-3:14b": 0.05,
    "ministral-3:8b": 0.05,
    "ministral-3:3b": 0.05,
    "mistral-nemo:12b": 0.3,
    "nemotron-3.5-bolt:e4b": 1.0,
    "lfm2.5:8b": 0.2,
    "lfm2.5:24b": 0.1,
}
"""The temperature each model's maker recommends (model cards read on 2026-11-11).

Steps ② and ③ run at this value, and so does the content review: they answer a small JSON
object, and no measurement has compared their temperatures yet.  Step ① does not — it rewrites
the text, and its temperature is measured (:data:`STEP_ONE_TEMPERATURES`).

Teachers cannot set a temperature: the app sends these values, so a model is used the way its
maker tuned it and the evaluation tools measure exactly what a teacher's run sends.  Qwen's value
is the card's *non-thinking* row, because the app sends ``think=False``; the tags ship the
thinking value.  Ministral's card says "below 0.1", so 0.05.  Liquid's cards say 0.2 for
``lfm2.5:8b`` and 0.1 for ``lfm2.5:24b`` (with a top-k and a repetition penalty the tags carry);
the 8B entry equals the default and is listed so the table says the card states it.  A model whose
maker states nothing, or states 0.2, is absent and gets the default: ``gpt-oss:20b``,
``mistral:latest``, the Granite, Apertus and EuroLLM builds.  The table also covers models the
catalog does not list, because the evaluation tools check candidates before they are catalogued.
"""


STEP_ONE_TEMPERATURES: dict[str, float] = {
    "qwen3.5:4b": 0.05,
    "qwen3.5:9b": 0.05,
    "ministral-3:8b": 0.05,
    "ministral-3:14b": 0.05,
    "ministral-3:3b": 0.05,
    "mistral-nemo:12b": 0.05,
}
"""The measured temperature of step ① for the models that want a low one.

Step ① rewrites the text, so its temperature is measured rather than taken from the card: 0.05,
0.4, 0.7 and 1.0 over twelve models, three repeats each, on the official test set.  No model was
better above 0.4, and these six fall with every step up, so they keep 0.05.  Every other model is
tied with its best value and gets :data:`STEP_ONE_DEFAULT_TEMPERATURE`.  ``ministral-3:3b`` was
not measured itself: its card says "below 0.1" and its family falls with every step up, so it
keeps the family's value.
"""


STEP_ONE_PROMPTS: dict[str, str] = {}
"""The step-① prompt variant (``CORRECT_VARIANTS``) a model is sent, by tag.

Empty until a measurement on two test sets shows a variant helps a model: the standard
``CORRECT_SYSTEM`` goes to every other model.  Adopting one is a line here, a changed cache key
for that model only, and nothing else.  Another quantisation of a listed model (``…:9b-q8_0`` of
``…:9b``) is sent the same variant, as it is run at the same temperature.
"""


STEP_ONE_DEFAULT_TEMPERATURE = 0.4
"""The measured temperature of step ① for every model not in :data:`STEP_ONE_TEMPERATURES`.

0.4 rather than 0.05 although both are tied with the best value: one model lost text at 0.05, and
the one runaway loop seen so far was at a low temperature.
"""


THINK_LEVELS: dict[str, str] = {"gpt-oss": "low"}
"""Model families that take a reasoning *level* instead of the on/off flag the app sends.

``gpt-oss`` ignores ``think=False`` and reasons at its default level on every request (measured
2026-11-12: 788 characters of hidden reasoning and 260 answer tokens for one corrected sentence,
against 76 characters and 41 tokens with ``"low"``), which is what ran a full page past the
window.  Proofreading needs next to no reasoning, so such a family is asked for the lowest level;
:func:`request_profile` puts it into the profile, so the cache key, the evaluation tools and the
request itself read the same value.
"""


def think_level(tag: str) -> str | None:
    """Return the reasoning level the model *tag* has to be asked for, ``None`` for the flag.

    Matches the family at the start of the model name, so a Hugging Face build such as
    ``hf.co/unsloth/gpt-oss-20b-GGUF:UD-Q4_K_XL`` is covered as well.
    """
    name = tag.lower().rsplit("/", 1)[-1]
    return next((level for family, level in THINK_LEVELS.items() if name.startswith(family)), None)


SAMPLING_OPTIONS: frozenset[str] = frozenset(
    {"top_k", "top_p", "min_p", "presence_penalty", "frequency_penalty", "repeat_penalty"}
)
"""The sampling values of Ollama a profile may set besides the temperature.

``seed`` and ``num_ctx`` are not among them: the seed belongs to a run, the window to the app.
"""


def sampling_text(value: float) -> str:
    """Return *value* as the text that names it in a file name and in a cache key.

    Twelve significant digits, so two values a request tells apart never share a name.
    """
    return format(value, ".12g")


@dataclass(frozen=True)
class StepProfile:
    """How one step of the exchange is sent: its temperature and its thinking.

    A step is one kind of request (① correct, ② explain, ③ unclear).  The steps differ in what
    they ask for — ① rewrites the text, ② and ③ answer a small JSON object — so they may differ
    in temperature and in what a reasoning model is told.
    """

    #: The temperature the request is sent at.
    temperature: float = DEFAULT_CORRECTION_TEMPERATURE
    #: What the request tells a reasoning model about thinking: ``False`` (off), or the level
    #: (``"low"``) of a family that ignores the flag (see :data:`THINK_LEVELS`).
    think: bool | str = False
    #: Sampling values the request sends besides the temperature, as sorted ``(name, value)``
    #: pairs of :data:`SAMPLING_OPTIONS`.  Empty: the model's own tag decides (Ollama applies the
    #: values of the model's Modelfile), which is what every request sent until a measurement
    #: shows that a model needs another value.
    options: tuple[tuple[str, float], ...] = ()
    #: The id of the system-prompt variant the step sends (``CORRECT_VARIANTS``), or ``""`` for
    #: the standard prompt.  Only step ① has variants; the profile refuses one on ② or ③.
    prompt: str = ""

    def __post_init__(self) -> None:
        """Refuse an option or a prompt no request may carry, and keep the pairs in one order."""
        if self.prompt:
            correct_system(self.prompt)  # raises for an unknown variant
        unknown = sorted({name for name, _ in self.options} - SAMPLING_OPTIONS)
        if unknown:
            allowed = ", ".join(sorted(SAMPLING_OPTIONS))
            raise ValueError(f"unknown sampling option {unknown[0]!r} (allowed: {allowed})")
        names = [name for name, _ in self.options]
        repeated = sorted({name for name in names if names.count(name) > 1})
        if repeated:
            raise ValueError(f"sampling option {repeated[0]!r} is given more than once")
        for name, value in self.options:
            if name == "top_k" and not float(value).is_integer():
                raise ValueError(f"sampling option 'top_k' must be a whole number, not {value:g}")
        object.__setattr__(self, "options", tuple(sorted(self.options)))

    def sampling(self) -> dict[str, float]:
        """Return the extra sampling values as the options dict of a request."""
        return dict(self.options)


@dataclass(frozen=True)
class RequestProfile:
    """How the app asks one model to proofread; the default is what every model gets.

    A knob enters only with a measured reason, and each one is a new evaluation protocol version:
    the request the app sends is what a run measures.
    """

    #: Which exchange proofreads the text (see :class:`~.proofreading.result.ExchangeKind`).
    exchange: ExchangeKind = ExchangeKind.TEXT_FIRST
    #: The three steps of the exchange.
    correct: StepProfile = StepProfile()
    explain: StepProfile = StepProfile()
    unclear: StepProfile = StepProfile()
    #: Whether step ③ runs at all.  On for every model; only a tuning decision turns it off for
    #: a tier, because a text whose meaning a reader cannot tell is what a teacher most needs.
    unclear_check: bool = True
    #: How many edits one explain request carries at most.
    explain_batch: int = 20
    #: Add the compact-JSON rule to the fixed part of the previous review request.  The exchange
    #: does not send that request; the evaluation tools still measure it.
    compact_json: bool = False

    def __post_init__(self) -> None:
        """Refuse a prompt variant on a step that has none: only step ① rewrites the text."""
        if self.explain.prompt or self.unclear.prompt:
            raise ValueError("only step ① has prompt variants")

    @property
    def temperature(self) -> float:
        """Return the temperature of step ①, the step that rewrites the text."""
        return self.correct.temperature

    @property
    def review_temperature(self) -> float:
        """Return the temperature of the JSON-answering steps ② and ③.

        It is also the temperature an online provider's chat model is built at
        (``Config.correction_temperature``).
        """
        return self.explain.temperature

    @property
    def think(self) -> bool | str:
        """Return what step ① tells a reasoning model about thinking."""
        return self.correct.think

    def step(self, name: StepName) -> StepProfile:
        """Return the profile of the step *name*; R's review step uses ①'s."""
        if name == "explain":
            return self.explain
        if name == "unclear":
            return self.unclear
        return self.correct

    def fingerprint(self) -> str:
        """Return everything of this profile that changes a request, as one string.

        Part of the correction cache key: two runs whose fingerprints differ send different
        requests, so an answer of one must not be reused for the other.
        """
        steps = "|".join(
            f"{step.temperature:g}/{step.think}"
            + "".join(f"/{name}={sampling_text(value)}" for name, value in step.options)
            # Only a step with a prompt variant gets an element, so every other fingerprint
            # stays what it was; the text's hash makes an edited variant another request.
            + (f"/prompt={prompt_identity(step.prompt)}" if step.prompt else "")
            for step in (self.correct, self.explain, self.unclear)
        )
        return "|".join(
            [
                stored_exchange_id(self.exchange),
                steps,
                "unclear" if self.unclear_check else "no-unclear",
                str(self.explain_batch),
            ]
        )


DEFAULT_REQUEST_PROFILE = RequestProfile()
"""What a model the catalog does not know gets: the request that broke nobody."""


def recommended_temperature(tag: str) -> float | None:
    """Return the temperature the maker of *tag* recommends, or ``None`` when it states none."""
    return tag_value(MAKER_TEMPERATURES, tag)


def step_one_temperature(tag: str) -> float | None:
    """Return the measured temperature of step ① for *tag*, ``None`` when it was not measured.

    A variant of a measured tag (``qwen3.5:4b-q8_0``: the same model in another quantisation)
    has its model's value, because the models in :data:`STEP_ONE_TEMPERATURES` fall with every
    step up and a variant that fell through to the default would run at 0.4.
    """
    return measured_value(STEP_ONE_TEMPERATURES, tag)


def _temperature(tag: str, provider: str, default: float) -> float:
    """Return the maker's recommendation for a local model, else *default*.

    Only the table of Ollama tags applies to Ollama: another provider's model of the same name is
    a different service with its own defaults.
    """
    if not is_ollama(provider):
        return default
    recommended = recommended_temperature(tag)
    return default if recommended is None else recommended


def _step_one_temperature(tag: str, provider: str) -> float:
    """Return the temperature step ① is sent at: measured for a local model, else the default."""
    if not is_ollama(provider):
        return DEFAULT_CORRECTION_TEMPERATURE
    measured = step_one_temperature(tag)
    return STEP_ONE_DEFAULT_TEMPERATURE if measured is None else measured


def _step_one_prompt(tag: str, provider: str) -> str:
    """Return the step-① prompt variant of a local model, ``""`` for the standard prompt."""
    if not is_ollama(provider):
        return ""
    return measured_value(STEP_ONE_PROMPTS, tag) or ""


def profile_for(base: RequestProfile, tag: str, provider: str = PROVIDER_OLLAMA) -> RequestProfile:
    """Return *base* with the settings the app sends for *tag*.

    Step ① runs at its measured temperature (:data:`STEP_ONE_TEMPERATURES`), steps ② and ③ at
    the maker's (:data:`MAKER_TEMPERATURES`), because no measurement has compared theirs yet.
    """
    level = think_level(tag) if is_ollama(provider) else None
    maker = _temperature(tag, provider, DEFAULT_CORRECTION_TEMPERATURE)
    return replace(
        base,
        correct=StepProfile(
            temperature=_step_one_temperature(tag, provider),
            think=level or False,
            prompt=_step_one_prompt(tag, provider),
        ),
        explain=StepProfile(temperature=maker, think=level or False),
        unclear=StepProfile(temperature=maker, think=level or False),
    )


def review_temperature(tag: str, provider: str = PROVIDER_OLLAMA) -> float:
    """Return the temperature the content review is sent at for *tag*."""
    return _temperature(tag, provider, DEFAULT_REVIEW_TEMPERATURE)
