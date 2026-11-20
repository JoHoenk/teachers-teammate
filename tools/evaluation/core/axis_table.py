"""The axes of a run: one declaration per axis, and the codec every surface derives from it.

An axis is one attribute a run can vary in.  It used to be declared five times — in ``AXES``, in
``Protocol``'s fields, in ``to_dict``, in ``from_dict`` and in ``axis_values`` — so adding one meant
editing five places and forgetting one of them.  Here it is declared once:

* :attr:`Axis.field` names the ``Protocol`` attribute it is stored in;
* :attr:`Axis.dump` turns that attribute into the JSON value a file holds;
* :attr:`Axis.parse` turns the JSON value back;
* :attr:`Axis.label` prints the value the way a report names it;
* :attr:`Axis.tag` turns it into one path-safe segment of a file name;
* :attr:`Axis.compare_role` says how a comparison treats it.

``Protocol.to_dict``, ``Protocol.from_dict``, :func:`axis_values`, the file name's tags and every
label iterate :data:`AXES`, so a new axis is one :class:`Axis` line plus one dataclass field, and a
test asserts the two agree.

This module imports nothing from :mod:`.records`, so the record codec can use it.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import re
from typing import Any

from .modes import DEFAULT_EXCHANGE, DEFAULT_STEPS, Exchange, Step, exchange_of, steps_of

SAMPLING_PREFIX = "sampling."
"""How a single sampling option is named as an axis: ``sampling.presence_penalty``."""

MODEL = "model"
"""The compare role of an axis that names the model: two values are two models to compare."""

FIXED = "fixed"
"""The compare role of an axis a comparison holds fixed: it may not vary inside one comparison."""

DERIVED = "derived"
"""The compare role of an axis another axis determines: it may vary only with that axis."""

COMPARE_ROLES: tuple[str, ...] = (MODEL, FIXED, DERIVED)
"""Every role an axis can have; an axis without one is a test failure."""


def _identity(value: Any) -> Any:
    """Return *value* as it is: the dump and parse of an axis stored as it is."""
    return value


def _pairs_to_dict(pairs: Mapping[str, Any]) -> dict[str, Any]:
    """Return a tuple of pairs as the JSON object a file holds."""
    return dict(pairs)


def _sampling_from_dict(raw: Any) -> tuple[tuple[str, float], ...]:
    """Return a stored sampling object as the sorted pairs a protocol holds."""
    return tuple(sorted((str(name), float(value)) for name, value in (raw or {}).items()))


def _instructions_from_dict(raw: Any) -> tuple[tuple[str, str], ...]:
    """Return a stored instruction object as the sorted pairs a protocol holds."""
    return tuple(sorted((str(code), str(digest)) for code, digest in (raw or {}).items()))


def _steps_to_list(steps: tuple[Step, ...]) -> list[str]:
    """Return the steps as the list of values a file holds."""
    return [step.value for step in steps]


def _uses_to_list(uses: tuple[str, ...]) -> list[str]:
    """Return the request parts as the list a file holds."""
    return list(uses)


def _uses_from_list(raw: Any) -> tuple[str, ...]:
    """Return a stored list of request parts as the tuple a protocol holds."""
    return tuple(str(part) for part in raw or ())


def _exchange_to_text(exchange: Exchange) -> str:
    """Return the exchange as the value a file holds."""
    return exchange.value


def _exchange_from_text(raw: Any) -> Exchange:
    """Return a stored exchange value as the exchange it names."""
    return exchange_of(raw)


def _steps_from_list(raw: Any) -> tuple[Step, ...]:
    """Return a stored list of steps as the steps it names."""
    return steps_of(raw)


def plain(value: Any) -> Any:
    """Return *value* as something JSON can hold and two equal values compare equal in."""
    if isinstance(value, Mapping):
        return {str(key): plain(item) for key, item in sorted(value.items())}
    if isinstance(value, (list, tuple)):
        return [plain(item) for item in value]
    return value


_PATH_SAFE = re.compile(r"[^A-Za-z0-9._+-]")
"""Everything a tag value may not hold: a separator, a drive colon, a space."""


def path_safe(text: str) -> str:
    """Return *text* as one path segment: every character outside ``[A-Za-z0-9._+-]`` becomes ``-``.

    A tag value comes from a model tag or a prompt variant, so it can hold ``/`` and ``:``
    (``hf.co/bartowski/Qwen3-8B-GGUF:Q4_K_M``); a name must stay one segment on every platform.
    """
    return _PATH_SAFE.sub("-", text)


def digest8(digest: str) -> str:
    """Return the first eight hex digits of a model digest, ``unknown`` when there is none."""
    value = digest.strip()
    if ":" in value:  # Ollama writes "sha256:<hex>"
        value = value.split(":", 1)[1]
    return value[:8] if value else "unknown"


def model_slug(tag: str) -> str:
    """Return a model tag in the form a run id uses: lower case, no registry prefix, no marks.

    ``hf.co/bartowski/Qwen3-8B-GGUF:Q4_K_M`` becomes ``bartowski-qwen3-8b-gguf-q4_k_m``, so the
    id survives being a file name on every platform.
    """
    slug = tag.strip().lower()
    for prefix in ("hf.co/", "https://hf.co/"):
        if slug.startswith(prefix):
            slug = slug[len(prefix) :]
    return slug.replace("/", "-").replace(":", "-")


def _tag_value(value: Any) -> str:
    """Return *value* as one path-safe tag value; a list joins its values with ``+``.

    An empty list is written ``none``, so a run that ran no step of the exchange reads
    ``steps-none`` and not ``steps-``, which looks like a value that went missing.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return f"{value:g}"
    if isinstance(value, (list, tuple)):
        return "+".join(_tag_value(item) for item in value) or "none"
    return path_safe(str(value))


