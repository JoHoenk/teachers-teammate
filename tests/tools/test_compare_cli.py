"""The command line of ``compare_writing_models``: the arguments, the refusals, one resume.
The run loop itself is tested in ``test_runner.py``; what is left here is what belongs to the
command line: the parsed arguments become the right :class:`~tools.model_eval.runner.Matrix`, a
protocol that does not allow a run exits with code 2 before anything is sent, a refused plan does
too, and a run whose sessions agree does go on.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from teachers_teammate.exceptions import OCRError
from teachers_teammate.infrastructure.ollama_utils import PROOFREADING_NUM_CTX
from tools import compare_writing_models
from tools.model_eval import agent_client, measure, protocol, results, runner, session
from tools.model_eval.answer_mode import AnswerMode

MODEL = "gemma4:e2b"
MACHINE = "rx9070xt-server"
DIGEST = "sha256:0123456789abcdef0123456789abcdef"


class _Probe:
    """A server that answers without a server."""

    def __init__(self, url: str) -> None:
        self.url = url

    def version(self) -> str:
        """Return the version of the invented server."""
        return "0.40.13"

    def model_info(self, tag: str) -> dict:
        """Return the digest and details of the invented model."""
        return {
            "digest": DIGEST,
            "quantization": "Q4_K_M",
            "parameter_size": "2B",
            "family": "gemma3",
        }

    def loaded(self) -> tuple:
        """Return what the invented server holds in memory: nothing."""
        return ()


class _BrokenChat:
    """A server whose requests do not come back, as a model that is not pulled does."""

    def __init__(self, url: str, *, num_ctx: int = 0) -> None:
        self.url = url
        self.num_ctx = num_ctx

    def chat_with_stats(self, *args, **kwargs):
        """Fail the request."""
        raise OCRError("the request did not come back")


def _no_request(*args, **kwargs):
    """Fail the test: a refused run must not send a request."""
    raise AssertionError("a refused run must not send a request")


def _args(tmp_path: Path) -> argparse.Namespace:
    """Return the arguments of a ``run`` over two invented texts."""
    testset = tmp_path / "set"
    testset.mkdir()
    records = [
        results.TestRecord(
            id=name,
            language="de",
            kind="errors",
            origin="invented",
            source="Der Hund laufen schnell.",
            reference="Der Hund läuft schnell.",
        )
        for name in ("de-01", "de-02")
    ]
    (testset / "de.jsonl").write_text(
        "".join(json.dumps(r.to_dict(), ensure_ascii=False) + "\n" for r in records),
        encoding="utf-8",
    )
    return argparse.Namespace(
        out=tmp_path / "out",
        testset=testset,
        language=["de"],
        model=[MODEL],
        temperature=[0.2],
        repeat=1,
        no_seed=False,
        seed=20261109,
        timeout=5.0,
        isolate=False,
        gpu_sampler=None,
        gpu_ssh="",
        gpu_agent=None,
        gpu_token_file=None,
        machine=MACHINE,
        ollama_url="http://localhost:11434",
        mode=[AnswerMode.REVIEW],
        compact_json="catalog",
        no_smoke=True,
        progress="off",
    )


def _identity(args: argparse.Namespace, model: str = MODEL, **overrides) -> dict:
    """Return the identity of the run ``_args`` describes, as ``run`` builds it."""
    records = results.read_testset(args.testset, args.language)
    identity = measure.run_identity(
        stamp=protocol.current_stamp(),
        probe=_Probe(""),
        model=model,
        mode=AnswerMode.REVIEW,
        languages=args.language,
        temperature=0.2,
        request_profile={"compact_json": False, "temperature": 0.2},
        num_ctx=PROOFREADING_NUM_CTX,
        timeout=args.timeout,
        seed=20261109,
        repeat=1,
        isolate=False,
        sampler_vendor="",
        machine=MACHINE,
        testset=args.testset.name,
        testset_sha256=results.records_sha256(records),
        texts=len(records),
    )
    identity.update(overrides)
    return identity


def _write_session(args: argparse.Namespace, path: Path, **overrides) -> None:
    """Store one earlier session next to the results file *path*."""
    header = measure.session_header(
        _identity(args, **overrides),
        preparation=measure.Preparation(loaded_before=(), vram_baseline_mb=None),
        revision="cfdc0ea",
        now="2026-11-09T18:00:00",
    )
    path.with_suffix(".header.json").write_text(json.dumps([header]), encoding="utf-8")


def _results_file(args: argparse.Namespace, model: str = MODEL, *, rows: bool = False) -> Path:
    """Create the results file of *model*, with one stored row when *rows*, and return it."""
    args.out.mkdir(exist_ok=True)
    path = args.out / results.result_file_name(model, 0.2)
    path.write_text("", encoding="utf-8")
    if rows:
        row = results.ResultRow(
            id="de/de-01",
            language="de",
            kind="errors",
            origin="invented",
            model=model,
            temperature=0.2,
            seconds=1.0,
            failed=False,
            fallback=False,
            has_review=False,
            source="Der Hund laufen schnell.",
            reference="Der Hund läuft schnell.",
            output="Der Hund läuft schnell.",
        )
        results.write_rows(path, [row])
    return path


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    """Give every run an invented server and revision, and forbid a real request."""
    monkeypatch.setattr(measure, "ServerProbe", _Probe)
    monkeypatch.setattr(measure, "tool_revision", lambda: "cfdc0ea")
    monkeypatch.setattr(measure, "RecordingClient", _no_request)


def test_the_arguments_become_the_matrix(tmp_path, monkeypatch):
    """
    Given  a command line with two modes, two temperatures and --no-seed
    When   it is parsed
    Then   the matrix holds the parsed modes and no base seed, and the rest as given
    """
    seen: list[runner.Matrix] = []

    def _capture(matrix, *args, **kwargs) -> runner.RunResult:
        seen.append(matrix)
        return runner.RunResult(refused=(), sessions=0, rows=0)

    monkeypatch.setattr(runner, "run_matrix", _capture)
    args = _args(tmp_path)
    args.mode = [AnswerMode.REVIEW, AnswerMode.TEXT]
    args.temperature = [0.2, 0.7]
    args.no_seed = True

    assert compare_writing_models._run(args) == 0

    assert seen == [
        runner.Matrix(
            out=args.out,
            testset=args.testset,
            languages=("de",),
            models=(MODEL,),
            temperatures=(0.2, 0.7),
            modes=(AnswerMode.REVIEW, AnswerMode.TEXT),
            repeats=1,
            base_seed=None,
            timeout=5.0,
            isolate=False,
            gpu_sampler=None,
            machine=MACHINE,
            compact_json="catalog",
        )
    ]


def _write_language(testset: Path, language: str) -> None:
    """Write one record of *language* into the set, as the builder does."""
    record = results.TestRecord(
        id=f"{language}-01",
        language=language,
        kind="errors",
        origin="invented",
        source="Der Hund laufen schnell.",
        reference="Der Hund läuft schnell.",
    )
    (testset / f"{language}.jsonl").write_text(
        json.dumps(record.to_dict(), ensure_ascii=False) + "\n", encoding="utf-8"
    )


def test_the_languages_default_to_the_files_of_the_set(tmp_path, monkeypatch):
    """
    Given  a set that holds de.jsonl and en.jsonl and no fr.jsonl, and no --language
    When   the run is planned
    Then   it plans German and English, instead of failing on a file the set does not have
    """
    seen: list[runner.Matrix] = []

    def _capture(matrix, *args, **kwargs) -> runner.RunResult:
        seen.append(matrix)
        return runner.RunResult(refused=(), sessions=0, rows=0)

    monkeypatch.setattr(runner, "run_matrix", _capture)
    args = _args(tmp_path)
    _write_language(args.testset, "en")
    args.language = None

    assert compare_writing_models._run(args) == 0

    assert seen[0].languages == ("de", "en")


def test_a_language_the_set_does_not_hold_is_refused_before_any_request(tmp_path, capsys):
    """
    Given  a --language the set has no file for
    When   the run is started
    Then   it exits with code 2 before the first request and says which languages the set has
    """
    args = _args(tmp_path)
    args.language = ["fr"]

    assert compare_writing_models._run(args) == 2

    assert "the set has de, not fr" in capsys.readouterr().err
    assert not args.out.exists(), "the run stopped before it wrote anything"


def test_run_refuses_to_start_when_the_request_side_changed(tmp_path, monkeypatch, capsys):
    """
    Given  a prompt, answer format or window that changed since the current eval version
    When   a run is started
    Then   it exits with code 2 before the first request and says what to do about it
    """
    monkeypatch.setattr(protocol, "fingerprint_sha256", lambda: "0" * 64)
    args = _args(tmp_path)

    assert compare_writing_models._run(args) == 2

    assert "add a version to protocol.json" in capsys.readouterr().err
    assert not args.out.exists(), "the run stopped before it wrote anything"


def test_run_refuses_a_version_file_that_cannot_be_read(tmp_path, monkeypatch, capsys):
    """
    Given  a version file that is not JSON
    When   a run is started
    Then   it exits with code 2 and names the file, instead of ending in a traceback
    """
    broken = tmp_path / "protocol.json"
    broken.write_text("{", encoding="utf-8")
    monkeypatch.setattr(protocol, "PROTOCOL_PATH", broken)
    args = _args(tmp_path)

    assert compare_writing_models._run(args) == 2

    assert "cannot be read" in capsys.readouterr().err
    assert not args.out.exists()


def test_run_refuses_to_resume_into_a_run_of_another_model(tmp_path, capsys):
    """
    Given  a results file whose last session was made with another model digest
    When   the run is started again
    Then   it exits with code 2 before the first request, names the conflict and says which files
           to delete
    """
    args = _args(tmp_path)
    path = _results_file(args, rows=True)
    _write_session(args, path, model_info={"digest": "sha256:" + "f" * 32})

    assert compare_writing_models._run(args) == 2

    err = capsys.readouterr().err
    assert "model_info.digest" in err
    assert f"delete {path}" in err


def test_run_refuses_results_without_a_header(tmp_path, capsys):
    """
    Given  a results file with a result in it and no header file
    When   the run is started again
    Then   it exits with code 2 before the first request: nobody can say which session made them
    """
    args = _args(tmp_path)
    path = _results_file(args, rows=True)

    assert compare_writing_models._run(args) == 2

    err = capsys.readouterr().err
    assert "1 results but no" in err
    assert f"delete {path}" in err


def test_run_refuses_a_test_set_rebuilt_under_the_same_name(tmp_path, capsys):
    """
    Given  a results file whose session saw other texts under the same test-set name
    When   the run is started again
    Then   it exits with code 2 and names the test-set hash
    """
    args = _args(tmp_path)
    path = _results_file(args, rows=True)
    _write_session(args, path, testset_sha256="e" * 64)

    assert compare_writing_models._run(args) == 2

    assert "testset_sha256" in capsys.readouterr().err


class _StubMeasured:
    """A measured run without a server: nothing is loaded, nothing is sampled."""

    def __init__(self, **kwargs) -> None:
        self.finished = False

    def prepare(self, warm_up, *, isolate):
        """Return a preparation without sending the warm-up."""
        return measure.Preparation(loaded_before=(), vram_baseline_mb=None)

    def begin(self) -> None:
        """Note nothing."""

    def end(self):
        """Report no performance."""
        return None

    def finish(self, *, unload):
        """Remember that the run was finished."""
        self.finished = True


def test_a_resume_with_the_same_settings_goes_on(tmp_path, monkeypatch):
    """
    Given  a results file with one of two texts and a header written by an earlier session with
           the same settings
    When   the run is started again
    Then   it resumes: a second session is appended and the missing text is written
    """
    from teachers_teammate.domain.correction_review import CorrectionOutcome  # noqa: PLC0415
    from teachers_teammate.interfaces import Corrector  # noqa: PLC0415

    class _Echo(Corrector):
        def correct(self, raw_text, language, *, text_format="plain"):
            return CorrectionOutcome(text="Der Hund läuft schnell.")

    monkeypatch.setattr(measure, "MeasuredRun", _StubMeasured)
    monkeypatch.setattr(measure, "RecordingClient", lambda url, seed=None: object())
    monkeypatch.setattr(
        compare_writing_models, "app_corrector_factory", lambda *a, **k: lambda m, t: _Echo()
    )
    args = _args(tmp_path)
    path = _results_file(args, rows=True)
    _write_session(args, path)

    assert compare_writing_models._run(args) == 0

    assert len(session.read_sessions(path.with_suffix(".header.json"))) == 2
    assert [row.id for row in results.read_rows(path)] == ["de/de-01", "de/de-02"]


def test_a_failed_smoke_skips_the_sessions_of_that_setup(tmp_path, monkeypatch, capsys):
    """
    Given  a setup whose smoke check fails, and the same run with ``--no-smoke``
    When   the run starts
    Then   the sessions of that setup are skipped and nothing is written, and without the smoke
           check they are run
    """
    monkeypatch.setattr(compare_writing_models, "OllamaChatPort", _BrokenChat)
    args = _args(tmp_path)
    args.no_smoke = False

    assert compare_writing_models._run(args) == 0

    out = capsys.readouterr().out
    assert f"smoke {MODEL} (plain, T=0.2, 8192): FAIL" in out
    assert f"review sessions of {MODEL} are skipped" in out
    assert not (args.out / results.result_file_name(MODEL, 0.2)).exists()


class _FakeAgent:
    """An AgentClient stand-in that answers ``info()`` with a card in use, or fails."""

    def __init__(self, url, *, token=None, fail=False):
        self.url = url
        self.token = token
        self._fail = fail

    def info(self):
        if self._fail:
            raise agent_client.AgentError("connection refused")
        return SimpleNamespace(vendor="amd", source="sysfs", agent_sha256="ab" * 32)


@pytest.mark.use_case("Model_Eval_Measure")
def test_a_memory_agent_is_recorded_in_the_matrix_and_read_with_its_token(tmp_path, monkeypatch):
    """
    Given  a command line with --gpu-agent and a token file
    When   the run starts
    Then   the agent is asked for its identity with the token, and the matrix carries a record of
           the agent as the sampler, so the run counts as sampled without any shell access
    """
    seen: list[runner.Matrix] = []

    def _capture(matrix, *args, **kwargs) -> runner.RunResult:
        seen.append(matrix)
        return runner.RunResult(refused=(), sessions=0, rows=0)

    clients: list[_FakeAgent] = []

    def _client(url, *, token=None):
        clients.append(_FakeAgent(url, token=token))
        return clients[-1]

    monkeypatch.setattr(runner, "run_matrix", _capture)
    monkeypatch.setattr(agent_client, "AgentClient", _client)
    token_file = tmp_path / "token"
    token_file.write_text("secret\n", encoding="utf-8")
    args = _args(tmp_path)
    args.gpu_agent = "http://server:11436"
    args.gpu_token_file = token_file

    assert compare_writing_models._run(args) == 0

    assert clients[0].token == "secret"
    assert seen[0].gpu_sampler == {
        "kind": "agent",
        "vendor": "amd",
        "source": "sysfs",
        "agent_sha256": "ab" * 32,
    }


@pytest.mark.use_case("Model_Eval_Measure")
def test_a_memory_agent_that_cannot_be_asked_stops_the_run(tmp_path, monkeypatch, capsys):
    """
    Given  --gpu-agent naming an agent that does not answer
    When   the run starts
    Then   it is refused with exit code 2 before any text is sent, and the reason is printed
    """
    monkeypatch.setattr(
        agent_client, "AgentClient", lambda url, *, token=None: _FakeAgent(url, fail=True)
    )
    monkeypatch.setattr(runner, "run_matrix", lambda *a, **k: pytest.fail("a run was started"))
    args = _args(tmp_path)
    args.gpu_agent = "http://server:11436"

    assert compare_writing_models._run(args) == 2

    assert "cannot be used" in capsys.readouterr().err


@pytest.mark.use_case("Model_Eval_Measure")
def test_the_agent_and_the_shell_sampler_are_not_combined(tmp_path, monkeypatch, capsys):
    """
    Given  both --gpu-agent and --gpu-sampler
    When   the run starts
    Then   it is refused with exit code 2, naming the two ways to read the card
    """
    monkeypatch.setattr(runner, "run_matrix", lambda *a, **k: pytest.fail("a run was started"))
    args = _args(tmp_path)
    args.gpu_agent = "http://server:11436"
    args.gpu_sampler = "amd"

    assert compare_writing_models._run(args) == 2

    assert "pass one" in capsys.readouterr().err
