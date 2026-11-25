"""The fit and page time table: the two criteria that come before quality, read from the records.

The size record says what a model needs and how fast it writes on one machine and device, the run
says how long a page took.  The table judges a card by the memory it held, a processor-only
computer by the model's size in the catalog, and marks a page time that no run measured.
"""

from __future__ import annotations

from teachers_teammate.domain.context_budget import BASE_NUM_CTX
from teachers_teammate.domain.model_catalog import CPU_COMFORT_GB, HardwareProfile, card_budget_gb
from tools.evaluation.core.machines import Machine
from tools.evaluation.core.modes import ALL_STEPS, Exchange, Step
from tools.evaluation.core.sizes import CardReading, FitRecord, OllamaReading
from tools.evaluation.report.tables.fit import (
    limit_gb,
    summarise_tiers,
    tier_table,
)
from tools.evaluation.report.tables.performance import PerformanceSummary

CARD_MACHINE = "card-machine"
CPU_MACHINE = "cpu-machine"

CARD_BYTES = 8 * 2**30
"""The card the memory agent read: 8 GiB, which the driver reports as 8188 MB."""

CARD_MB = 8188


def _machine(machine_id: str, vram_mb: int) -> Machine:
    """Return a machine of the registry with the card *vram_mb* megabytes large."""
    return Machine(
        id=machine_id,
        gpu_vendor="nvidia",
        gpu_name="GeForce RTX 3070",
        vram_mb=vram_mb,
        cpu="Ryzen 7 5800H",
        ram_gib=32,
        os="Windows 11",
        accelerator_stack="CUDA 12",
    )


def _card(need_gb: float) -> CardReading:
    """Return a card reading whose idle figure is 0.4 GiB and whose peak is *need_gb* above it."""
    idle = int(0.4 * 2**30)
    peak = idle + int(need_gb * 1e9)
    return CardReading(
        agent_sha256="0" * 64,
        source="nvidia-smi",
        total_bytes=CARD_BYTES,
        idle_bytes=idle,
        peak_bytes=peak,
        end_bytes=peak,
    )


def _record(
    tag: str,
    *,
    need_gb: float,
    write_tps: float,
    machine: str = CARD_MACHINE,
    device: str = "gpu",
    num_ctx: int = BASE_NUM_CTX,
    measured_at: str = "2026-11-20T10:00:00+00:00",
) -> FitRecord:
    """Return one size measurement: the need in GB, and the tokens a second its answer ran at."""
    tokens = 200
    return FitRecord(
        measured_at=measured_at,
        machine=machine,
        ollama_version="0.12.0",
        tool_commit="0123456",
        tag=tag,
        digest=f"sha256:{tag}",
        num_ctx=num_ctx,
        answer_tokens=tokens,
        generation_seconds=tokens / write_tps,
        ollama=OllamaReading(
            context_length=num_ctx,
            size_bytes=int(need_gb * 1e9),
            size_vram_bytes=0 if device == "cpu" else int(need_gb * 1e9),
        ),
        card=None if device == "cpu" else _card(need_gb),
        device=device,
    )


def _summary(
    model: str,
    *,
    seconds_per_100_words: float,
    steps: tuple[Step, ...] = ALL_STEPS,
    machine: str = CARD_MACHINE,
) -> PerformanceSummary:
    """Return a performance summary of one model, as a run of *steps* on *machine* gives it."""
    return PerformanceSummary(
        model=model,
        temperature=0.05,
        mode=Exchange.TEXT_FIRST,
        exchange=Exchange.TEXT_FIRST,
        steps=steps,
        machine=machine,
        isolated=True,
        texts=3,
        comparable=3,
        seconds_per_100_words=seconds_per_100_words,
        answer_tokens_per_s=None,
        answer_tokens_spread=None,
        requests_per_text=None,
        step_one_share=None,
        lost_text=0,
        added_text=0,
        loss_checked=0,
        vram_used_mb=None,
        on_card_share=None,
    )


def _card_rows() -> list:
    """Return the rows of the 8 GB card for one comfortable model and one too large one."""
    sizes = [
        _record("gemma4:e4b", need_gb=4.6, write_tps=40.0),
        _record("ministral-3:14b", need_gb=9.9, write_tps=20.0),
    ]
    return summarise_tiers(sizes, [], {CARD_MACHINE: _machine(CARD_MACHINE, CARD_MB)})


def test_a_need_within_the_card_fits() -> None:
    """Given a model whose measured need stays within the share of the card the app gives it,
    When the rows of that machine are summarised,
    Then the model fits the 8 GB tier and the row is still to be measured by a run.
    """
    rows = {row.model: row for row in _card_rows()}

    row = rows["gemma4:e4b"]
    assert row.tier is HardwareProfile.CARD_8GB
    assert row.need_gb == 4.6
    assert row.fits is True
    assert row.measured is False
    assert row.seconds_per_page is not None


def test_a_need_beyond_the_card_does_not_fit() -> None:
    """Given a model whose measured need exceeds what the app puts on an 8 GB card,
    When the rows are summarised,
    Then it does not fit that tier.
    """
    rows = {row.model: row for row in _card_rows()}

    assert rows["ministral-3:14b"].fits is False


def test_a_card_tier_allows_the_share_the_app_gives_a_model() -> None:
    """Given the app's rule for a card tier,
    When the limit of the 8 GB tier is read,
    Then it is the card's usable share divided by the share a model may take.
    """
    assert limit_gb(HardwareProfile.CARD_8GB) == card_budget_gb(HardwareProfile.CARD_8GB) / 0.9
    assert limit_gb(HardwareProfile.CPU) == CPU_COMFORT_GB


