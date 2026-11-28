"""Unit tests for the three command lines: ``evaluate``, ``store`` and ``report``.

One block per tool.  A gate finds a model at the temperature its maker recommends, a comparison
keeps every temperature it was asked to compare, the newest series of a model is chosen and printed
unless the command names one, and a command refuses a combination of options before it prints
anything.  ``validate`` says what it did with its exit code: ``0`` passed, ``1`` refused, ``2``
nothing to check.

Nothing here reads the real store: the records and the runs are built in the test, so a failure
names the rule that broke and not a file on somebody's disk.
"""

from __future__ import annotations

import json

from pathlib import Path
import sys

import pytest

from tools import evaluate, prepare_gec_testset, report, store, verify_model_catalog
from tools.evaluation.core import paths, protocol_table
from tools.evaluation.core.hashing import sha256_text
from tools.evaluation.core.labels import ReferenceLabel
from tools.evaluation.core.manifest import Manifest, ManifestItem
from tools.evaluation.core.records import (
    Answer,
    Environment,
    Input,
    Protocol,
    RunFile,
    RunHeader,
    RunSettings,
    Session,
    TestRecord,
    key_sha256,
    question_sha256,
)
from teachers_teammate.domain.proofreading.word_diff import text_offsets
from teachers_teammate.domain.proofreading.edit_agreement import ordered_edits
from tools.evaluation.core.modes import Exchange, default_steps
from tools.evaluation.run.defaults import temperature_for
from tools.evaluation.run.protocol import instructions_sha256
from tools.evaluation.store import layout, writer

SOURCE = "Er geht heute nach Hause."
REFERENCE = "Er geht heute heim."
TESTSET = "gec-2026-12"
MODEL = "gemma4:12b"
FINGERPRINT = protocol_table.fingerprints_of(protocol_table.current_version())[0]


def _record() -> TestRecord:
    """Return one test record of one error text."""
    offsets = text_offsets(SOURCE)
    start, end, replacement = ordered_edits(SOURCE, REFERENCE)[0]
    return TestRecord(
        id="de-01",
        language="de",
        source=SOURCE,
        kind="errors",
        reference=REFERENCE,
        labels=(
            ReferenceLabel(
                start=offsets[start],
                end=offsets[end],
                replacement="".join(replacement),
                code="R:VERB:FORM",
            ),
        ),
        sentence_spans=((0, len(SOURCE)),),
        origin="a test",
        band="page",
        corpus="falko-merlin",
    )


def _header(
    record: TestRecord,
    *,
    temperature: float = 0.0,
    machine: str = "rx9070xt-server",
    repeat: int = 1,
    prompt: str = "standard",
    instructions: str = "",
) -> RunHeader:
    """Return the header of a run of *record*, at one temperature, on one machine, with one prompt.

    *instructions* is the hash of what was sent in German; empty records none.
    """
    return RunHeader(
        input=Input(
            testset=TESTSET,
            question_sha256=question_sha256([record]),
            texts=1,
            languages=("de",),
        ),
        protocol=Protocol(
            model=MODEL,
            model_digest="a" * 64,
            fingerprint=FINGERPRINT,
            temperature=temperature,
            step_one_prompt=prompt,
            instructions=(("de", instructions),) if instructions else (),
        ),
        run=RunSettings(seed=20261109, repeat=repeat),
        environment=Environment(machine=machine, ollama_version="0.40.16"),
    )


def _run(
    record: TestRecord,
    *,
    date: str = "2026-11-14T23:22:00+00:00",
    output: str = REFERENCE,
    **header,
) -> RunFile:
    """Return a run of *record* that answers *output*, which is the reference unless asked."""
    return RunFile(
        header=_header(record, **header),
        sessions=(Session(date=date, tool_revision="ffa440f"),),
        answers=(
            Answer(
                text=f"{record.language}/{record.id}",
                text_sha256=record.text_sha256,
                output=output,
            ),
        ),
    )


def _manifest(record: TestRecord, *, question: str = "") -> Manifest:
    """Return a manifest that pins *record*, or one that pins another question."""
    return Manifest(
        testset=TESTSET,
        built="2026-11-14T23:22:00+00:00",
        selection_seed=20261109,
        builder_revision="ffa440f",
        question_sha256=question or question_sha256([record]),
        key_sha256=key_sha256([record]),
        items=(
            ManifestItem(
                id=record.id,
                language=record.language,
                kind=record.kind,
                corpus=record.corpus,
                band=record.band,
                pointer={},
                source={
                    "sha256": sha256_text(record.source),
                    "words": 1,
                    "chars": len(record.source),
                },
                reference={"sha256": "0" * 64},
            ),
        ),
    )


