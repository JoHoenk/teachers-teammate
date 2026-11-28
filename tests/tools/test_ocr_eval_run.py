"""A run of a page set: what it records, what it keeps, and what it refuses."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from teachers_teammate.exceptions import OCRDeadlineError, OCRError, OllamaUnreachableError
from teachers_teammate.infrastructure.ppocr.detect import DetectorSettings
from teachers_teammate.messages import Message, MessageKey, detail, unreachable
from tools.ocr_eval import pages, presets, records, runs, runner
from tools.ocr_eval.engine import Answer, Ask, detector_settings, numbers_of


class FakeEngine:
    """A reading engine that answers what it was told to, and counts its pages.

    An answer of ``None`` is a page the server refused; an exception is raised instead.
    """

    def __init__(
        self,
        *answers: str | BaseException | None,
        engine: str = "ppocr",
        model: str = "reader",
        digest: str = "reader@1",
        settings: DetectorSettings | None = None,
        cpu_only: bool = False,
    ) -> None:
        self.engine = engine
        self.model = model
        self.settings = settings
        self.cpu_only = cpu_only
        self._digest = digest
        self._answers: list[str | BaseException | None] = list(answers) or ["Hallo Welt"]
        self.read_pages: list[Path] = []
        self.languages: list[str] = []
        self.warm_ups = 0

    def ask(self, language: str) -> Ask:
        """Return what this engine is asked, as the reader or a model would say it."""
        return Ask(
            engine=self.engine,
            model=self.model,
            digest=self._digest,
            prompt="" if self.settings is not None else f"Read the page in {language}.",
            detector=numbers_of(self.settings) if self.settings is not None else (),
            cpu_only=self.cpu_only,
        )

    def warm_up(self) -> None:
        """Count the warm-ups a run asked for."""
        self.warm_ups += 1

    def read(self, image: Path, language: str) -> Answer:
        """Return the next answer, and remember the page and its language."""
        self.read_pages.append(image)
        self.languages.append(language)
        text = self._answers[min(len(self.read_pages), len(self._answers)) - 1]
        if isinstance(text, BaseException):
            raise text
        if text is None:
            raise OCRError(Message(MessageKey.MODELS_NOT_DOWNLOADED))
        return Answer(text=text, seconds=0.5, memory_mb=128)


def _set(
    root: Path,
    *,
    writers: int = 2,
    names: tuple[str, ...] = ("s01", "s02"),
    languages: tuple[str, ...] = ("de",),
) -> Path:
    """Write a page set of one group with its metadata, and return the set's root."""
    group = root / "handwritten_scan"
    group.mkdir(parents=True, exist_ok=True)
    rows = []
    for index, name in enumerate(names):
        image = group / f"{name}.png"
        image.write_bytes(f"page {name}".encode())
        image.with_name(f"{name}{pages.REFERENCE_SUFFIX}").write_text(
            "Hallo Welt", encoding="utf-8"
        )
        language = languages[index % len(languages)]
        rows.append(f'{{"id": "{name}", "writer": "w{index % writers}", "language": "{language}"}}')
    (root / pages.METADATA_NAME).write_text("\n".join(rows) + "\n", encoding="utf-8")
    return root


def _plan(
    pages_root: Path,
    store: Path,
    cache: Path,
    *,
    split: str = records.ALL,
    cpu_only: bool = False,
    model: str = "reader",
    timeout: float | None = runner.TIMEOUT,
) -> runner.Plan:
    """Return what one run of *pages_root* is set to, prepared as raw so no step runs."""
    return runner.Plan(
        model=model,
        preset=presets.parse("raw"),
        pages=pages_root,
        store=store,
        machine="rx9070xt-server",
        cache=cache,
        split=split,
        cpu_only=cpu_only,
        timeout=timeout,
    )


def test_a_run_records_one_page_per_page_of_the_set(tmp_path: Path) -> None:
    """Given a set of two pages and an engine that reads them
    When   the run is executed
    Then   the record holds both pages, each with its group, its side and its numbers
    """
    set_root = _set(tmp_path / "pages")
    engine = FakeEngine("Hallo Welt")

    record = runner.execute(_plan(set_root, tmp_path / "store", tmp_path / "cache"), engine=engine)

    assert [page.page for page in record.pages] == ["s01", "s02"]
    assert {page.group for page in record.pages} == {"handwritten_scan"}
    assert {page.split for page in record.pages} == {records.TUNE, records.CHECK}
    assert record.header.pages == 2
    assert record.header.set_version
    assert [page.writer for page in record.pages] == ["w0", "w1"]


