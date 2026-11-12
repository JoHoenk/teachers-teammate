"""Unit tests for the memory agent: the readers, the ring buffer, the windows, the HTTP API."""

from __future__ import annotations

import ast
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import sys
import threading
import urllib.error
import urllib.request
from unittest.mock import patch

import pytest

from tools.model_eval import gpu_agent

_MIB = 1024 * 1024

_AMD_OUTPUT = """PID 4242
'CTRL' + 'C' to stop watching output:
timestamp,gpu,xcp,vram_used,vram_free,vram_total,vram_percent
1794248980,0,0,682,15622,16304,4.18

timestamp,gpu,xcp,vram_used,vram_free,vram_total,vram_percent
1794248981,0,0,5100,11204,16304,31.28
"""

_MEMINFO = """MemTotal:       32505856 kB
MemFree:         1234567 kB
MemAvailable:   29360128 kB
Buffers:          123456 kB
"""


# ── helpers ──────────────────────────────────────────────────────────────────


class _Clock:
    """A clock a test moves by hand."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def _agent(readings, *, clock=None, keep_s=600.0, cards=(), ram_total=None, sha="abc"):
    """Return an agent whose sample source walks *readings* and then repeats the last one."""
    values = list(readings)

    def read_sample():
        return values.pop(0) if len(values) > 1 else values[0]

    return gpu_agent.Agent(
        vendor="amd",
        source="sysfs",
        interval_s=0.2,
        keep_s=keep_s,
        cards=list(cards),
        ram_total_bytes=ram_total,
        read_sample=read_sample,
        clock=clock or _Clock(),
        agent_sha256=sha,
    )


@contextmanager
def _serving(agent, token=None):
    """Run *agent* on a free loopback port and yield its base URL."""
    server = gpu_agent.AgentServer(("127.0.0.1", 0), agent, token)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _request(url, *, method="GET", body=None, token=None):
    """Send one request and return ``(status, answer)``, reading an error's body too."""
    headers = {"Content-Type": "application/json"}
    if token is not None:
        headers["X-Token"] = token
    request = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=5) as answer:
            return answer.status, json.loads(answer.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read().decode("utf-8") or "{}")


# ── the file itself ──────────────────────────────────────────────────────────


@pytest.mark.use_case("Model_Eval_Gpu_Agent")
def test_the_agent_imports_only_the_standard_library() -> None:
    """
    Given  the agent's source
    When   its imports are read
    Then   every one of them is a standard-library module, so the file can be copied alone
    """
    tree = ast.parse(Path(gpu_agent.__file__).read_text(encoding="utf-8"))

    modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, "the agent must not import from this repository"
            modules.add((node.module or "").split(".")[0])

    assert modules <= set(sys.stdlib_module_names)


@pytest.mark.use_case("Model_Eval_Gpu_Agent")
def test_the_agent_names_its_own_file() -> None:
    """
    Given  the agent's file on disk
    When   it reports its own hash
    Then   the hash is the SHA-256 of that file, so a run can name the agent that measured
    """
    expected = hashlib.sha256(Path(gpu_agent.__file__).read_bytes()).hexdigest()

    assert gpu_agent.own_sha256() == expected


# ── the readers ──────────────────────────────────────────────────────────────


@pytest.mark.use_case("Model_Eval_Gpu_Agent")
def test_sysfs_gives_the_amd_card_in_bytes_and_skips_the_others(tmp_path: Path) -> None:
    """
    Given  a sysfs tree with an AMD card, an Intel card and a connector entry
    When   the reader walks it
    Then   only the AMD card comes out, with the byte figures the kernel wrote
    """
    amd = tmp_path / "card0" / "device"
    amd.mkdir(parents=True)
    (amd / "vendor").write_text("0x1002\n")
    (amd / "mem_info_vram_used").write_text("715128832\n")
    (amd / "mem_info_vram_total").write_text("17095983104\n")
    intel = tmp_path / "card1" / "device"
    intel.mkdir(parents=True)
    (intel / "vendor").write_text("0x8086\n")
    (intel / "mem_info_vram_used").write_text("1024\n")
    (intel / "mem_info_vram_total").write_text("2048\n")
    connector = tmp_path / "card0-DP-1" / "device"
    connector.mkdir(parents=True)
    (connector / "vendor").write_text("0x1002\n")
    (connector / "mem_info_vram_used").write_text("1\n")
    (connector / "mem_info_vram_total").write_text("2\n")

    cards = gpu_agent.read_sysfs_amd(tmp_path)

    assert cards == [gpu_agent.CardReading(0, "card0", 715128832, 17095983104)]


