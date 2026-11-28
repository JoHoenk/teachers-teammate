"""The command line: what it refuses before reading, and what a store of runs reads back as."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from teachers_teammate.exceptions import ModelMissingError
from teachers_teammate.messages import Message, MessageKey, models_not_downloaded
from tools.ocr_eval import cli, pages, records, runs, runner
from tools.ocr_eval.engine import Answer, Ask, numbers_of


class _AnswersEngine:
    """An engine that answers every page with the same sentence."""

    engine = "ppocr"
    model = "reader"
    settings = None

    def ask(self, language: str) -> Ask:  # the reader reads every language alike
        """Return what this engine is asked: the reader's, with no prompt."""
        return Ask(
            engine=self.engine,
            model=self.model,
            digest="reader@1",
            prompt="",
            detector=numbers_of(self.settings) if self.settings is not None else (),
        )

    def warm_up(self) -> None:
        """Nothing to load."""

    def read(self, image: Path, language: str) -> Answer:  # one answer per page
        """Answer the page."""
        return Answer(text="Hallo Welt", seconds=0.5, memory_mb=128)


class _MissingEngine:
    """An engine whose model is not on the machine: it cannot even warm up."""

    engine = "ppocr"
    model = "reader"

    def ask(self, language: str) -> Ask:  # the reader reads every language alike
        """Return what this engine is asked; the model files are missing either way."""
        return Ask(engine=self.engine, model=self.model, digest="reader@1", prompt="")

    def warm_up(self) -> None:
        """Raise the way the reader does without its model files."""
        raise ModelMissingError(models_not_downloaded("paddle"))

    def read(self, image: Path, language: str) -> Answer:  # never reached
        """Never reached: the warm-up refuses the run first."""
        raise AssertionError("a run whose model is missing reads no page")


def _pages(root: Path) -> Path:
    """Write a page set of one page with its metadata, and return the set's root."""
    group = root / "handwritten_scan"
    group.mkdir(parents=True)
    (group / "s01.png").write_bytes(b"page")
    (group / f"s01{pages.REFERENCE_SUFFIX}").write_text("Hallo Welt", encoding="utf-8")
    (root / pages.METADATA_NAME).write_text(
        '{"id": "s01", "writer": "w1", "language": "de"}\n',
        encoding="utf-8",
    )
    return root


def _store(root: Path, *, vendor: str = "amd") -> Path:
    """Write a store of evaluation records holding one machine, and return its root."""
    machines = root / "machines"
    machines.mkdir(parents=True)
    (machines / "rx9070xt-server.json").write_text(
        json.dumps(
            {
                "format": 1,
                "id": "rx9070xt-server",
                "gpu": {"vendor": vendor, "name": "Radeon RX 9070 XT", "vram_mb": 16304},
                "cpu": "Ryzen 9",
                "ram_gib": 64,
                "os": "Linux",
                "accelerator_stack": "rocm 7",
            }
        ),
        encoding="utf-8",
    )
    return root


def _record(
    model: str, *, preset: str = "R", cer: float = 0.1, set_version: str = "abc123def"
) -> records.RunRecord:
    """Return a run of one page of the page set *set_version*, read at *cer*."""
    return records.RunRecord(
        header=records.Header(
            model=model,
            engine="ollama",
            prompt="app",
            preset=preset,
            set_version=set_version,
            memory_mb=6144,
            licence="Apache-2.0",
        ),
        pages=(
            records.PageNumbers(
                page="s01",
                group="handwritten_scan",
                split="tune",
                cer=cer,
                wer=cer,
                bag_wer=cer,
                kept=0.5,
                words=10,
                characters=50,
                seconds=2.0,
            ),
        ),
    )


def _write(store: Path, record: records.RunRecord) -> str:
    """Write *record* into the store and return the run's name."""
    name = runs.run_name(record.header)
    runs.write(runs.run_path(store, name), record)
    return name


def test_a_preset_nobody_has_is_refused_before_anything_is_read(tmp_path: Path, capsys) -> None:
    """Given a command naming a preset the tool does not have
    When   it is parsed
    Then   the parse fails with the refusal code, so no page is prepared under it
    """
    with pytest.raises(SystemExit) as caught:
        cli.main(
            [
                "run",
                "--store",
                str(tmp_path),
                "--pages",
                str(tmp_path),
                "--model",
                "reader",
                "--machine",
                "rx9070xt-server",
                "--preset",
                "R-despeckle",
            ]
        )

    assert caught.value.code == 2
    assert "despeckle" in capsys.readouterr().err