def test_a_second_run_reads_no_page_twice(tmp_path: Path) -> None:
    """Given a finished run and the same run again with another engine that would answer otherwise
    When   both are executed
    Then   the second engine is never asked, because the readings are cached
    """
    set_root = _set(tmp_path / "pages")
    store, cache = tmp_path / "store", tmp_path / "cache"
    runner.execute(_plan(set_root, store, cache), engine=FakeEngine("Hallo Welt"))
    second = FakeEngine("Etwas anderes")

    record = runner.execute(_plan(set_root, store, cache), engine=second)

    assert second.read_pages == []
    assert all(page.cer == 0.0 for page in record.pages)


def test_a_page_the_engine_refused_is_recorded_as_an_empty_answer(tmp_path: Path) -> None:
    """Given a set of two pages where the server refused the second
    When   the run is executed
    Then   that page is in the record with the empty flag, and the other one is read
    """
    set_root = _set(tmp_path / "pages")

    record = runner.execute(
        _plan(set_root, tmp_path / "store", tmp_path / "cache"),
        engine=FakeEngine("Hallo Welt", None),
    )

    refused = record.pages[1]
    assert refused.flags == ("empty",)
    assert refused.wer == 1.0
    assert record.pages[0].flags == ()


def test_a_page_the_engine_failed_is_read_again_next_time(tmp_path: Path) -> None:
    """Given a run whose engine failed the second page
    When   the same plan is run again with an engine that reads it
    Then   that page is asked again, and only that one: a failure is not cached
    """
    set_root = _set(tmp_path / "pages")
    store, cache = tmp_path / "store", tmp_path / "cache"
    runner.execute(_plan(set_root, store, cache), engine=FakeEngine("Hallo Welt", None))
    second = FakeEngine("Hallo Welt")

    record = runner.execute(_plan(set_root, store, cache), engine=second)

    assert len(second.read_pages) == 1
    assert all(page.flags == () for page in record.pages)


def test_a_page_the_deadline_ended_is_cut_off_timed_and_kept(tmp_path: Path) -> None:
    """Given a model that is still writing the second page when the run's deadline passes
    When   the run is made, and then made again with the same deadline
    Then   the page is empty and cut off, it carries the time it was given, and the second run
           does not wait for it again
    """
    set_root = _set(tmp_path / "pages")
    store, cache = tmp_path / "store", tmp_path / "cache"
    looping = OCRDeadlineError("the answer was still being written after 900 s")

    first = runner.execute(_plan(set_root, store, cache), engine=FakeEngine("Hallo Welt", looping))
    again = FakeEngine("Hallo Welt")
    second = runner.execute(_plan(set_root, store, cache), engine=again)

    assert first.pages[1].flags == ("empty", "cut_off")
    assert first.pages[1].seconds == runner.TIMEOUT
    assert again.read_pages == []
    assert second.pages[1].flags == ("empty", "cut_off")


def test_a_longer_deadline_reads_a_page_the_shorter_one_ended_again(tmp_path: Path) -> None:
    """Given a page a run's deadline ended
    When   the same run is made again with a longer deadline
    Then   that page is read again, since the model may now finish it
    """
    set_root = _set(tmp_path / "pages")
    store, cache = tmp_path / "store", tmp_path / "cache"
    looping = OCRDeadlineError("the answer was still being written after 60 s")
    runner.execute(
        _plan(set_root, store, cache, timeout=60.0), engine=FakeEngine("Hallo Welt", looping)
    )
    patient = FakeEngine("Hallo Welt")

    record = runner.execute(_plan(set_root, store, cache, timeout=1800.0), engine=patient)

    assert len(patient.read_pages) == 1
    assert record.pages[1].flags == ()


def test_each_page_is_asked_in_its_own_language(tmp_path: Path) -> None:
    """Given a set of a German page and an English one, as pages.jsonl names them
    When   the run is made
    Then   each page is read in its own language, and the header names both
    """
    set_root = _set(tmp_path / "pages", languages=("de", "en"))
    engine = FakeEngine("Hallo Welt")

    record = runner.execute(_plan(set_root, tmp_path / "store", tmp_path / "cache"), engine=engine)

    assert engine.languages == ["German", "English"]
    assert record.header.language == "English,German"