@pytest.mark.use_case("Model_Eval_Gpu_Agent")
def test_a_sysfs_card_that_cannot_be_read_is_skipped(tmp_path: Path) -> None:
    """
    Given  a sysfs tree whose card has no memory files
    When   the reader walks it
    Then   the card is left out instead of ending the reading
    """
    device = tmp_path / "card0" / "device"
    device.mkdir(parents=True)
    (device / "vendor").write_text("0x1002\n")

    assert gpu_agent.read_sysfs_amd(tmp_path) == []


@pytest.mark.use_case("Model_Eval_Gpu_Agent")
def test_amd_smi_is_read_by_column_name_from_the_last_sample() -> None:
    """
    Given  the lines amd-smi prints, with a header before every sample
    When   they are parsed
    Then   the last sample's card comes out, its MB read as mebibytes
    """
    cards = gpu_agent.parse_amd_smi(_AMD_OUTPUT)

    assert cards == [gpu_agent.CardReading(0, "AMD GPU 0", 5100 * _MIB, 16304 * _MIB)]


@pytest.mark.use_case("Model_Eval_Gpu_Agent")
def test_nvidia_smi_gives_one_card_per_line_of_the_last_tick() -> None:
    """
    Given  two ticks of nvidia-smi output for two cards
    When   they are parsed
    Then   the last tick's two cards come out, in MiB turned into bytes
    """
    text = (
        "0, NVIDIA GeForce RTX 3070 Laptop GPU, 100, 8192\n"
        "1, NVIDIA GeForce GTX 1050, 200, 4096\n"
        "0, NVIDIA GeForce RTX 3070 Laptop GPU, 6900, 8192\n"
        "1, NVIDIA GeForce GTX 1050, 300, 4096\n"
    )

    cards = gpu_agent.parse_nvidia_smi(text)

    assert cards == [
        gpu_agent.CardReading(0, "NVIDIA GeForce RTX 3070 Laptop GPU", 6900 * _MIB, 8192 * _MIB),
        gpu_agent.CardReading(1, "NVIDIA GeForce GTX 1050", 300 * _MIB, 4096 * _MIB),
    ]


@pytest.mark.use_case("Model_Eval_Gpu_Agent")
def test_meminfo_gives_the_available_and_the_total_memory_in_bytes() -> None:
    """
    Given  the text of /proc/meminfo
    When   it is parsed
    Then   MemAvailable and MemTotal come out in bytes
    """
    assert gpu_agent.parse_meminfo(_MEMINFO) == (29360128 * 1024, 32505856 * 1024)


@pytest.mark.use_case("Model_Eval_Gpu_Agent")
def test_meminfo_without_the_two_figures_gives_nothing() -> None:
    """
    Given  a /proc/meminfo text that lacks MemAvailable
    When   it is parsed
    Then   the reader says it has nothing rather than guessing
    """
    assert gpu_agent.parse_meminfo("MemTotal: 100 kB\n") is None


@pytest.mark.use_case("Model_Eval_Gpu_Agent")
def test_the_windows_reader_is_filled_through_a_fake_function() -> None:
    """
    Given  a filled MEMORYSTATUSEX structure, as the Windows API would fill it
    When   the reader is asked for the main memory
    Then   the available and the total figures come out in bytes
    """
    status = gpu_agent.MemoryStatusEx()
    status.ullAvailPhys = 5 * 1024**3
    status.ullTotalPhys = 8 * 1024**3

    assert gpu_agent.windows_memory(lambda: status) == (5 * 1024**3, 8 * 1024**3)
    assert gpu_agent.windows_memory(lambda: None) is None


# ── the ring buffer and the windows ──────────────────────────────────────────


@pytest.mark.use_case("Model_Eval_Gpu_Agent")
def test_the_buffer_drops_what_is_older_than_it_keeps() -> None:
    """
    Given  a buffer that keeps ten seconds
    When   samples at 0, 5 and 20 seconds are added
    Then   only the last one is left
    """
    clock = _Clock()
    buffer = gpu_agent.SampleBuffer(10.0, clock)

    for at_s in (0.0, 5.0, 20.0):
        buffer.add(gpu_agent.Sample(at_s, int(at_s), None))

    assert [sample.at_s for sample in buffer.between(0.0, 100.0)] == [20.0]