def _label_value(value: Any) -> str:
    """Return *value* as one readable word: a list joins with ``+``, a mapping with ``,``."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return f"{value:g}"
    if isinstance(value, Mapping):
        pairs = (f"{name}={_label_value(item)}" for name, item in sorted(value.items()))
        return ",".join(pairs) or "none"
    if isinstance(value, (list, tuple)):
        return "+".join(_label_value(item) for item in value) or "none"
    return str(value)


def _label_hash(value: Any) -> str:
    """Return a per-language hash object as its eight-digit digests, so a label names it."""
    if not isinstance(value, Mapping):
        return _label_value(value)
    return ",".join(f"{code}={digest8(str(digest))}" for code, digest in sorted(value.items()))


def _label_temperature(value: Any) -> str:
    """Return a temperature as ``temperature 0.4``, never as ``T0.4``."""
    return f"temperature {_label_value(value)}"


def _label_model(value: Any) -> str:
    """Return a model tag as it is: two tags that differ must read differently."""
    return str(value)


@dataclass(frozen=True)
class Axis:
    """One attribute a run can vary in, with everything every surface derives from it."""

    name: str
    """The name every surface uses: the key of the compare key, the tag of a file name, a label."""

    field: str
    """The ``Protocol`` attribute the axis is stored in."""

    default: Any
    """The value a run has when nothing says otherwise, in the form a file holds it.

    ``None`` for an axis that is always in the compare key.  A default is compared against the
    dumped value, so an axis at its default is left out of the key and out of the file name.
    """

    dump: Callable[[Any], Any] = _identity
    """Turn the attribute into the JSON value a file holds."""

    parse: Callable[[Any], Any] = _identity
    """Turn the JSON value back into the attribute."""

    label: Callable[[Any], str] = _label_value
    """Print the JSON value the way a report names it."""

    tag: Callable[[Any], str] = _tag_value
    """Turn the JSON value into one path-safe segment of a file name."""

    compare_role: str = MODEL
    """How a comparison treats the axis: :data:`MODEL`, :data:`FIXED` or :data:`DERIVED`."""

    values: tuple[str, ...] = ()
    """The readable values, for the docs and for ``compare --along``; empty when open-ended."""

    in_name: bool = True
    """Whether the axis can appear as a tag of the file name; a hash cannot."""

    doc: str = ""


AXES: tuple[Axis, ...] = (
    Axis(
        "model",
        "model",
        None,
        label=_label_model,
        doc="The model tag; two digests of one tag are two models.",
    ),
    Axis(
        "model_digest",
        "model_digest",
        None,
        label=digest8,
        doc="The digest of the weights; part of the model's identity.",
    ),
    Axis(
        "exchange",
        "exchange",
        DEFAULT_EXCHANGE.value,
        dump=_exchange_to_text,
        parse=_exchange_from_text,
        values=tuple(exchange.value for exchange in Exchange),
        compare_role=FIXED,
        doc="How the text travels to the model and back.",
    ),
    Axis(
        "steps",
        "steps",
        tuple(step.value for step in DEFAULT_STEPS),
        dump=_steps_to_list,
        parse=_steps_from_list,
        values=tuple(step.value for step in Step),
        compare_role=FIXED,
        doc="Which steps of the exchange ran; ``correct-only`` is ``text-first`` with one step.",
    ),
    Axis(
        "step_one_prompt",
        "step_one_prompt",
        "standard",
        compare_role=FIXED,
        doc="The variant of step ①'s prompt; its text is in ``instructions``.",
    ),
    Axis(
        "temperature",
        "temperature",
        None,
        label=_label_temperature,
        doc="The temperature the run asked at; never abbreviated to ``T``.",
    ),
    Axis(
        "sampling",
        "sampling",
        {},
        dump=_pairs_to_dict,
        parse=_sampling_from_dict,
        doc="The sampling options sent; each option is its own axis when compared.",
    ),
    Axis(
        "compact_json",
        "compact_json",
        False,
        compare_role=FIXED,
        doc="Whether the review request asked for compact JSON.",
    ),
    Axis(
        "num_ctx",
        "num_ctx",
        8192,
        compare_role=FIXED,
        doc="The context window the run measured at.",
    ),
    Axis(
        "provider",
        "provider",
        "ollama",
        compare_role=FIXED,
        doc="Who ran the model: ``ollama`` for a server of ours.",
    ),
    Axis(
        "answer_format_version",
        "answer_format_version",
        1,
        compare_role=FIXED,
        doc="The version of the answer schema the run asked for.",
    ),
    Axis(
        "uses",
        "uses",
        (),
        dump=_uses_to_list,
        parse=_uses_from_list,
        values=("level", "notes", "passages"),
        compare_role=FIXED,
        doc="The request parts the run sent.",
    ),
    Axis(
        "explainer",
        "explainer",
        "",
        tag=lambda value: _tag_value(model_slug(str(value))),
        compare_role=FIXED,
        doc="The model tag that wrote the explanations, when another model did.",
    ),
    Axis(
        "instructions",
        "instructions",
        {},
        dump=_pairs_to_dict,
        parse=_instructions_from_dict,
        label=_label_hash,
        in_name=False,
        compare_role=DERIVED,
        doc="The hash of what was sent, per language; identity, not a readable value.",
    ),
)
"""Every axis of a run, in the order the docs and the file name list them."""

AXIS_NAMES: tuple[str, ...] = tuple(axis.name for axis in AXES)
"""The axis names, for a reader that wants the list without the objects."""

AXIS_BY_NAME: dict[str, Axis] = {axis.name: axis for axis in AXES}
"""The axes by name, so ``compare --along`` can refuse a name it does not know."""

UNKNOWN_PREFIX = "unknown."
"""How an axis a newer writer added is named in a compare key: ``unknown.thinking``."""


def axis_values(protocol: Any) -> dict[str, Any]:
    """Return every axis of a protocol, keyed by its name, as the JSON values a file holds.

    Args:
        protocol: A :class:`~.records.Protocol`; typed loosely so a test can pass a stand-in.
    """
    return {axis.name: axis.dump(getattr(protocol, axis.field)) for axis in AXES}


def axis_label(name: str, value: Any) -> str:
    """Return one compare-key value as the label a report prints for it.

    The one label function: a row's name, a table's column and the page's facts all call it, so
    ``temperature`` is never ``T`` and two model tags never read the same.  A hash is cut to its
    eight digits, because a label names what differs and does not quote it.
    """
    if name.startswith(UNKNOWN_PREFIX):
        return f"unknown axis {name[len(UNKNOWN_PREFIX) :]}={_label_value(value)}"
    axis = AXIS_BY_NAME.get(name)
    if axis is None:
        return _label_value(value)
    return axis.label(value)


def axis_tag(name: str, value: Any) -> str:
    """Return one compare-key value as the path-safe tag value of a file name."""
    axis = AXIS_BY_NAME.get(name)
    return _tag_value(value) if axis is None else axis.tag(value)


__all__ = [
    "AXES",
    "AXIS_BY_NAME",
    "AXIS_NAMES",
    "COMPARE_ROLES",
    "DERIVED",
    "FIXED",
    "MODEL",
    "SAMPLING_PREFIX",
    "UNKNOWN_PREFIX",
    "Axis",
    "axis_label",
    "axis_tag",
    "axis_values",
    "digest8",
    "model_slug",
    "path_safe",
    "plain",
]