def test_a_run_that_reads_every_page_empty_is_refused(tmp_path: Path) -> None:
    """Given an engine whose server refused every page
    When   the run is executed
    Then   it refuses to write a record, saying that every page came back empty
    """
    set_root = _set(tmp_path / "pages")

    with pytest.raises(ValueError, match="every one of them empty"):
        runner.execute(
            _plan(set_root, tmp_path / "store", tmp_path / "cache"), engine=FakeEngine(None, None)
        )


def test_a_set_where_two_pages_bear_one_id_is_refused(tmp_path: Path) -> None:
    """Given a transcribed page of the same name in two groups
    When   the run is executed
    Then   it is refused, naming both files and reading no page
    """
    set_root = _set(tmp_path / "pages", names=("s01",))
    other = set_root / "printed_photo"
    other.mkdir()
    (other / "s01.png").write_bytes(b"page s01, printed")
    (other / f"s01{pages.REFERENCE_SUFFIX}").write_text("Hallo Welt", encoding="utf-8")
    engine = FakeEngine()

    with pytest.raises(ValueError, match="two pages bear this id") as refused:
        runner.execute(_plan(set_root, tmp_path / "store", tmp_path / "cache"), engine=engine)

    assert "handwritten_scan/s01.png and printed_photo/s01.png" in str(refused.value)
    assert engine.read_pages == []


def test_a_set_without_metadata_is_refused(tmp_path: Path) -> None:
    """Given a page with a transcription but no pages.jsonl
    When   the run is executed
    Then   it is refused, saying what the metadata is needed for
    """
    set_root = _set(tmp_path / "pages")
    (set_root / pages.METADATA_NAME).unlink()

    with pytest.raises(ValueError, match=pages.METADATA_NAME):
        runner.execute(_plan(set_root, tmp_path / "store", tmp_path / "cache"), engine=FakeEngine())


def test_a_set_without_a_transcription_is_refused(tmp_path: Path) -> None:
    """Given a folder with an image but no transcription beside it
    When   the run is executed
    Then   it is refused, naming the missing suffix
    """
    group = tmp_path / "pages" / "handwritten_scan"
    group.mkdir(parents=True)
    (group / "s01.png").write_bytes(b"page")

    with pytest.raises(ValueError, match=pages.REFERENCE_SUFFIX):
        runner.execute(
            _plan(tmp_path / "pages", tmp_path / "store", tmp_path / "cache"), engine=FakeEngine()
        )


def test_a_cpu_only_run_after_a_card_run_reads_every_page_again(tmp_path: Path) -> None:
    """Given a finished run on the graphics card
    When   the same plan is run with the model kept on the processor
    Then   the engine is asked again: the seconds of one device are not the other's
    """
    set_root = _set(tmp_path / "pages")
    store, cache = tmp_path / "store", tmp_path / "cache"
    runner.execute(_plan(set_root, store, cache), engine=FakeEngine("Hallo Welt"))
    on_the_processor = FakeEngine("Hallo Welt", cpu_only=True)

    runner.execute(_plan(set_root, store, cache, cpu_only=True), engine=on_the_processor)

    assert on_the_processor.read_pages


def test_a_cached_reading_keeps_its_seconds_and_its_memory(tmp_path: Path) -> None:
    """Given a finished run
    When   the same plan is run again, and its pages come back from the cache
    Then   the seconds and the memory of the first reading are in the record, not zero
    """
    set_root = _set(tmp_path / "pages")
    store, cache = tmp_path / "store", tmp_path / "cache"
    runner.execute(_plan(set_root, store, cache), engine=FakeEngine("Hallo Welt"))

    record = runner.execute(_plan(set_root, store, cache), engine=FakeEngine("Etwas anderes"))

    assert record.header.memory_mb == 128
    assert [page.seconds for page in record.pages] == [0.5, 0.5]


def test_a_lost_server_ends_the_run_and_writes_nothing(tmp_path: Path) -> None:
    """Given a server that goes away while the second page is read
    When   the run is made
    Then   it raises rather than scoring the pages of a model that was never asked
    """
    set_root = _set(tmp_path / "pages")
    plan = _plan(set_root, tmp_path / "store", tmp_path / "cache")
    gone = OllamaUnreachableError(unreachable("http://127.0.0.1:11434", detail("down")))

    with pytest.raises(OllamaUnreachableError):
        runner.run(plan, engine=FakeEngine("Hallo Welt", gone))

    assert runs.run_files(plan.store) == []