@pytest.mark.use_case("Model_Eval_Gpu_Agent")
def test_a_window_reports_the_peak_the_mean_and_the_minimum_of_the_card() -> None:
    """
    Given  an agent whose card reads 100, 300, 200 and 150 bytes across a window
    When   the window is opened, sampled and closed
    Then   the statistics cover the samples at both ends, and the main memory's fall too
    """
    clock = _Clock()
    agent = _agent([(100, 1000), (300, 900), (200, 800), (150, 700)], clock=clock)

    window = agent.open_window()["window"]
    for step in (1.0, 2.0):
        clock.now = step
        agent.take_sample()
    clock.now = 3.0
    stats = agent.close_window(window)

    assert stats["card"] == {
        "start_bytes": 100,
        "peak_bytes": 300,
        "mean_bytes": 187,
        "min_bytes": 100,
        "end_bytes": 150,
    }
    assert stats["ram"] == {
        "start_available_bytes": 1000,
        "min_available_bytes": 700,
        "end_available_bytes": 700,
    }
    assert stats["samples"] == 4
    assert stats["duration_s"] == 3.0


@pytest.mark.use_case("Model_Eval_Gpu_Agent")
def test_a_ninth_window_drops_the_oldest() -> None:
    """
    Given  an agent with eight windows open
    When   a ninth is opened
    Then   the first one is gone and the other eight still close
    """
    agent = _agent([(100, None)])

    windows = [agent.open_window()["window"] for _ in range(9)]

    assert agent.close_window(windows[0]) is None
    assert agent.close_window(windows[1]) is not None


@pytest.mark.use_case("Model_Eval_Gpu_Agent")
def test_a_window_older_than_an_hour_is_discarded() -> None:
    """
    Given  a window opened an hour and a second ago and never closed
    When   another window is opened
    Then   the forgotten one is gone
    """
    clock = _Clock()
    agent = _agent([(100, None)], clock=clock)
    forgotten = agent.open_window()["window"]

    clock.now = gpu_agent.WINDOW_MAX_AGE_S + 1.0
    agent.open_window()

    assert agent.close_window(forgotten) is None


@pytest.mark.use_case("Model_Eval_Gpu_Agent")
def test_the_info_lists_every_card_and_the_last_reading() -> None:
    """
    Given  an agent with two cards and one sample
    When   it is asked what it is
    Then   the cards, their sum, the main memory and the reading come out
    """
    cards = [
        gpu_agent.CardReading(0, "card0", 100, 1000),
        gpu_agent.CardReading(1, "card1", 200, 2000),
    ]
    agent = _agent([(300, 4000)], cards=cards, ram_total=8000)
    agent.take_sample()

    info = agent.info()

    assert info["agent"] == gpu_agent.AGENT_VERSION
    assert info["vendor"] == "amd"
    assert info["source"] == "sysfs"
    assert info["cards"] == [
        {"index": 0, "name": "card0", "total_bytes": 1000},
        {"index": 1, "name": "card1", "total_bytes": 2000},
    ]
    assert info["card_total_bytes"] == 3000
    assert info["ram_total_bytes"] == 8000
    assert info["now"] == {"card_used_bytes": 300, "ram_available_bytes": 4000}


# ── the HTTP interface ───────────────────────────────────────────────────────


@pytest.mark.use_case("Model_Eval_Gpu_Agent")
def test_a_request_without_the_token_is_refused() -> None:
    """
    Given  an agent started with a token
    When   a request arrives without it, and one with a wrong one
    Then   both are refused with 401
    """
    with _serving(_agent([(100, None)]), token="secret") as url:
        missing = _request(f"{url}/info")
        wrong = _request(f"{url}/info", token="nope")
        right = _request(f"{url}/info", token="secret")

    assert missing[0] == 401
    assert wrong[0] == 401
    assert right[0] == 200


@pytest.mark.use_case("Model_Eval_Gpu_Agent")
def test_an_agent_without_a_sample_says_so() -> None:
    """
    Given  an agent that was never started, so it has no sample
    When   a window is opened
    Then   the answer is 503, not a window on nothing
    """
    with _serving(_agent([(100, None)])) as url:
        status, answer = _request(f"{url}/window", method="POST", body=b"")

    assert status == 503
    assert answer["error"] == "no samples"


@pytest.mark.use_case("Model_Eval_Gpu_Agent")
def test_a_body_over_a_kilobyte_is_refused() -> None:
    """
    Given  an agent that reads no body
    When   a request carries more than a kilobyte
    Then   it is refused with 413
    """
    agent = _agent([(100, None)])
    agent.take_sample()

    with _serving(agent) as url:
        status, _ = _request(f"{url}/window", method="POST", body=b"x" * 2000)

    assert status == 413


