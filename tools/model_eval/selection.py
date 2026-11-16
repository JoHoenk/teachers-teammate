"""Which committed runs a gate-2 command reads: the selection of one or two groups.

A gate-2 command names a model, not a file: the runs of that model are found below the repository
directory, and the parts of the key the command does not name are taken from the newest run of the
model — its test set, its machine, its window.  The newest run is the newest one that matches the
mode, the temperature and the eval version the command asks for, so an older run of another mode
cannot decide which test set is read.  A comparison takes the test set and the machine from the
model it judges, not from the one it compares against, so the two groups are always the same
evaluation.  What is left ambiguous is refused rather than guessed: two digests of one tag, or two
request profiles, are two models or two requests, and the command asks for ``--digest`` or
``--compact-json`` instead of picking one.

This module reads and checks the committed files; :mod:`.grouping` forms the groups and
:mod:`.gate_two` judges them.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from teachers_teammate.domain.model_catalog import normalize_tag

from . import grouping, layout, protocol, run_format, validation
from .answer_mode import AnswerMode
from .cache_groups import cache_groups
from .testset import read_manifest
from .viewer import load


@dataclass(frozen=True)
class Selection:
    """What a gate-2 command asks for: one model, or two, and how to tell their runs apart."""

    model: str
    against: str | None = None
    mode: AnswerMode = AnswerMode.REVIEW
    temperature: float | None = None
    """The temperature of the runs; ``None`` takes each model's own recommended one."""
    machine: str | None = None
    """The machine whose runs are read, or the provider of an online service (what the index shows
    in its machine column); ``None`` takes the newest run's."""
    testset: str | None = None
    """The test set whose runs are read; ``None`` takes the newest run's."""
    digest: str | None = None
    """The digest of the model, when two digests of one tag are committed."""
    compact_json: bool | None = None
    """The compact-JSON rule of the request profile, when two profiles are committed."""
    eval_version: int | None = None
    """The eval version; ``None`` takes the current one, because older runs measured another
    request."""
    against_temperature: float | None = None
    """The temperature of the runs of ``against``; ``None`` applies the rule of ``temperature`` to
    it too, so a refinement of one model at two temperatures names both."""
    against_mode: AnswerMode | None = None
    """The answer mode of the runs of ``against``; ``None`` takes ``mode``."""

    def temperature_of(self, model: str) -> float:
        """Return the temperature the runs of *model* are read at.

        An explicit ``--temperature`` is one value for every model named.  Without it each model is
        read at the temperature the app sends it — its maker's recommendation, else 0.2 — so two
        models are compared at their own starting points, and a refinement is a second group of
        the same model (:func:`~.defaults.temperature_for`).
        """
        from . import defaults  # noqa: PLC0415

        return defaults.temperature_for(model, self.temperature)

    def sides(self) -> tuple[tuple[str, float, AnswerMode], ...]:
        """Return the model, temperature and mode of each side, the one to judge first.

        The second side takes its own temperature and mode when the command names them, and the
        first side's otherwise: a comparison of two models at their own starting points names
        neither, and a refinement of one model at two temperatures names both.
        """
        first = (self.model, self.temperature_of(self.model), self.mode)
        if self.against is None:
            return (first,)
        temperature = (
            self.temperature_of(self.against)
            if self.against_temperature is None
            else self.against_temperature
        )
        return (first, (self.against, temperature, self.against_mode or self.mode))


def select(out: Path, wanted: Selection) -> list[grouping.RunGroup]:
    """Return one group per model *wanted* names, in the order it names them.

    The test set and the machine are resolved once, from the newest run of the model the command
    judges, and every model is read on them: a comparison of two models is a comparison of one
    evaluation, so ``--against`` cannot bring its own test set or machine.

    Args:
        out: The repository directory that holds ``runs/`` and ``testsets/``.
        wanted: The model or models, and the parts of the key the command fixed.

    Raises:
        ValueError: if a run below *out* is refused, no run matches, or the runs of one model
            differ in a part the command did not name.
    """
    runs = _read(out)
    if not runs:
        raise ValueError(f"no run files below {out}")
    version = wanted.eval_version if wanted.eval_version is not None else _current_version()
    chosen: list[grouping.RunGroup] = []
    anchor: tuple[str, str] | None = None
    for model, temperature, mode in wanted.sides():
        mine = [run for run in runs if _tag(run) == normalize_tag(model)]
        if not mine:
            raise ValueError(f"no run of {model!r} below {out}")
        if anchor is None:
            anchor = _anchor(mine, wanted, version, model, temperature, mode)
        chosen.extend(
            _groups(
                out,
                _narrow(mine, wanted, version, anchor, model, temperature, mode),
                model,
            )
        )
    return chosen