def test_a_faster_model_gets_the_shorter_page() -> None:
    """Given two models with the same need on the same card, one writing twice as fast,
    When no run measured either of them,
    Then the faster one gets the shorter page time.
    """
    sizes = [
        _record("quick:1b", need_gb=2.0, write_tps=80.0),
        _record("slow:1b", need_gb=2.0, write_tps=40.0),
    ]
    rows = {
        row.model: row
        for row in summarise_tiers(sizes, [], {CARD_MACHINE: _machine(CARD_MACHINE, CARD_MB)})
    }

    quick = rows["quick:1b"].seconds_per_page
    slow = rows["slow:1b"].seconds_per_page
    assert quick is not None and slow is not None
    assert quick < slow


def test_a_full_exchange_gives_the_page_time_and_a_partial_one_does_not() -> None:
    """Given a run of all three steps for one model and one of two steps for another,
    When the rows are summarised,
    Then the first takes the run's page time and the second is not a page time at all.
    """
    sizes = [
        _record("gemma4:e4b", need_gb=4.6, write_tps=40.0),
        _record("qwen3:4b", need_gb=3.9, write_tps=40.0),
    ]
    performance = [
        _summary("gemma4:e4b", seconds_per_100_words=8.0),
        _summary("qwen3:4b", seconds_per_100_words=8.0, steps=(Step.CORRECT, Step.EXPLAIN)),
    ]
    rows = {
        row.model: row
        for row in summarise_tiers(
            sizes, performance, {CARD_MACHINE: _machine(CARD_MACHINE, CARD_MB)}
        )
    }

    assert rows["gemma4:e4b"].measured is True
    assert rows["gemma4:e4b"].seconds_per_page == 24.0
    assert rows["qwen3:4b"].measured is False


def test_a_processor_only_computer_is_judged_by_the_catalog_size() -> None:
    """Given a processor-only machine whose memory held more than the app's limit for a model,
    When the rows are summarised,
    Then the catalog's size decides, and a model it does not know gets no verdict.
    """
    sizes = [
        _record("gemma4:e4b", need_gb=9.0, write_tps=11.0, machine=CPU_MACHINE, device="cpu"),
        _record("qwen3.5:9b", need_gb=6.5, write_tps=5.7, machine=CPU_MACHINE, device="cpu"),
        _record("custom:7b", need_gb=1.0, write_tps=9.0, machine=CPU_MACHINE, device="cpu"),
    ]
    rows = {
        row.model: row
        for row in summarise_tiers(sizes, [], {CPU_MACHINE: _machine(CPU_MACHINE, 0)})
    }

    assert rows["gemma4:e4b"].tier is HardwareProfile.CPU
    need = rows["gemma4:e4b"].need_gb
    assert need is not None and need > CPU_COMFORT_GB
    assert rows["gemma4:e4b"].fits is True
    assert rows["qwen3.5:9b"].fits is False
    assert rows["custom:7b"].fits is None


def test_the_table_shows_the_tier_the_verdict_and_where_the_page_time_came_from() -> None:
    """Given one model that fits the card and one that does not,
    When the table is written,
    Then each block names its tier and its limit, and each row its verdict and source.
    """
    table = tier_table(_card_rows())

    assert "### about 8 GB — `card-machine` (gpu)" in table
    assert f"a model fits at {limit_gb(HardwareProfile.CARD_8GB):.1f} GB or less" in table
    assert "| `gemma4:e4b` | 4.6 GB | yes (4.6 GB of " in table
    assert "| **no** (9.9 GB of " in table
    assert "| computed |" in table


def test_the_table_prints_a_measured_page_time_as_a_run() -> None:
    """Given a full exchange on the machine,
    When the table is written,
    Then that row says the page time came from a run.
    """
    sizes = [_record("gemma4:e4b", need_gb=4.6, write_tps=40.0)]
    performance = [_summary("gemma4:e4b", seconds_per_100_words=8.0)]
    table = tier_table(
        summarise_tiers(sizes, performance, {CARD_MACHINE: _machine(CARD_MACHINE, CARD_MB)})
    )

    assert "| 24 s |" in table
    assert "| run |" in table


def test_the_table_lists_what_fits_before_the_rest() -> None:
    """Given a model that fits the card and a quicker one that does not,
    When the table is written,
    Then the one the tier runs comes first.
    """
    sizes = [
        _record("big:24b", need_gb=15.0, write_tps=60.0),
        _record("small:4b", need_gb=3.9, write_tps=20.0),
    ]
    table = tier_table(summarise_tiers(sizes, [], {CARD_MACHINE: _machine(CARD_MACHINE, CARD_MB)}))

    assert table.index("| `small:4b`") < table.index("| `big:24b`")


def test_the_table_says_how_the_verdict_and_the_page_time_are_read() -> None:
    """Given rows of a card tier and of a processor-only tier,
    When the table is written,
    Then the footer says where each verdict and each page time comes from.
    """
    sizes = [
        _record("gemma4:e4b", need_gb=4.6, write_tps=40.0),
        _record("qwen3:4b", need_gb=3.9, write_tps=11.0, machine=CPU_MACHINE, device="cpu"),
    ]
    table = tier_table(
        summarise_tiers(
            sizes,
            [],
            {CARD_MACHINE: _machine(CARD_MACHINE, CARD_MB), CPU_MACHINE: _machine(CPU_MACHINE, 0)},
        )
    )

    assert f"the app takes a model of at most {CPU_COMFORT_GB:.1f} GB" in table
    assert "300 words through the whole exchange" in table
    assert "`computed` the time the measured writing rate gives" in table


def test_no_records_means_no_table() -> None:
    """Given no size measured at all,
    When the table is written,
    Then it says there is nothing to read.
    """
    assert tier_table(summarise_tiers([], [], {})) == (
        "No size was measured: nothing to read for a tier."
    )