@pytest.mark.use_case("Model_Eval_Gpu_Agent")
def test_another_method_on_a_known_path_is_refused() -> None:
    """
    Given  an agent with the two paths of the API
    When   a request uses a method the path does not take
    Then   it is refused with 405
    """
    agent = _agent([(100, None)])
    agent.take_sample()

    with _serving(agent) as url:
        on_info = _request(f"{url}/info", method="PUT")
        on_window = _request(f"{url}/window", method="GET")

    assert on_info[0] == 405
    assert on_window[0] == 405


@pytest.mark.use_case("Model_Eval_Gpu_Agent")
def test_an_unknown_path_and_a_window_closed_twice_are_not_found() -> None:
    """
    Given  an agent with one window open
    When   an unknown path is asked for, and the window is closed twice
    Then   both answers are 404
    """
    agent = _agent([(100, None)])
    agent.take_sample()

    with _serving(agent) as url:
        unknown = _request(f"{url}/nothing")
        window = _request(f"{url}/window", method="POST", body=b"")[1]["window"]
        first = _request(f"{url}/window/{window}/close", method="POST", body=b"")
        second = _request(f"{url}/window/{window}/close", method="POST", body=b"")

    assert unknown[0] == 404
    assert first[0] == 200
    assert second[0] == 404


@pytest.mark.use_case("Model_Eval_Gpu_Agent")
def test_the_info_carries_the_hash_of_the_agent_file() -> None:
    """
    Given  an agent that knows its own hash
    When   /info is read
    Then   the hash is there, so a run can name the agent that measured
    """
    agent = _agent([(100, None)], sha="deadbeef")
    agent.take_sample()

    with _serving(agent) as url:
        status, answer = _request(f"{url}/info")

    assert status == 200
    assert answer["agent_sha256"] == "deadbeef"


# ── choosing what to read ────────────────────────────────────────────────────


@pytest.mark.use_case("Model_Eval_Gpu_Agent")
def test_auto_picks_the_amd_card_in_sysfs(tmp_path: Path) -> None:
    """
    Given  a machine with an AMD card in sysfs and nvidia-smi on the PATH
    When   the vendor and the source are chosen automatically
    Then   the card is read through sysfs
    """
    device = tmp_path / "card0" / "device"
    device.mkdir(parents=True)
    (device / "vendor").write_text("0x1002\n")
    (device / "mem_info_vram_used").write_text("1\n")
    (device / "mem_info_vram_total").write_text("2\n")

    chosen = gpu_agent.choose_vendor_and_source(
        "auto", "auto", sysfs_root=tmp_path, which=lambda name: f"/usr/bin/{name}", system="Linux"
    )

    assert chosen == ("amd", "sysfs")


@pytest.mark.use_case("Model_Eval_Gpu_Agent")
def test_auto_falls_back_to_nvidia_smi(tmp_path: Path) -> None:
    """
    Given  a machine without an AMD card in sysfs but with nvidia-smi
    When   the vendor and the source are chosen automatically
    Then   the card is read through the tool
    """
    chosen = gpu_agent.choose_vendor_and_source(
        "auto",
        "auto",
        sysfs_root=tmp_path,
        which=lambda name: "/usr/bin/nvidia-smi" if name == "nvidia-smi" else None,
        system="Linux",
    )

    assert chosen == ("nvidia", "tool")


@pytest.mark.use_case("Model_Eval_Gpu_Agent")
def test_a_machine_without_a_supported_card_stops_with_exit_2(tmp_path: Path) -> None:
    """
    Given  a machine with neither an AMD card in sysfs nor a tool on the PATH
    When   the agent is started
    Then   it says so and exits with 2
    """
    with (
        patch.object(gpu_agent, "read_sysfs_amd", return_value=[]),
        patch.object(gpu_agent.shutil, "which", return_value=None),
        patch.object(gpu_agent.platform, "system", return_value="Linux"),
    ):
        with pytest.raises(gpu_agent.AgentSetupError):
            gpu_agent.choose_vendor_and_source("auto", "auto")
        assert gpu_agent.main([]) == 2


@pytest.mark.use_case("Model_Eval_Gpu_Agent")
def test_amd_on_windows_is_refused_with_a_clear_message() -> None:
    """
    Given  a Windows machine
    When   the AMD vendor is asked for
    Then   the agent refuses, because neither sysfs nor amd-smi is there
    """
    with pytest.raises(gpu_agent.AgentSetupError, match="no AMD source on Windows"):
        gpu_agent.choose_vendor_and_source(
            "amd",
            "auto",
            sysfs_root=Path("/nonexistent"),
            which=lambda name: None,
            system="Windows",
        )


