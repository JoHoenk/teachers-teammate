"""Behaviour tests for the benchmark sub-widgets (CompareView, RunsList)."""
# pylint: disable=W0621,W0613  # pytest fixtures shadow module-scope names / injected by name

from __future__ import annotations

import pytest

from teachers_teammate.config import OcrConfig
from teachers_teammate.domain.benchmark import compare_pair
from teachers_teammate.gui.benchmark._compare_view import CompareView
from teachers_teammate.gui.benchmark._runs_list import RunsList
from teachers_teammate.infrastructure.benchmark.run_store import StoredRun


def _run(run_id: str, text: str) -> StoredRun:
    return StoredRun(
        schema_version=1,
        run_id=run_id,
        document_hash="doc1",
        document_path="/docs/sample.pdf",
        display_name="sample.pdf",
        ocr_config_hash="abc12345",
        ocr=OcrConfig(engine="tesseract", model="", preprocess_method="none"),
        language="English",
        raw_text=text,
        preview_img="",
        timestamp=f"2026-06-06T10:00:0{run_id}",
        elapsed_s=1.0,
    )


@pytest.mark.gui
@pytest.mark.use_case("OCR_Run_Comparison")
def test_compare_view_shows_comparison(qtbot) -> None:
    """
    Given  two runs and their PairComparison
    When   show_comparison() is called
    Then   both text panes, the diff, and a similarity label are populated
    """
    view = CompareView()
    qtbot.addWidget(view)
    a, b = _run("1", "hello world"), _run("2", "hello there")
    view.show_comparison(a, b, compare_pair(a.raw_text, b.raw_text))

    assert view._text_a.toPlainText() == "hello world"
    assert view._text_b.toPlainText() == "hello there"
    assert "Similarity:" in view._similarity.text()


@pytest.mark.gui
def test_compare_view_clear_resets(qtbot) -> None:
    """
    Given  a CompareView showing a comparison
    When   clear() is called
    Then   the panes empty and the prompt label returns
    """
    view = CompareView()
    qtbot.addWidget(view)
    a, b = _run("1", "x"), _run("2", "y")
    view.show_comparison(a, b, compare_pair(a.raw_text, b.raw_text))
    view.clear()
    assert view._text_a.toPlainText() == ""
    assert "Select run A and run B" in view._similarity.text()


@pytest.mark.gui
@pytest.mark.use_case("OCR_Run_Comparison")
def test_runs_list_assigns_a_and_b(qtbot) -> None:
    """
    Given  a RunsList populated with two runs
    When   one is set as A and the other as B
    Then   selection_changed emits both run ids and rows show [A]/[B] markers
    """
    widget = RunsList()
    qtbot.addWidget(widget)
    emitted: list[tuple[str, str]] = []
    widget.selection_changed.connect(lambda a, b: emitted.append((a, b)))
    widget.set_runs([_run("1", "a"), _run("2", "b")])

    widget._list.setCurrentRow(0)
    widget._assign("a")
    widget._list.setCurrentRow(1)
    widget._assign("b")

    assert emitted[-1] == ("1", "2")
    texts = [widget._list.item(i).text() for i in range(widget._list.count())]
    assert any("[A]" in t for t in texts)
    assert any("[B]" in t for t in texts)


@pytest.mark.gui
@pytest.mark.use_case("Benchmark_Run_Deletion")
def test_runs_list_delete_and_clear_signals(qtbot) -> None:
    """
    Given  a populated RunsList with a selected run
    When   delete and clear-all are triggered
    Then   delete_requested(run_id) and clear_all_requested fire
    """
    widget = RunsList()
    qtbot.addWidget(widget)
    deleted: list[str] = []
    cleared: list[bool] = []
    widget.delete_requested.connect(deleted.append)
    widget.clear_all_requested.connect(lambda: cleared.append(True))
    widget.set_runs([_run("1", "a")])

    widget._list.setCurrentRow(0)
    widget._on_delete()
    widget.clear_all_requested.emit()  # simulate the clear button

    assert deleted == ["1"]
    assert cleared == [True]


@pytest.mark.gui
def test_runs_list_drops_stale_ab_on_refresh(qtbot) -> None:
    """
    Given  a RunsList with run "1" assigned as A
    When   set_runs is called with a list that no longer contains "1"
    Then   the A selection is dropped and selection_changed reports it empty
    """
    widget = RunsList()
    qtbot.addWidget(widget)
    widget.set_runs([_run("1", "a")])
    widget._list.setCurrentRow(0)
    widget._assign("a")

    emitted: list[tuple[str, str]] = []
    widget.selection_changed.connect(lambda a, b: emitted.append((a, b)))
    widget.set_runs([_run("2", "b")])

    assert emitted[-1] == ("", "")