def test_a_machine_the_registry_does_not_hold_is_refused(tmp_path: Path) -> None:
    """Given a store whose registry names one machine
    When   a run names another one
    Then   the run is refused, naming the machines the registry holds
    """
    store = _store(tmp_path / "evals")

    code = cli.main(
        [
            "run",
            "--store",
            str(store),
            "--pages",
            str(tmp_path),
            "--model",
            "reader",
            "--machine",
            "laptop",
        ]
    )

    assert code == 2


def test_a_card_without_a_device_is_refused(tmp_path: Path) -> None:
    """Given a machine id of a box without a graphics card
    When   a run asks for the card
    Then   it is refused: that id measures the processor
    """
    store = _store(tmp_path / "evals", vendor="none")

    code = cli.main(
        [
            "run",
            "--store",
            str(store),
            "--pages",
            str(tmp_path),
            "--model",
            "reader",
            "--machine",
            "rx9070xt-server",
        ]
    )

    assert code == 2


def test_a_detector_number_for_a_model_is_refused(tmp_path: Path) -> None:
    """Given a run of a model with a detector override
    When   the command line is read
    Then   it is refused: the detector is the reader's, a model has none
    """
    store = _store(tmp_path / "evals")

    code = cli.main(
        [
            "run",
            "--store",
            str(store),
            "--pages",
            str(tmp_path),
            "--model",
            "qwen3-vl:8b",
            "--machine",
            "rx9070xt-server",
            "--detector",
            "max_side=960",
        ]
    )

    assert code == 2


def test_the_reader_is_not_asked_with_a_card_prompt(tmp_path: Path) -> None:
    """Given the built-in reader and the card prompt
    When   the command line is read
    Then   it is refused: the reader has no card
    """
    store = _store(tmp_path / "evals")

    code = cli.main(
        [
            "run",
            "--store",
            str(store),
            "--pages",
            str(tmp_path),
            "--model",
            "reader",
            "--machine",
            "rx9070xt-server",
            "--prompt",
            "card",
        ]
    )

    assert code == 2


def test_a_model_the_machine_has_not_downloaded_ends_the_run(
    tmp_path: Path, capsys, monkeypatch
) -> None:
    """Given a run whose engine cannot load its model at all
    When   the command is run
    Then   it refuses with the message's key on stderr, and writes no record
    """
    store = _store(tmp_path / "evals")
    monkeypatch.setattr(runner, "engine_of", lambda plan, *, store=None: _MissingEngine())

    code = cli.main(
        [
            "run",
            "--store",
            str(store),
            "--pages",
            str(_pages(tmp_path / "pages")),
            "--model",
            "reader",
            "--preset",
            "raw",
            "--cache",
            str(tmp_path / "cache"),
            "--machine",
            "rx9070xt-server",
        ]
    )

    assert code == 2
    assert str(MessageKey.MODELS_NOT_DOWNLOADED) in capsys.readouterr().err
    assert runs.run_files(store) == []


def test_the_report_puts_two_runs_side_by_side(tmp_path: Path, capsys) -> None:
    """Given a store with two runs, one of them chosen as the baseline
    When   report is asked for it
    Then   both rows are printed, the baseline's difference to itself being none
    """
    store = _store(tmp_path / "evals")
    baseline = _write(store, _record("chandra-ocr-2", cer=0.1))
    _write(store, _record("qwen3-vl:8b", cer=0.05))

    code = cli.main(["report", "--store", str(store), "--baseline", baseline])
    printed = capsys.readouterr().out

    assert code == 0
    assert "chandra-ocr-2" in printed
    assert "qwen3-vl:8b" in printed
    assert "+5.0" in printed


def test_a_baseline_nobody_has_is_refused(tmp_path: Path) -> None:
    """Given a run name no file in the store holds
    When   the report is asked for it
    Then   it is refused, naming the runs the store does hold
    """
    store = _store(tmp_path / "evals")
    name = _write(store, _record("chandra-ocr-2"))

    code = cli.main(["report", "--store", str(store), "--baseline", "nobody"])

    assert code == 2
    assert name