@pytest.mark.use_case("Model_Eval_Gpu_Agent")
def test_the_tool_command_asks_for_the_interval_it_was_given() -> None:
    """
    Given  an interval of a fifth of a second
    When   the command for each vendor is built
    Then   nvidia-smi is asked for 200 ms and amd-smi streams once a second
    """
    assert gpu_agent.tool_command("nvidia", 0.2)[-1] == "-lms=200"
    assert gpu_agent.tool_command("amd", 0.2) == ["amd-smi", "monitor", "-v", "--csv", "-w", "1"]


@pytest.mark.use_case("Model_Eval_Gpu_Agent")
def test_the_token_is_read_once_from_the_file(tmp_path: Path) -> None:
    """
    Given  a token file with a trailing newline
    When   the agent reads its token
    Then   the file's content comes back without the newline
    """
    path = tmp_path / "token"
    path.write_text("s3cret\n")
    options = gpu_agent._parse_args(["--token-file", str(path)])

    assert gpu_agent.read_token(options) == "s3cret"


@pytest.mark.use_case("Model_Eval_Gpu_Agent")
def test_a_missing_token_file_stops_with_exit_2(tmp_path: Path) -> None:
    """
    Given  a token file that is not there
    When   the agent is started
    Then   it says so and exits with 2
    """
    assert gpu_agent.main(["--token-file", str(tmp_path / "absent")]) == 2


@pytest.mark.use_case("Model_Eval_Gpu_Agent")
def test_the_cards_of_a_tool_are_listed_as_they_arrive() -> None:
    """
    Given  an agent whose cards come from a tool that had not printed when the agent was built
    When   the tool delivers its first reading
    Then   the info lists the card and its total, instead of the empty list of the start
    """
    arrived: list[gpu_agent.CardReading] = []
    agent = gpu_agent.Agent(
        vendor="nvidia",
        source="tool",
        interval_s=0.2,
        keep_s=600.0,
        read_cards=lambda: arrived,
        ram_total_bytes=None,
        read_sample=lambda: (0, None),
        clock=_Clock(),
    )
    agent.take_sample()
    assert agent.info()["cards"] == []

    arrived.append(gpu_agent.CardReading(0, "RTX 3070 Laptop", 700 * _MIB, 8192 * _MIB))

    info = agent.info()
    assert [card["name"] for card in info["cards"]] == ["RTX 3070 Laptop"]
    assert info["card_total_bytes"] == 8192 * _MIB


@pytest.mark.use_case("Model_Eval_Gpu_Agent")
def test_a_tool_stream_says_when_its_first_reading_has_arrived() -> None:
    """
    Given  a tool stream that has read nothing
    When   a line of the tool's output arrives
    Then   waiting for the first reading ends, and the cards are there
    """
    stream = gpu_agent.ToolStream(["nvidia-smi"], gpu_agent.parse_nvidia_smi)
    assert stream.wait_for_cards(0.0) is False

    stream._feed("0, RTX 3070 Laptop, 700, 8192\n")

    assert stream.wait_for_cards(0.0) is True
    assert stream.cards()[0].total_bytes == 8192 * _MIB


@pytest.mark.use_case("Model_Eval_Gpu_Agent")
def test_a_tool_that_prints_nothing_stops_the_agent_with_a_message() -> None:
    """
    Given  a machine whose tool never prints a reading
    When   the agent is built
    Then   it stops with a setup error rather than report a card of zero bytes
    """
    options = gpu_agent._parse_args(["--vendor", "nvidia", "--source", "tool"])
    with (
        patch.object(gpu_agent, "choose_vendor_and_source", return_value=("nvidia", "tool")),
        patch.object(gpu_agent, "TOOL_FIRST_READING_S", 0.0),
        patch.object(gpu_agent.ToolStream, "start"),
        pytest.raises(gpu_agent.AgentSetupError),
    ):
        gpu_agent.build_agent(options)


@pytest.mark.use_case("Model_Eval_Gpu_Agent")
def test_an_answer_to_head_has_no_body() -> None:
    """
    Given  an agent that is serving
    When   a client sends HEAD
    Then   the answer carries the headers only
    """
    agent = _agent([(100, None)])
    agent.take_sample()

    with _serving(agent) as url:
        request = urllib.request.Request(f"{url}/info", method="HEAD")
        with pytest.raises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(request, timeout=5)

    assert caught.value.code == 405
    assert caught.value.read() == b""