def _store(root: Path, *runs: RunFile, question: str = "") -> Path:
    """Write one test set and *runs* into a store at *root* and return the root."""
    record = _record()
    writer.write_testset(root, TESTSET, (record,), _manifest(record, question=question))
    for number, run in enumerate(runs):
        writer.write_run(layout.run_path(root, f"run-{number}.run.jsonl"), run)
    return root


# ── report: the temperature a gate reads ─────────────────────────────────────


def test_a_gate_finds_a_model_at_its_own_temperature(tmp_path: Path, capsys) -> None:
    """Given  a run at the temperature its model's maker recommends, which is not 0.0
    When   the gate is asked for that model without a temperature
    Then   it finds the run and judges it, instead of refusing that no run matches
    """
    own = temperature_for(MODEL)
    assert own != 0.0
    store_root = _store(tmp_path / "store", _run(_record(), temperature=own))

    code = report.main(["gate", "--model", MODEL, "--store", str(store_root)])

    printed = capsys.readouterr()
    assert code in {0, 1}
    assert f"# Gate 2: `{MODEL}` at temperature {own:g}" in printed.out


def test_a_comparison_keeps_every_temperature(tmp_path: Path, capsys) -> None:
    """Given  two runs of one model at two temperatures
    When   they are compared along the temperature
    Then   both are read, because a comparison does not filter on a temperature it did not name
    """
    record = _record()
    store_root = _store(
        tmp_path / "store",
        _run(record, temperature=0.2),
        _run(record, temperature=0.7),
    )

    code = report.main(
        ["compare", "--model", MODEL, "--store", str(store_root), "--along", "temperature"]
    )

    printed = capsys.readouterr()
    assert code == 0
    assert "0.2" in printed.out
    assert "0.7" in printed.out


# ── report: the series a command reads ───────────────────────────────────────


def test_the_newest_series_is_chosen_and_printed(tmp_path: Path, capsys) -> None:
    """Given  two runs of one model on two machines, the second one newer
    When   the gate is asked for the model without a machine
    Then   it reads the newer machine and prints what it chose, with the other series
    """
    record = _record()
    own = temperature_for(MODEL)
    store_root = _store(
        tmp_path / "store",
        _run(record, machine="rx9070xt", temperature=own, date="2026-11-10T10:00:00+00:00"),
        _run(record, machine="rx9070xt-server", temperature=own, date="2026-11-16T10:00:00+00:00"),
    )

    report.main(["gate", "--model", MODEL, "--store", str(store_root)])

    printed = capsys.readouterr()
    assert "chose machine rx9070xt-server, set gec-2026-12 (newest, 2026-11-16)" in printed.err
    assert "also stored: rx9070xt/gec-2026-12" in printed.err


def test_a_named_machine_overrides_the_choice(tmp_path: Path, capsys) -> None:
    """Given  two runs of one model on two machines, the second one newer
    When   the gate is asked for the older machine
    Then   it reads that one, and the set it still had to choose is printed
    """
    record = _record()
    own = temperature_for(MODEL)
    store_root = _store(
        tmp_path / "store",
        _run(record, machine="rx9070xt", temperature=own, date="2026-11-10T10:00:00+00:00"),
        _run(record, machine="rx9070xt-server", temperature=own, date="2026-11-16T10:00:00+00:00"),
    )

    code = report.main(
        ["gate", "--model", MODEL, "--store", str(store_root), "--machine", "rx9070xt"]
    )

    printed = capsys.readouterr()
    assert code in {0, 1}
    assert "chose machine rx9070xt, set gec-2026-12" in printed.err


# ── report: the group a gate reads ───────────────────────────────────────────


def _today(prompt: str = "") -> str:
    """Return the hash of the German instructions today's code sends with step-① *prompt*."""
    return instructions_sha256(
        ["de"],
        exchange=Exchange.TEXT_FIRST,
        steps=default_steps(Exchange.TEXT_FIRST),
        prompt=prompt,
    )