def select_cache(caches: Sequence[load.Cache], wanted: Selection) -> list[grouping.RunGroup]:
    """Return one group per side *wanted* names, from the local result folders *caches*.

    The same narrowing as :func:`select`, on the runs a folder holds instead of on committed files:
    the test set is the one the folders were read with, so ``wanted.testset`` is not read, and a
    side that leaves two groups (two digests, or two request profiles) is refused rather than
    guessed, as it is for committed runs.

    Args:
        caches: The folders, already read against one test set.
        wanted: The model or models, and the parts of the key the command fixed.

    Raises:
        ValueError: if no run matches, or the runs of one side make more than one group.
    """
    found = [group for group in cache_groups(caches) if _cache_wanted(group.key, wanted)]
    chosen: list[grouping.RunGroup] = []
    for model, temperature, mode in wanted.sides():
        mine = [
            group
            for group in found
            if normalize_tag(group.key.model) == normalize_tag(model)
            and group.key.temperature == temperature
            and group.key.mode is mode
        ]
        if not mine:
            raise ValueError(
                f"no run of {model!r} at temperature {temperature:g}, {mode.value} mode"
            )
        if len(mine) > 1:
            raise ValueError(
                f"{model!r}: the folders hold {len(mine)} groups at temperature "
                f"{temperature:g}, {mode.value} mode (they differ in the digest or the request "
                "profile); keep one folder per set of sampling values"
            )
        chosen.append(mine[0])
    return chosen


def _cache_wanted(key: grouping.GroupKey, wanted: Selection) -> bool:
    """Whether a cache group's *key* agrees with the parts of the key the command fixed."""
    if wanted.digest is not None and not key.digest.removeprefix("sha256:").startswith(
        wanted.digest
    ):
        return False
    if wanted.machine is not None and wanted.machine not in (key.machine, key.provider):
        return False
    if wanted.compact_json is not None:
        profile = dict(key.request_profile)
        if bool(profile.get("compact_json")) != wanted.compact_json:
            return False
    return True


def _read(out: Path) -> list[run_format.StoredRun]:
    """Return every committed run below *out*, refusing one that fails format mode.

    Raises:
        ValueError: if a run file is refused, naming the file and the first problem.
    """
    runs = []
    for path in layout.run_files(out):
        problems = validation.validate_run(path)
        if problems:
            raise ValueError(f"{path.name} is refused: {problems[0]}")
        runs.append(run_format.read_run(path))
    return runs


def _current_version() -> int:
    """Return the eval version of the code, which is the one a new run would carry."""
    return protocol.current_stamp().eval_version


def _tag(run: run_format.StoredRun) -> str:
    """Return the normalised model tag of *run*."""
    return normalize_tag(str(run.header.get("model", "")))


def _anchor(
    runs: Sequence[run_format.StoredRun],
    wanted: Selection,
    version: int,
    model: str,
    temperature: float,
    mode: AnswerMode,
) -> tuple[str, str]:
    """Return the test set and the machine of the newest run that matches *wanted*.

    The mode, the temperature and the eval version are filtered first, so the newest run of another
    mode or of an older request cannot decide which evaluation is read.

    Raises:
        ValueError: if no run of the model matches the mode, the temperature and the version.
    """
    matching = [
        run
        for run in runs
        if run.meta.eval_version == version
        and run.meta.mode is mode
        and run.meta.temperature == temperature
        and (wanted.testset is None or run.meta.testset == wanted.testset)
        and (wanted.machine is None or run.meta.host == wanted.machine)
    ]
    if not matching:
        raise ValueError(
            f"no run of {model!r} at eval version {version}, {mode.value} mode, "
            f"temperature {temperature:g}"
        )
    newest = max(matching, key=lambda run: run.meta.date)
    return (wanted.testset or newest.meta.testset, wanted.machine or newest.meta.host)


