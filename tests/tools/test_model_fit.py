"""What a model really needs: the stored records, the need, the limit, the growth.

No server and no card: the records are built here, the way a measurement would store them.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from teachers_teammate.domain.context_budget import BASE_NUM_CTX, CONTEXT_STEPS
from teachers_teammate.domain.model_catalog import (
    FULL_SPEED_SHARE,
    card_budget_gb,
    runs_at_full_speed,
)
from tools.model_eval.model_fit import (
    GB,
    LINEARITY_TOLERANCE,
    MEMORY_TOLERANCE,
    CardReading,
    FitLimit,
    FitRecord,
    FitRecordError,
    FitStore,
    OllamaReading,
    RamReading,
    card_increase_bytes,
    catalog_lines,
    compare_lines,
    current_build,
    fit_failure,
    fit_line,
    growth_figures,
    growth_per_8k,
    latest,
    need_bytes,
    ollama_off_by,
    ram_drop_bytes,
    record_line,
    report_lines,
    size_for_decision,
    spilled,
    tier_windows,
    within,
)

DIGEST = "sha256:0123456789abcdef0123456789abcdef"
TAG = "qwen3:4b"
SERVER = "rx9070xt"
CARD_TOTAL = 17095983104
"""A 16 GB card, as the agent reports it."""


def _record(  # noqa: PLR0913  # one keyword per figure a test may vary
    *,
    num_ctx: int = BASE_NUM_CTX,
    size_gb: float = 8.0,
    vram_gb: float | None = None,
    card_gb: float | None = None,
    idle_gb: float = 0.7,
    ram_drop_gb: float | None = None,
    server: str = SERVER,
    tag: str = TAG,
    digest: str = DIGEST,
    measured_at: str = "2026-11-13T19:04:11Z",
    ollama_version: str = "0.40.15",
) -> FitRecord:
    """Return a record as a measurement would store it, with the figures a test names."""
    on_card = size_gb if vram_gb is None else vram_gb
    card = None
    if card_gb is not None:
        card = CardReading(
            agent_sha256="abc",
            source="sysfs",
            total_bytes=CARD_TOTAL,
            idle_bytes=int(idle_gb * GB),
            peak_bytes=int((idle_gb + card_gb) * GB),
            end_bytes=int((idle_gb + card_gb) * GB),
        )
    ram = None
    if ram_drop_gb is not None:
        ram = RamReading(
            total_bytes=33285996544,
            idle_available_bytes=int(30 * GB),
            min_available_bytes=int((30 - ram_drop_gb) * GB),
        )
    return FitRecord(
        measured_at=measured_at,
        server=server,
        ollama_version=ollama_version,
        tool_commit="abc1234",
        tag=tag,
        digest=digest,
        num_ctx=num_ctx,
        options={"temperature": 1.0, "num_ctx": num_ctx},
        load_seconds=4.2,
        ollama=OllamaReading(
            context_length=num_ctx,
            size_bytes=int(size_gb * GB),
            size_vram_bytes=int(on_card * GB),
        ),
        card=card,
        ram=ram,
    )


# ── the record and the store ─────────────────────────────────────────────────


@pytest.mark.use_case("Model_Eval_Model_Fit")
def test_a_record_survives_the_round_trip_through_json() -> None:
    """
    Given  a record with a card reading and a main-memory reading
    When   it is written as JSON and read back
    Then   every field is the same, so a stored line says what was measured
    """
    record = _record(num_ctx=16384, size_gb=9.5, card_gb=9.8, ram_drop_gb=1.5)

    again = FitRecord.from_json(json.loads(json.dumps(record.to_json())))

    assert again == record


@pytest.mark.use_case("Model_Eval_Model_Fit")
def test_a_record_of_another_schema_is_refused() -> None:
    """
    Given  a stored line written by a later version of the tool
    When   it is read
    Then   the reader refuses it instead of guessing what its fields mean
    """
    data = _record().to_json()
    data["schema"] = 2

    with pytest.raises(FitRecordError, match="schema 2 is not 1"):
        FitRecord.from_json(data)


@pytest.mark.use_case("Model_Eval_Model_Fit")
def test_a_record_without_a_field_is_refused() -> None:
    """
    Given  a stored line that lacks the digest
    When   it is read
    Then   the reader names the missing field
    """
    data = _record().to_json()
    del data["digest"]

    with pytest.raises(FitRecordError, match="'digest'"):
        FitRecord.from_json(data)


@pytest.mark.use_case("Model_Eval_Model_Fit")
def test_the_store_appends_and_reads_back(tmp_path: Path) -> None:
    """
    Given  an empty store
    When   two records are appended
    Then   both come back in the order they were measured, and the file has two lines
    """
    store = FitStore(tmp_path / "model-fit" / "sizes.jsonl")
    first = _record(num_ctx=8192)
    second = _record(num_ctx=16384)

    store.append(first)
    store.append(second)

    assert store.records() == (first, second)
    assert len(store.path.read_text(encoding="utf-8").splitlines()) == 2


@pytest.mark.use_case("Model_Eval_Model_Fit")
def test_an_absent_store_holds_no_record(tmp_path: Path) -> None:
    """
    Given  a store whose file was never written
    When   it is read
    Then   it holds nothing, so a first measurement needs no setup
    """
    assert FitStore(tmp_path / "absent.jsonl").records() == ()


@pytest.mark.use_case("Model_Eval_Model_Fit")
def test_the_store_knows_what_was_measured(tmp_path: Path) -> None:
    """
    Given  a store with one record
    When   it is asked about that build and window, and about another window
    Then   only the measured one counts as done
    """
    store = FitStore(tmp_path / "sizes.jsonl")
    store.append(_record(num_ctx=8192))

    assert store.has(server=SERVER, tag=TAG, digest=DIGEST, num_ctx=8192, ollama_version="0.40.15")
    assert not store.has(
        server=SERVER, tag=TAG, digest=DIGEST, num_ctx=16384, ollama_version="0.40.15"
    )
    assert not store.has(
        server=SERVER, tag=TAG, digest=DIGEST, num_ctx=8192, ollama_version="0.40.16"
    )


# ── what a record says ───────────────────────────────────────────────────────


@pytest.mark.use_case("Model_Eval_Model_Fit")
def test_the_need_is_the_card_increase_when_ollama_reports_less() -> None:
    """
    Given  a model Ollama calls 1.0 GB while the card holds 6.9 GB more than before
    When   the need is worked out
    Then   the card's figure wins: Ollama's bookkeeping leaves the driver's share out
    """
    record = _record(size_gb=1.0, card_gb=6.9)

    assert card_increase_bytes(record) == int(6.9 * GB)
    assert need_bytes(record) == int(6.9 * GB)
    assert ollama_off_by(record) == pytest.approx((6.9 - 1.0) / 6.9)


@pytest.mark.use_case("Model_Eval_Model_Fit")
def test_the_need_is_ollamas_size_when_the_card_holds_less() -> None:
    """
    Given  a model Ollama calls 9.0 GB while the card's use rose by 8.0 GB
    When   the need is worked out
    Then   Ollama's figure wins, because the rest sits in main memory
    """
    record = _record(size_gb=9.0, vram_gb=8.0, card_gb=8.0)

    assert need_bytes(record) == int(9.0 * GB)


@pytest.mark.use_case("Model_Eval_Model_Fit")
def test_without_a_card_reading_the_need_is_ollamas_size() -> None:
    """
    Given  a record made with ``--no-agent``
    When   the need is worked out
    Then   Ollama's size is all there is, and nothing claims a card figure
    """
    record = _record(size_gb=8.0)

    assert card_increase_bytes(record) is None
    assert need_bytes(record) == int(8.0 * GB)
    assert ollama_off_by(record) is None


@pytest.mark.use_case("Model_Eval_Model_Fit")
def test_the_main_memory_drop_is_read_from_the_record() -> None:
    """
    Given  a record whose available main memory fell by 1.5 GB
    When   the drop is read
    Then   it is 1.5 GB, and ``None`` without a reading
    """
    assert ram_drop_bytes(_record(ram_drop_gb=1.5)) == int(1.5 * GB)
    assert ram_drop_bytes(_record()) is None


@pytest.mark.use_case("Model_Eval_Model_Fit")
def test_a_model_ollama_splits_is_flagged_as_spilled() -> None:
    """
    Given  a model Ollama reports partly in main memory
    When   the record is asked whether it spilled
    Then   it did, so its need is only a lower bound
    """
    assert spilled(_record(size_gb=9.0, vram_gb=8.0)) is True
    assert spilled(_record(size_gb=8.0, vram_gb=8.0)) is False


@pytest.mark.use_case("Model_Eval_Model_Fit")
def test_a_card_peak_at_its_total_is_flagged_as_spilled() -> None:
    """
    Given  a card whose peak sits at 99 % of its total while Ollama claims all of it on the card
    When   the record is asked whether it spilled
    Then   it did: something had to go elsewhere
    """
    record = _record(size_gb=16.0, vram_gb=16.0, card_gb=CARD_TOTAL / GB * 0.99 - 0.7)

    assert spilled(record) is True


# ── reading the store ────────────────────────────────────────────────────────


@pytest.mark.use_case("Model_Eval_Model_Fit")
def test_the_newest_record_per_server_is_the_one_that_counts() -> None:
    """
    Given  two records of the same build and window on one server, and one of another digest
    When   the latest records are collected
    Then   the newer one of the server is kept and the other build is ignored
    """
    older = _record(measured_at="2026-11-01T10:00:00Z", size_gb=8.0)
    newer = _record(measured_at="2026-11-13T19:04:11Z", size_gb=8.4)
    other = _record(measured_at="2026-11-20T10:00:00Z", digest="sha256:other")

    found = latest([older, newer, other], tag=TAG, digest=DIGEST, num_ctx=BASE_NUM_CTX)

    assert found == {SERVER: newer}


@pytest.mark.use_case("Model_Eval_Model_Fit")
def test_the_decision_takes_the_larger_need_of_two_servers() -> None:
    """
    Given  the same build measured on two machines, one needing more
    When   the record to decide with is chosen
    Then   the larger need wins, because two backends that disagree give the cautious answer
    """
    small = _record(server="rx9070xt", size_gb=8.0, card_gb=8.2)
    large = _record(server="rtx3070", size_gb=9.0, card_gb=9.4)

    chosen = size_for_decision([small, large], tag=TAG, digest=DIGEST, num_ctx=BASE_NUM_CTX)

    assert chosen is large


@pytest.mark.use_case("Model_Eval_Model_Fit")
def test_nothing_measured_gives_no_record_to_decide_with() -> None:
    """
    Given  a store without a record of this build
    When   the record to decide with is asked for
    Then   there is none, so the caller knows it has to measure
    """
    assert size_for_decision([], tag=TAG, digest=DIGEST, num_ctx=BASE_NUM_CTX) is None


@pytest.mark.use_case("Model_Eval_Model_Fit")
def test_the_growth_per_8k_comes_from_the_longest_span() -> None:
    """
    Given  a model that needs 8.0 GB at 8192 tokens and 14.0 GB at 32768
    When   the growth is worked out
    Then   it is 2 GB per 8192 tokens
    """
    records = [
        _record(num_ctx=8192, size_gb=8.0, card_gb=8.0),
        _record(num_ctx=16384, size_gb=10.0, card_gb=10.0),
        _record(num_ctx=32768, size_gb=14.0, card_gb=14.0),
    ]

    assert growth_per_8k(records) == pytest.approx(2.0)


@pytest.mark.use_case("Model_Eval_Model_Fit")
def test_a_spilled_step_is_left_out_of_the_growth() -> None:
    """
    Given  a model whose largest window spilled onto the processor
    When   the growth is worked out
    Then   only the steps that fit the card count, because a spilled need is a lower bound
    """
    records = [
        _record(num_ctx=8192, size_gb=8.0, card_gb=8.0),
        _record(num_ctx=16384, size_gb=10.0, card_gb=10.0),
        _record(num_ctx=32768, size_gb=14.0, vram_gb=11.0, card_gb=11.0),
    ]

    assert growth_per_8k(records) == pytest.approx(2.0)


@pytest.mark.use_case("Model_Eval_Model_Fit")
def test_one_step_is_not_enough_for_a_growth() -> None:
    """
    Given  a model measured at one window only
    When   the growth is worked out
    Then   there is none, because there is nothing to grow from
    """
    assert growth_per_8k([_record(num_ctx=8192, size_gb=8.0)]) is None


# ── the growth figures and their flags ───────────────────────────────────────


@pytest.mark.use_case("Model_Eval_Model_Fit")
def test_the_growth_figures_report_both_spans_and_no_flag() -> None:
    """
    Given  a model that grows 2 GB per 8k at both spans
    When   the figures are worked out
    Then   both spans agree and nothing is flagged
    """
    records = [
        _record(num_ctx=8192, size_gb=8.0, card_gb=8.0),
        _record(num_ctx=16384, size_gb=10.0, card_gb=10.0),
        _record(num_ctx=32768, size_gb=14.0, card_gb=14.0),
    ]

    figures = growth_figures(records, catalog_memory_gb=8.0)

    assert figures.size_at_base_gb == pytest.approx(8.0)
    assert figures.growth_per_8k_gb == pytest.approx(2.0)
    assert figures.growth_per_8k_short_gb == pytest.approx(2.0)
    assert figures.flags == ()


@pytest.mark.use_case("Model_Eval_Model_Fit")
def test_a_memory_that_differs_from_the_catalog_is_flagged() -> None:
    """
    Given  a model that needs 9.0 GB at 8192 tokens where the catalog says 8.0 GB
    When   the figures are worked out
    Then   the difference is flagged, because the catalog value is what the app sizes with
    """
    records = [
        _record(num_ctx=8192, size_gb=9.0, card_gb=9.0),
        _record(num_ctx=16384, size_gb=11.0, card_gb=11.0),
        _record(num_ctx=32768, size_gb=15.0, card_gb=15.0),
    ]

    figures = growth_figures(records, catalog_memory_gb=8.0)

    assert figures.memory_off_by == pytest.approx(0.125)
    assert figures.memory_off_by > MEMORY_TOLERANCE
    assert any("the catalog says 8.00 GB" in flag for flag in figures.flags)


@pytest.mark.use_case("Model_Eval_Model_Fit")
def test_a_difference_within_the_tolerance_is_not_flagged() -> None:
    """
    Given  a model that needs 8.4 GB at 8192 tokens where the catalog says 8.0 GB
    When   the figures are worked out
    Then   nothing is flagged: the catalog value is an estimate from another machine
    """
    records = [
        _record(num_ctx=8192, size_gb=8.4, card_gb=8.4),
        _record(num_ctx=16384, size_gb=10.4, card_gb=10.4),
        _record(num_ctx=32768, size_gb=14.4, card_gb=14.4),
    ]

    figures = growth_figures(records, catalog_memory_gb=8.0)

    assert figures.memory_off_by == pytest.approx(0.05)
    assert figures.memory_off_by < MEMORY_TOLERANCE
    assert figures.flags == ()


@pytest.mark.use_case("Model_Eval_Model_Fit")
def test_growth_that_is_not_linear_is_flagged() -> None:
    """
    Given  a model that grows 2 GB per 8k up to 16384 and 1 GB per 8k up to 32768
    When   the figures are worked out
    Then   the two spans are compared and the difference is flagged, because the app assumes
           linear growth
    """
    records = [
        _record(num_ctx=8192, size_gb=8.0, card_gb=8.0),
        _record(num_ctx=16384, size_gb=10.0, card_gb=10.0),
        _record(num_ctx=32768, size_gb=12.0, card_gb=12.0),
    ]

    figures = growth_figures(records)

    spread = abs(figures.growth_per_8k_gb - figures.growth_per_8k_short_gb) / max(
        figures.growth_per_8k_gb, figures.growth_per_8k_short_gb
    )
    assert spread > LINEARITY_TOLERANCE
    assert any("growth is not linear" in flag for flag in figures.flags)


@pytest.mark.use_case("Model_Eval_Model_Fit")
def test_a_step_in_main_memory_is_flagged() -> None:
    """
    Given  a model whose memory at 32768 tokens is larger than the part on the card
    When   the figures are worked out
    Then   the step is flagged: the figure says nothing about the card
    """
    records = [
        _record(num_ctx=8192, size_gb=8.0, card_gb=8.0),
        _record(num_ctx=16384, size_gb=10.0, card_gb=10.0),
        _record(num_ctx=32768, size_gb=14.0, vram_gb=11.0, card_gb=11.0),
    ]

    figures = growth_figures(records)

    assert figures.spilled is True
    assert any("partly in main memory at 32,768 tokens" in flag for flag in figures.flags)


@pytest.mark.use_case("Model_Eval_Model_Fit")
def test_a_missing_base_measurement_leaves_the_growth_unknown() -> None:
    """
    Given  measurements that do not include the base window
    When   the figures are worked out
    Then   there is no growth figure, because there is nothing to grow from
    """
    records = [
        _record(num_ctx=16384, size_gb=10.0, card_gb=10.0),
        _record(num_ctx=32768, size_gb=12.0, card_gb=12.0),
    ]

    figures = growth_figures(records, catalog_memory_gb=8.0)

    assert figures.size_at_base_gb is None
    assert figures.growth_per_8k_gb is None
    assert figures.growth_per_8k_short_gb is None
    assert figures.memory_off_by is None


# ── the limit ────────────────────────────────────────────────────────────────


@pytest.mark.use_case("Model_Eval_Model_Fit")
def test_a_tier_limit_is_the_apps_own_rule() -> None:
    """
    Given  the limit ``16gb``
    When   it is parsed
    Then   the largest need allowed is the usable card divided by the full-speed share, so the
           screen and the app cannot disagree about a tier
    """
    limit = FitLimit.parse("16gb")

    assert limit.max_size_gb == pytest.approx(card_budget_gb("16gb") / FULL_SPEED_SHARE)
    assert limit.label == "16gb"
    assert FitLimit.parse("8gb").max_size_gb == pytest.approx(
        card_budget_gb("8gb") / FULL_SPEED_SHARE
    )


@pytest.mark.use_case("Model_Eval_Model_Fit")
def test_a_number_limit_is_that_many_gb() -> None:
    """
    Given  the limit ``14.5``
    When   it is parsed
    Then   the largest need allowed is 14.5 GB
    """
    limit = FitLimit.parse("14.5")

    assert limit.max_size_gb == pytest.approx(14.5)
    assert limit.label == "14.5 GB"


@pytest.mark.use_case("Model_Eval_Model_Fit")
def test_the_limit_off_checks_nothing() -> None:
    """
    Given  the limit ``off``
    When   it is parsed
    Then   there is no largest need, and every model is within it
    """
    limit = FitLimit.parse("off")

    assert limit.max_size_gb is None
    assert limit.label == "off"
    assert within(_record(size_gb=99.0), limit) is True


@pytest.mark.use_case("Model_Eval_Model_Fit")
def test_a_limit_that_is_not_a_size_is_refused() -> None:
    """
    Given  the limits ``0`` and ``abc``
    When   they are parsed
    Then   both are refused with a message that says what a limit looks like
    """
    with pytest.raises(ValueError, match="has to be positive"):
        FitLimit.parse("0")
    with pytest.raises(ValueError, match="is not a limit"):
        FitLimit.parse("abc")


@pytest.mark.use_case("Model_Eval_Model_Fit")
def test_the_limit_is_compared_with_the_need() -> None:
    """
    Given  a model Ollama calls 14.0 GB while the card holds 15.8 GB more than before
    When   it is compared with the default 16 GB limit
    Then   it does not fit, because the need is the card's figure
    """
    record = _record(size_gb=14.0, card_gb=15.8)

    assert within(record, FitLimit.parse("16gb")) is False
    assert within(record, FitLimit.parse("16.5")) is True


@pytest.mark.use_case("Model_Eval_Model_Fit")
def test_the_tier_windows_name_the_largest_window_that_fits() -> None:
    """
    Given  a model needing 6.0, 7.0 and 9.0 GB at the three windows
    When   the largest fitting window is asked for at 8gb and at 16gb
    Then   the 8 GB card stops at 16384 and the 16 GB card reaches 32768
    """
    records = [
        _record(num_ctx=8192, size_gb=6.0, card_gb=6.0),
        _record(num_ctx=16384, size_gb=7.0, card_gb=7.0),
        _record(num_ctx=32768, size_gb=9.0, card_gb=9.0),
    ]

    windows = tier_windows(records, [FitLimit.parse("8gb"), FitLimit.parse("16gb")])

    assert windows == {"8gb": 16384, "16gb": 32768}


@pytest.mark.use_case("Model_Eval_Model_Fit")
def test_a_model_that_fits_no_window_gets_none() -> None:
    """
    Given  a model that needs more than the limit at every window
    When   the largest fitting window is asked for
    Then   there is none, and the report says so instead of naming a window
    """
    records = [_record(num_ctx=8192, size_gb=20.0, card_gb=20.0)]

    assert tier_windows(records, [FitLimit.parse("8gb")]) == {"8gb": None}


@pytest.mark.use_case("Model_Eval_Model_Fit")
def test_a_share_of_exactly_the_full_speed_share_counts_as_full_speed() -> None:
    """
    Given  a model of 10 GB on a card of 9 GB, which is exactly the full-speed share
    When   the app's rule is asked
    Then   it counts as full speed, as it does in the app
    """
    assert runs_at_full_speed(10.0, 10.0 * FULL_SPEED_SHARE) is True
    assert runs_at_full_speed(10.0, 10.0 * FULL_SPEED_SHARE - 0.01) is False


# ── printing ─────────────────────────────────────────────────────────────────


@pytest.mark.use_case("Model_Eval_Model_Fit")
def test_the_report_names_every_step_the_growth_and_the_tiers() -> None:
    """
    Given  the measurements of one model on one server
    When   the report block is printed
    Then   every window, the card's figure, the growth per 8k and the tier windows appear
    """
    records = [
        _record(num_ctx=8192, size_gb=8.0, card_gb=8.0, ram_drop_gb=1.0),
        _record(num_ctx=16384, size_gb=10.0, card_gb=10.0),
        _record(num_ctx=32768, size_gb=14.0, card_gb=14.0),
    ]

    lines = report_lines(TAG, records, catalog_memory_gb=8.0, limits=[FitLimit.parse("16gb")])

    assert lines[0] == f"{TAG} on {SERVER}:"
    assert any("8,192 tokens" in line and "8.00 GB" in line for line in lines)
    assert any("32,768 tokens" in line and "100 % on the card" in line for line in lines)
    assert any("main memory -1.00 GB" in line for line in lines)
    assert any("growth per 8k (long span)" in line for line in lines)
    assert any("limit 16gb: up to 32,768 tokens" in line for line in lines)


@pytest.mark.use_case("Model_Eval_Model_Fit")
def test_a_record_without_a_card_reading_says_so_in_the_report() -> None:
    """
    Given  a record made with ``--no-agent``
    When   the report block is printed
    Then   it says there is no card reading instead of showing a figure of zero
    """
    lines = report_lines(TAG, [_record(size_gb=8.0)])

    assert any("no card reading" in line for line in lines)


@pytest.mark.use_case("Model_Eval_Model_Fit")
def test_the_measurement_line_names_the_need_and_the_card() -> None:
    """
    Given  a record whose card holds more than Ollama reports
    When   the line of the measurement is printed
    Then   the need, Ollama's figure, the card's figure and how far apart they are appear
    """
    line = record_line(_record(size_gb=1.0, card_gb=6.9))

    assert "needs   6.90 GB" in line
    assert "Ollama says   1.00 GB" in line
    assert "the card holds   6.90 GB" in line
    assert "86 % apart" in line


@pytest.mark.use_case("Model_Eval_Model_Fit")
def test_a_spilled_measurement_says_so() -> None:
    """
    Given  a record whose model did not fit the card
    When   the line of the measurement is printed
    Then   it is marked as spilled, so nobody reads its need as complete
    """
    assert "SPILLED" in record_line(_record(size_gb=9.0, vram_gb=8.0, card_gb=8.0))


@pytest.mark.use_case("Model_Eval_Model_Fit")
def test_the_comparison_marks_a_size_that_disagrees() -> None:
    """
    Given  the same build measured on two machines, 12 % apart
    When   the two are put side by side
    Then   the figure is marked as disagreeing, by the cross-check's own tolerance
    """
    first = [_record(server="rx9070xt", size_gb=8.0, card_gb=8.0)]
    second = [_record(server="rtx3070", size_gb=8.96, card_gb=8.96)]

    lines = compare_lines(TAG, first, second)

    assert any("DISAGREE" in line for line in lines)


@pytest.mark.use_case("Model_Eval_Model_Fit")
def test_the_comparison_accepts_two_machines_that_agree() -> None:
    """
    Given  the same build measured on two machines, 2 % apart
    When   the two are put side by side
    Then   the figure is marked as agreeing
    """
    first = [_record(server="rx9070xt", size_gb=8.0, card_gb=8.0)]
    second = [_record(server="rtx3070", size_gb=8.16, card_gb=8.16)]

    lines = compare_lines(TAG, first, second)

    assert any("ok" in line for line in lines)
    assert not any("DISAGREE" in line for line in lines)


@pytest.mark.use_case("Model_Eval_Model_Fit")
def test_the_catalog_figures_are_the_need_and_the_growth() -> None:
    """
    Given  a model measured at every window
    When   the figures to enter in the catalog are printed
    Then   ``memory_gb`` is the need at 8192 and ``context_gb_per_8k`` the growth
    """
    records = [
        _record(num_ctx=8192, size_gb=8.0, card_gb=8.4),
        _record(num_ctx=16384, size_gb=10.0, card_gb=10.4),
        _record(num_ctx=32768, size_gb=14.0, card_gb=14.4),
    ]

    lines = catalog_lines(TAG, records, catalog_memory_gb=8.0)

    assert any("memory_gb: 8.40" in line for line in lines)
    assert any("context_gb_per_8k: 2.00" in line for line in lines)


@pytest.mark.use_case("Model_Eval_Model_Fit")
def test_the_screens_line_names_the_need_its_source_and_the_limit() -> None:
    """
    Given  a record whose card increase is larger than Ollama's figure, and a 16 GB limit
    When   the line a screen prints for it is made
    Then   it names the need, the server, Ollama's own figure and the limit, so a reader can
           check the figure against the store
    """
    record = _record(size_gb=9.0, card_gb=9.84)

    line = fit_line(record, FitLimit.parse("16gb"))

    assert line == (
        "fit: needs 9.84 GB at 8,192 tokens (rx9070xt; Ollama says 9.00), limit 15.11 GB (16gb)"
    )


@pytest.mark.use_case("Model_Eval_Model_Fit")
def test_a_record_without_a_card_reading_says_so_in_the_line() -> None:
    """
    Given  a record made with ``--no-agent``
    When   the line a screen prints for it is made
    Then   it says the figure is Ollama's alone, because that figure leaves out the driver's
           share
    """
    record = _record(size_gb=9.0, card_gb=None)

    line = fit_line(record, FitLimit.parse("16gb"))

    assert "Ollama's size only" in line
    assert "Ollama says" not in line


@pytest.mark.use_case("Model_Eval_Model_Fit")
def test_the_failure_names_the_date_and_the_source_of_the_figure() -> None:
    """
    Given  a record whose need is above the limit
    When   the reason a setup fails is made
    Then   it names the need, the server, the date, the source and the limit, so the reader can
           find the line in the store
    """
    record = _record(size_gb=14.0, card_gb=15.8)

    reason = fit_failure(record, FitLimit.parse("16gb"))

    assert reason == (
        "needs 15.80 GB at 8,192 tokens (rx9070xt, 2026-11-13, card reading); limit 15.11 GB (16gb)"
    )


@pytest.mark.use_case("Model_Eval_Model_Fit")
def test_the_ladder_is_the_apps_own() -> None:
    """
    Given  the windows a measurement walks
    When   they are compared with the app's ladder
    Then   they are the same three, so a record says what the app would ask for
    """
    assert CONTEXT_STEPS == (8192, 16384, 32768)
    assert BASE_NUM_CTX == CONTEXT_STEPS[0]


@pytest.mark.use_case("Model_Eval_Model_Fit")
def test_only_the_newest_build_and_the_newest_record_per_window_count() -> None:
    """
    Given  a store holding an older digest and a repeated measurement of the newest one
    When   the current build is taken
    Then   the older digest is left out, and of a repeat the newer record counts
    """
    old_build = _record(
        num_ctx=8192, size_gb=4.0, digest="sha256:old", measured_at="2026-11-01T10:00:00Z"
    )
    first = _record(num_ctx=8192, size_gb=8.0, measured_at="2026-11-10T10:00:00Z")
    again = _record(num_ctx=8192, size_gb=8.5, measured_at="2026-11-12T10:00:00Z")
    larger = _record(num_ctx=16384, size_gb=9.0, measured_at="2026-11-12T10:05:00Z")

    current = current_build([old_build, first, again, larger])

    assert [(record.num_ctx, record.ollama.size_bytes) for record in current] == [
        (8192, int(8.5 * GB)),
        (16384, int(9.0 * GB)),
    ]


@pytest.mark.use_case("Model_Eval_Model_Fit")
def test_the_growth_is_not_mixed_across_builds() -> None:
    """
    Given  an old build measured at 8192 and a new build measured at 16384 and 8192
    When   the growth is worked out
    Then   it comes from the new build alone
    """
    old_build = _record(
        num_ctx=8192, size_gb=2.0, digest="sha256:old", measured_at="2026-11-01T10:00:00Z"
    )
    base = _record(num_ctx=8192, size_gb=8.0, measured_at="2026-11-12T10:00:00Z")
    larger = _record(num_ctx=16384, size_gb=9.0, measured_at="2026-11-12T10:05:00Z")

    assert growth_per_8k([old_build, base, larger]) == pytest.approx(1.0)
    assert current_build([]) == ()