def test_a_gate_reads_the_runs_asked_with_todays_instructions(tmp_path: Path, capsys) -> None:
    """Given  two groups of one model that differ only in the instructions: today's and an older text
    When   the gate is asked for the model
    Then   it judges the group asked as the app asks today, and says it left the other out
    """
    record = _record()
    own = temperature_for(MODEL)
    store_root = _store(
        tmp_path / "store",
        _run(record, temperature=own, instructions="0" * 64),
        _run(record, temperature=own, instructions=_today()),
    )

    code = report.main(["gate", "--model", MODEL, "--store", str(store_root)])

    printed = capsys.readouterr()
    assert code in {0, 1}
    assert "left out 1 group(s) asked with older ones" in printed.err
    assert f"# Gate 2: `{MODEL}`" in printed.out


def test_a_gate_refuses_two_groups_and_names_what_differs(tmp_path: Path, capsys) -> None:
    """Given  two groups of one model, both asked with instructions today's code no longer sends
    When   the gate is asked for the model
    Then   it refuses, and names the axis the groups differ in
    """
    record = _record()
    own = temperature_for(MODEL)
    store_root = _store(
        tmp_path / "store",
        _run(record, temperature=own, instructions="0" * 64),
        _run(record, temperature=own, instructions="1" * 64),
    )

    code = report.main(["gate", "--model", MODEL, "--store", str(store_root)])

    assert code == 2
    assert "they differ in instructions" in capsys.readouterr().err


def test_a_gate_reads_the_prompt_variant_it_is_given(tmp_path: Path, capsys) -> None:
    """Given  a group of one model with the standard step-① prompt and one with a variant
    When   the gate is asked without a prompt, and then for the variant
    Then   it reads the model's own prompt first and the variant when named, never refusing
    """
    record = _record()
    own = temperature_for(MODEL)
    store_root = _store(
        tmp_path / "store",
        _run(record, temperature=own, instructions=_today()),
        _run(record, temperature=own, prompt="find", instructions=_today("find")),
    )

    standard = report.main(["gate", "--model", MODEL, "--store", str(store_root)])
    variant = report.main(
        ["gate", "--model", MODEL, "--store", str(store_root), "--prompt", "find"]
    )

    assert {standard, variant} <= {0, 1}
    assert "refused" not in capsys.readouterr().err


# ── report: the options a command refuses ────────────────────────────────────


def test_a_table_of_false_alarms_classifies_them(tmp_path: Path, capsys) -> None:
    """Given  a run that changes a mark the reference leaves alone
    When   the table is asked for the false alarms
    Then   it prints the table, with that change counted as one the reference does not make
    """
    record = _record()
    store_root = _store(tmp_path / "store", _run(record, output="Er geht heute nach Hause!"))

    code = report.main(["table", "--store", str(store_root), "--false-alarms"])

    printed = capsys.readouterr()
    assert code == 0
    assert "false alarms per error text" in printed.out
    assert f"| `{MODEL}` | 1 |" in printed.out


def test_a_command_without_a_store_is_refused_by_the_parser(capsys) -> None:
    """Given  no store on the command line
    When   a report, the builder or the size measurement is parsed
    Then   the parser refuses it and names --store, because no command guesses where the records are
    """
    for parse in (
        lambda: report.main(["gate", "--model", MODEL]),
        lambda: report.main(["compare", "--along", "model"]),
        lambda: report.main(["passes"]),
        lambda: report.main(["table"]),
        lambda: store.main(["validate"]),
        lambda: evaluate.main(["run", "--testset", "gec-2026-12", "--model", MODEL]),
        lambda: verify_model_catalog.main(["fit", "--report"]),
    ):
        with pytest.raises(SystemExit) as refused:
            parse()
        assert refused.value.code == 2
        assert "--store" in capsys.readouterr().err


def test_a_store_inside_the_app_repository_is_refused_by_the_parser(monkeypatch, capsys) -> None:
    """Given  a store inside the app repository
    When   a command that writes or reads records is parsed
    Then   the parser refuses it, for the report, the builder and the size measurements alike
    """
    inside = str(paths.repository_root() / "store")
    monkeypatch.setattr(sys, "argv", ["prepare", "--testset", "gec-2026-12", "--store", inside])

    for parse in (
        lambda: report.main(["table", "--store", inside]),
        prepare_gec_testset.main,
        lambda: verify_model_catalog.main(["fit", "--store", inside]),
        lambda: verify_model_catalog.main(["structured-output", "--fit-store", inside]),
    ):
        with pytest.raises(SystemExit) as refused:
            parse()
        assert refused.value.code == 2
        assert "inside the app repository" in capsys.readouterr().err


# ── store: what validate says with its exit code ─────────────────────────────