def test_validate_names_the_record_that_holds_a_sentence(tmp_path: Path, capsys) -> None:
    """Given a stored run whose pages entry holds words instead of numbers
    When   validate is asked for the store
    Then   it names the file and returns one
    """
    store = _store(tmp_path / "evals")
    name = _write(store, _record("chandra-ocr-2"))
    path = runs.run_path(store, name)
    stored = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    stored["pages"][0]["cer"] = "read well enough"
    path.write_text(json.dumps(stored) + "\n", encoding="utf-8")

    code = cli.main(["validate", "--store", str(store)])
    printed = capsys.readouterr().err

    assert code == 1
    assert path.name in printed


def test_validate_passes_a_store_of_numbers(tmp_path: Path, capsys) -> None:
    """Given a store of one well-formed run
    When   validate is asked for it
    Then   it says every record holds numbers and names only, and returns nought
    """
    store = _store(tmp_path / "evals")
    _write(store, _record("chandra-ocr-2"))

    code = cli.main(["validate", "--store", str(store)])
    printed = capsys.readouterr().out

    assert code == 0
    assert "1 of 1" in printed


def test_a_second_run_of_one_name_is_refused_until_replace_is_asked(
    tmp_path: Path, capsys, monkeypatch
) -> None:
    """Given a store that already holds this command's run
    When   the same command is run again, and then with --replace
    Then   the second is refused with exit 2, and only --replace writes over the first
    """
    store = _store(tmp_path / "evals")
    monkeypatch.setattr(runner, "engine_of", lambda plan, *, store=None: _AnswersEngine())
    command = [
        "run",
        "--store",
        str(store),
        "--pages",
        str(_pages(tmp_path / "pages")),
        "--model",
        "reader",
        "--preset",
        "raw",
        "--cache",
        str(tmp_path / "cache"),
        "--machine",
        "rx9070xt-server",
    ]

    assert cli.main(command) == 0
    assert cli.main(command) == 2
    assert "--replace" in capsys.readouterr().err
    assert cli.main([*command, "--replace"]) == 0
    assert len(runs.run_files(store)) == 1


def test_a_baseline_name_two_folders_hold_is_refused(tmp_path: Path, capsys) -> None:
    """Given the same run name in two campaign folders
    When   the report is asked for it by its bare name
    Then   it is refused, naming both folders
    """
    store = _store(tmp_path / "evals")
    record = _record("chandra-ocr-2")
    name = runs.run_name(record.header)
    runs.write(runs.run_path(store, name, folder="c1"), record)
    runs.write(runs.run_path(store, name, folder="c2"), record)

    code = cli.main(["report", "--store", str(store), "--baseline", name])
    err = capsys.readouterr().err

    assert code == 2
    assert f"c1/{name}" in err
    assert f"c2/{name}" in err


def test_a_report_holds_one_page_set(tmp_path: Path, capsys) -> None:
    """Given runs of two page sets, one of them chosen as the baseline
    When   the report is asked for it
    Then   only the baseline's set is in the table, and the other run is named on stderr
    """
    store = _store(tmp_path / "evals")
    baseline = _write(store, _record("chandra-ocr-2", set_version="aaaaaa1"))
    other = _write(store, _record("qwen3-vl:8b", set_version="bbbbbb2"))

    code = cli.main(["report", "--store", str(store), "--baseline", baseline])
    captured = capsys.readouterr()

    assert code == 0
    assert "qwen3-vl:8b" not in captured.out
    assert f"{other}: another page set (bbbbbb), left out" in captured.err


def test_a_report_of_two_page_sets_without_a_baseline_is_refused(tmp_path: Path, capsys) -> None:
    """Given runs of two page sets and no baseline to choose by
    When   the report is asked for the store
    Then   it is refused, naming both versions and how many runs hold each
    """
    store = _store(tmp_path / "evals")
    _write(store, _record("chandra-ocr-2", set_version="aaaaaa1"))
    _write(store, _record("qwen3-vl:8b", set_version="bbbbbb2"))
    _write(store, _record("glm-ocr:q8_0", set_version="bbbbbb2"))

    code = cli.main(["report", "--store", str(store)])
    err = capsys.readouterr().err

    assert code == 2
    assert "aaaaaa (1 run(s))" in err
    assert "bbbbbb (2 run(s))" in err