def _narrow(
    runs: Sequence[run_format.StoredRun],
    wanted: Selection,
    version: int,
    anchor: tuple[str, str],
    model: str,
    temperature: float,
    mode: AnswerMode,
) -> list[run_format.StoredRun]:
    """Return the runs of one model that match *wanted* on the evaluation *anchor* names.

    Raises:
        ValueError: if nothing matches, or two digests or two profiles remain.
    """
    testset, machine = anchor
    kept = [
        run
        for run in runs
        if str(run.header["testset"]) == testset
        and run.meta.host == machine
        and int(run.header["eval_version"]) == version
        and str(run.header["mode"]) == mode.value
        and float(run.header["temperature"]) == temperature
    ]
    if not kept:
        raise ValueError(
            f"no run of {model!r} on {testset} at {machine}, eval version {version}, "
            f"{mode.value} mode, temperature {temperature:g}"
        )
    kept = _one_digest(kept, wanted)
    return _one_profile(kept, wanted)


def _one_digest(
    runs: Sequence[run_format.StoredRun], wanted: Selection
) -> list[run_format.StoredRun]:
    """Return the runs of one digest, refusing two when the command named none.

    Raises:
        ValueError: if two digests remain and ``--digest`` was not given.
    """
    digests = sorted({_digest(run) for run in runs})
    if wanted.digest is not None:
        kept = [run for run in runs if _digest(run).startswith(wanted.digest)]
        if not kept:
            raise ValueError(f"no run of {wanted.model!r} with digest {wanted.digest!r}")
        return kept
    if len(digests) > 1:
        raise ValueError(
            f"{wanted.model!r} has {len(digests)} digests committed "
            f"({', '.join(digest[:12] for digest in digests)}): give --digest"
        )
    return list(runs)


def _one_profile(
    runs: Sequence[run_format.StoredRun], wanted: Selection
) -> list[run_format.StoredRun]:
    """Return the runs of one request profile, refusing two when the command named none.

    Raises:
        ValueError: if two profiles remain and ``--compact-json`` was not given.
    """
    profiles = sorted({_compact(run) for run in runs})
    if wanted.compact_json is not None:
        kept = [run for run in runs if _compact(run) == wanted.compact_json]
        if not kept:
            raise ValueError(
                f"no run of {wanted.model!r} with compact JSON "
                f"{'on' if wanted.compact_json else 'off'}"
            )
        return kept
    if len(profiles) > 1:
        raise ValueError(
            f"{wanted.model!r} has {len(profiles)} request profiles committed "
            "(compact JSON on and off): give --compact-json"
        )
    return list(runs)


def _digest(run: run_format.StoredRun) -> str:
    """Return the model digest of *run*, without the ``sha256:`` prefix."""
    digest = str((run.header.get("model_info") or {}).get("digest", ""))
    return digest.split(":", 1)[1] if ":" in digest else digest


def _compact(run: run_format.StoredRun) -> bool:
    """Return the compact-JSON rule of *run*'s request profile."""
    return bool((run.header.get("request_profile") or {}).get("compact_json"))


def _groups(out: Path, runs: Iterable[run_format.StoredRun], model: str) -> list[grouping.RunGroup]:
    """Return the one group *runs* make, typed against the manifest of their test set.

    Raises:
        ValueError: if the runs make more than one group, or the manifest is missing.
    """
    members = list(runs)
    testset = str(members[0].header["testset"])
    pin = out / layout.TESTSETS_DIR / testset / "manifest.json"
    if not pin.exists():
        raise ValueError(f"no manifest at {pin}")
    found = grouping.groups(members, read_manifest(pin))
    if len(found) != 1:
        raise ValueError(
            f"{model!r}: the runs make {len(found)} groups (they differ in the machine or "
            "provider, the test set, the temperature or the request profile); "
            "give --machine, --testset or --temperature to choose one"
        )
    return found


__all__ = ["Selection", "select", "select_cache"]