def test_a_tag_the_server_does_not_hold_is_refused_before_the_first_page(tmp_path: Path) -> None:
    """Given an Ollama engine whose server has no digest for its tag
    When   the run is executed
    Then   it is refused, naming the tag and the server, and no page is read
    """
    set_root = _set(tmp_path / "pages")
    engine = FakeEngine(engine="ollama", model="qwen3-vl:8b", digest="")

    with pytest.raises(ValueError, match="qwen3-vl:8b: the server at "):
        runner.execute(_plan(set_root, tmp_path / "store", tmp_path / "cache"), engine=engine)

    assert engine.read_pages == []


def test_a_run_writes_its_record_to_the_store(tmp_path: Path) -> None:
    """Given a set and an engine
    When   the run is made
    Then   one file of this tool's suffix sits in the store's own folder, and reads back as the
           record the run returned
    """
    set_root = _set(tmp_path / "pages")
    plan = _plan(set_root, tmp_path / "store", tmp_path / "cache")

    path, record = runner.run(plan, engine=FakeEngine("Hallo Welt"))

    assert path.parent == runs.runs_dir(plan.store)
    assert path.name.endswith(runs.RUN_SUFFIX)
    assert runs.read(path) == record


def test_a_split_reads_only_its_side(tmp_path: Path) -> None:
    """Given a set of four pages of two writers, split once
    When   the check side is run
    Then   only the pages of that side are read, and each record says which side it is
    """
    set_root = _set(tmp_path / "pages", writers=2, names=("s01", "s02", "s03", "s04"))
    whole = runner.execute(
        _plan(set_root, tmp_path / "store", tmp_path / "cache"), engine=FakeEngine()
    )
    check = {page.page for page in whole.pages if page.split == records.CHECK}
    engine = FakeEngine()

    record = runner.execute(
        _plan(set_root, tmp_path / "store2", tmp_path / "cache2", split=records.CHECK),
        engine=engine,
    )

    assert check
    assert {page.page for page in record.pages} == check
    assert {page.split for page in record.pages} == {records.CHECK}


def test_a_side_of_the_set_that_holds_no_page_is_refused(tmp_path: Path) -> None:
    """Given a set of one writer, which the split puts wholly on the tune side
    When   the check side is run
    Then   the run is refused for the empty side, not told the model answered nothing
    """
    set_root = _set(tmp_path / "pages", writers=1)
    plan = _plan(set_root, tmp_path / "store", tmp_path / "cache", split=records.CHECK)

    with pytest.raises(ValueError, match="no page on the check side"):
        runner.execute(plan, engine=FakeEngine())


def test_runs_that_differ_where_the_name_spells_it_get_a_file_each(tmp_path: Path) -> None:
    """Given plans of one set that differ only in the detector, the side or the machine
    When   each is run into one store
    Then   each gets a file of its own, because the name carries all three
    """
    set_root = _set(tmp_path / "pages")
    store = tmp_path / "store"
    base = _plan(set_root, store, tmp_path / "cache")
    plans = [
        base,
        replace(base, detector=(("max_side", 960.0),)),
        replace(base, split=records.TUNE),
        replace(base, machine="laptop"),
    ]

    paths = [
        runner.run(plan, engine=FakeEngine(settings=detector_settings(plan.detector)))[0]
        for plan in plans
    ]

    assert len({path.name for path in paths}) == len(plans)
    assert all(path.is_file() for path in paths)


def test_a_second_run_of_one_name_is_refused_until_it_is_replaced(tmp_path: Path) -> None:
    """Given a store that already holds a run of this plan
    When   the same plan is run again, and then with replace asked for
    Then   the first is refused, and the second writes over it
    """
    set_root = _set(tmp_path / "pages")
    plan = _plan(set_root, tmp_path / "store", tmp_path / "cache")
    first, _record = runner.run(plan, engine=FakeEngine("Hallo Welt"))

    with pytest.raises(FileExistsError, match="--replace"):
        runner.run(plan, engine=FakeEngine("Hallo Welt"))
    again, _record = runner.run(plan, engine=FakeEngine("Hallo Welt"), replace=True)

    assert again == first