def test_a_mistyped_store_is_nothing_to_check(tmp_path: Path, capsys) -> None:
    """Given  a path that holds no store
    When   it is validated
    Then   the exit code says there was nothing to check, and not that everything passed
    """
    code = store.main(["validate", "--store", str(tmp_path / "typo")])

    printed = capsys.readouterr()
    assert code == 2
    assert "nothing to check in" in printed.err


def test_a_refused_file_exits_one(tmp_path: Path, capsys) -> None:
    """Given  a store whose manifest pins another question than its records
    When   it is validated
    Then   the exit code is one, however many files were refused
    """
    store_root = _store(tmp_path / "store", _run(_record()), question="e" * 64)

    code = store.main(["validate", "--store", str(store_root)])

    printed = capsys.readouterr()
    assert code == 1
    assert "refused" in printed.err


def test_a_clean_store_exits_zero(tmp_path: Path, capsys) -> None:
    """Given  a store of one set and one run
    When   it is validated
    Then   the exit code is zero
    """
    store_root = _store(tmp_path / "store", _run(_record()))

    code = store.main(["validate", "--store", str(store_root)])

    printed = capsys.readouterr()
    assert code == 0, printed.err


# ── evaluate: the options a run refuses ──────────────────────────────────────


def test_a_run_without_a_machine_is_refused_by_the_parser(tmp_path: Path, capsys) -> None:
    """Given  a store and a model
    When   a run is asked for without naming the machine it runs on
    Then   the parser refuses it, because a report joins runs and size measurements by that name
    """
    store_root = _store(tmp_path / "store", _run(_record()))

    with pytest.raises(SystemExit) as refused:
        evaluate.main(["run", "--store", str(store_root), "--testset", TESTSET, "--model", MODEL])

    printed = capsys.readouterr()
    assert refused.value.code == 2
    assert printed.out == ""
    assert "--machine" in printed.err


def _registry(root: Path) -> None:
    """Write a card machine and the same box without its card into the store's registry."""
    card = {
        "format": 1,
        "id": "box",
        "gpu": {"vendor": "amd", "name": "AMD Radeon", "vram_mb": 16304},
        "cpu": "Intel Core i5",
        "ram_gib": 31,
        "os": "Ubuntu 24",
        "accelerator_stack": "ROCm",
    }
    processor = {**card, "id": "box-cpu", "gpu": {"vendor": "none", "name": "none", "vram_mb": 0}}
    (root / "machines").mkdir(parents=True, exist_ok=True)
    for machine in (card, processor):
        (root / "machines" / f"{machine['id']}.json").write_text(
            json.dumps(machine), encoding="utf-8"
        )


@pytest.mark.parametrize(
    ("machine", "flags", "said"),
    [
        ("box-cpu", [], "run it with --cpu-only"),
        ("box", ["--cpu-only"], "has a card"),
    ],
)
def test_a_run_whose_device_does_not_fit_its_machine_is_refused(
    tmp_path: Path, capsys, machine: str, flags: list[str], said: str
) -> None:
    """Given  a registry with a card machine and the same box without its card
    When   a run names the box without a card and not --cpu-only, or the card machine and --cpu-only
    Then   it refuses before the first request, so a page time never lands under the other id
    """
    store_root = _store(tmp_path / "store", _run(_record()))
    _registry(store_root)

    code = evaluate.main(
        [
            "run",
            "--store",
            str(store_root),
            "--testset",
            TESTSET,
            "--model",
            MODEL,
            "--machine",
            machine,
            *flags,
        ]
    )

    printed = capsys.readouterr()
    assert code == 2
    assert said in printed.err


def test_an_option_for_another_exchange_is_refused_before_anything_is_sent(
    tmp_path: Path, capsys
) -> None:
    """Given  a store and a one-request exchange, which sends no sampling value
    When   a run is asked for with a sampling option
    Then   it refuses before the first request, and says which exchange the option belongs to
    """
    store_root = _store(tmp_path / "store", _run(_record()))

    code = evaluate.main(
        [
            "run",
            "--store",
            str(store_root),
            "--testset",
            TESTSET,
            "--model",
            MODEL,
            "--machine",
            "rx9070xt-server",
            "--exchange",
            "one-request-text",
            "--option",
            "top_p=0.9",
        ]
    )

    printed = capsys.readouterr()
    assert code == 2
    assert printed.out == ""
    assert "--option reaches the proofreading exchange only" in printed.err
