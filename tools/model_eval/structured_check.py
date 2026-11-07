"""The structured-output check: does a model answer the review format reliably?

This is what decides ``OllamaModelSpec.structured_output`` in the catalog.  It used to live inside
``tools/verify_model_catalog.py``, where it built the prompt, checked the size, called the server,
validated, scored and printed in one function and could not be tested without Ollama.  The server
is now behind :class:`~.ports.ChatPort`, the fixtures are data, and the rule that turns runs into
a verdict is :class:`~.check_rule.CheckRule`.

Five fixtures cover what the answer format has to handle: plain English, plain German, plain
French, Markdown with formatting marks, and one full page of dense German prose.  The full page is
the one that shows a context window that is too small: German tokenises longest of the three
languages, so it is the worst case for the size the app sends.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
import time

from teachers_teammate.domain.formatted_text import TextFormat

from .check_rule import CheckRule, CheckRun, Verdict
from .ports import ChatPort

RUNS_AT_TEMPERATURE = 3
"""How often each fixture runs at a temperature above 0: a check that passes once says little."""

FULL_PAGE_DE = """Am letzten Wochenende ich bin mit meine Klasse auf ein Ausflug in die Berge gefahren. Wir haben uns um sieben Uhr am Bahnhof getroffen und der Lehrer hat die Fahrkarten für alle gekauft. Weil wir haben lange warten müssen, war der Zug schon voll und wir mussten stehen. Nach zwei Stunden wir sind endlich angekommen und die Sonne hat geschienen, obwohl der Wetterbericht hatte Regen angesagt.

Zuerst sind wir ein steilen Weg hinauf gegangen, wo man aufpassen muss, weil die Steine locker liegen. Meine Freundin ist hingefallen und hat sich das Knie verletzt, aber sie hat gesagt, das tut nicht so weh. Auf dem Gipfel war es windig und wir haben unsere Jacken angezogen. Von oben wir konnten das ganze Tal sehen und die kleinen Häuser sahen wie Spielzeug aus. Ein Mitschüler hat ein Fernrohr mitgebracht und wir haben die Vögel beobachtet.

Zum Mittagessen wir hatten Brote und Äpfel dabei, aber die Butter war in der Sonne weich geworden. Danach haben wir ein Spiel gespielt, bei dem man sich die Namen merken muss. Es hat viel Spass gemacht und alle haben gelacht. Der Lehrer hat gesagt, dass wir leise sein sollen, damit die anderen Wanderer nicht gestört werden.

Auf dem Rückweg hat es angefangen zu regnen und wir sind schnell gelaufen. Weil der Weg nass war, ist eine Mädchen ausgerutscht, aber es ist nichts passiert. Im Zug haben wir müde aus dem Fenster geschaut und manche sind eingeschlafen. Als wir zu Hause angekommen sind, hat meine Mutter gefragt, wie es war, und ich habe gesagt, es war ein sehr schöner Tag. Am nächsten Tag ich hatte Muskelkater, aber das war es wert.

In der Schule haben wir danach über den Ausflug gesprochen und jeder hat einen kleinen Text darüber geschrieben. Mir hat am besten gefallen, wie wir zusammen den steilen Weg geschafft haben, weil keiner aufgegeben hat. Nächstes Jahr ich möchte wieder mitkommen, vielleicht sogar im Winter, wenn Schnee liegt. Der Lehrer hat gesagt, dass er uns dann früher Bescheid gibt, damit alle planen können.
"""


@dataclass(frozen=True)
class Fixture:
    """One text the check sends, with the language and notation it needs."""

    name: str
    language: str
    text: str
    text_format: TextFormat = "plain"


FIXTURES: tuple[Fixture, ...] = (
    Fixture(
        name="plain EN",
        language="english",
        text="Yesterday she have gone to the cinema with his friend. "
        "It was very nice because they was there for the first time.",
    ),
    Fixture(
        name="plain DE",
        language="german",
        text="Gestern hat sie ins Kino gegangen mit sein Freundin. "
        "Es war sehr schön weil sie war dort zum ersten Mal.",
    ),
    Fixture(
        name="plain FR",
        language="french",
        text="Hier elle a aller au cinéma avec son ami. "
        "C'était très bien parce que ils était là pour la première fois.",
    ),
    Fixture(
        name="Markdown",
        language="english",
        text="Dear **diary**, my ~~backpack~~ passport and me went to school.\n\n"
        "- it was *very* nice\n- we have see a film",
        text_format="markdown",
    ),
    Fixture(name="full page DE", language="german", text=FULL_PAGE_DE),
)


def review_request(language: str, text_format: TextFormat, text: str) -> tuple[str, str]:
    """Return ``(system, user)`` of the request the app's corrector would send for *text*."""
    from teachers_teammate.infrastructure.correction import (  # noqa: PLC0415
        _human_message,
        _instructions,
    )

    system = _instructions("", language, level="B2", text_format=text_format, mode="review")
    return system, _human_message(text, language, language, "review")


def placeable(answer_json: str, source: str) -> tuple[int, int, str] | None:
    """Return ``(placed, total, corrected)`` for an answer; ``None`` if it does not validate."""
    from teachers_teammate.infrastructure.correction_schema import (  # noqa: PLC0415
        to_review,
        validate_answer,
    )

    answer, _error = validate_answer(answer_json)
    if answer is None:
        return None
    review = to_review(answer)
    total = len(review.sentences)
    if not total:
        return 0, 0, source
    from teachers_teammate.domain.correction_review import splice  # noqa: PLC0415

    corrected, unplaced = splice(source, review.sentences)
    return total - len(unplaced), total, corrected


def seed_settings(port: ChatPort, tag: str) -> str:
    """Return the sampling settings *tag* ships with, as one line for the report.

    ``temperature`` and ``num_ctx`` are left out: the app overrides both on every request.
    """
    import json  # noqa: PLC0415
    import urllib.request  # noqa: PLC0415

    client = getattr(port, "client", None)
    url = getattr(client, "_url", None) or "http://127.0.0.1:11434"
    request = urllib.request.Request(
        f"{str(url).rstrip('/')}/api/show",
        data=json.dumps({"model": tag}).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            info = json.load(response)
    except Exception as exc:  # noqa: BLE001  # the line is advisory; the check reports the rest
        return f"settings unknown ({exc})"
    shipped = [
        " ".join(line.split())
        for line in (info.get("parameters") or "").splitlines()
        if line.split()[:1] not in (["temperature"], ["num_ctx"], ["stop"])
    ]
    quant = (info.get("details") or {}).get("quantization_level", "?")
    return f"quantisation {quant}; own settings: {'; '.join(shipped) or 'none'}"


class StructuredOutputCheck:
    """Run the review answer format against every fixture and decide the catalog verdict."""

    def __init__(self, chat: ChatPort, rule: CheckRule | None = None, timeout: float = 300.0):
        self._chat = chat
        self._rule = rule or CheckRule()
        self._timeout = timeout

    @property
    def rule(self) -> CheckRule:
        """Return the rule a model has to pass."""
        return self._rule

    def run(self, tag: str, temperature: float, on_run=None) -> list[CheckRun]:
        """Send every fixture to *tag* and return what each request produced."""
        runs = RUNS_AT_TEMPERATURE if temperature > 0 else 1
        results: list[CheckRun] = []
        for fixture in FIXTURES:
            for number in range(1, runs + 1):
                run = self._run_once(tag, fixture, temperature)
                results.append(run)
                if on_run is not None:
                    on_run(run, number, runs)
        return results

    def passes(self, runs: list[CheckRun]) -> bool:
        """Return whether *runs* pass the rule."""
        return self._rule.passes(runs)

    def _run_once(self, tag: str, fixture: Fixture, temperature: float) -> CheckRun:
        """Send one review request for *fixture* and judge what came back."""
        from teachers_teammate.application.formatting import TextFormatter  # noqa: PLC0415
        from teachers_teammate.domain.proofreading_quality import (  # noqa: PLC0415
            count_explained,
        )
        from teachers_teammate.infrastructure.correction_schema import (  # noqa: PLC0415
            schema_json,
            to_review,
            validate_answer,
        )
        from teachers_teammate.infrastructure.ollama_utils import (  # noqa: PLC0415
            proofreading_options,
        )

        system, user = review_request(fixture.language, fixture.text_format, fixture.text)
        started = time.monotonic()
        if not self._chat.fits(system, user):
            return CheckRun(
                fixture=fixture.name, temperature=temperature, verdict=Verdict.REQUEST_TOO_LONG
            )
        try:
            raw, stats = self._chat.chat_with_stats(
                tag,
                user,
                system=system,
                options=proofreading_options(temperature, _num_ctx(self._chat)),
                timeout=self._timeout,
                format=schema_json(),
            )
        except Exception:  # noqa: BLE001  # one failing model must not stop the others
            return CheckRun(
                fixture=fixture.name,
                temperature=temperature,
                verdict=Verdict.REQUEST_FAILED,
                seconds=time.monotonic() - started,
            )
        seconds = time.monotonic() - started
        counted = placeable(raw, fixture.text)
        if counted is None:
            return CheckRun(
                fixture=fixture.name,
                temperature=temperature,
                verdict=Verdict.INVALID,
                done_reason=stats.done_reason,
                prompt_eval_count=stats.prompt_eval_count,
                eval_count=stats.eval_count,
                seconds=seconds,
            )
        placed, total, corrected = counted
        if stats.truncated:
            return CheckRun(
                fixture=fixture.name,
                temperature=temperature,
                verdict=Verdict.TRUNCATED,
                placed=placed,
                total=total,
                done_reason=stats.done_reason,
                prompt_eval_count=stats.prompt_eval_count,
                eval_count=stats.eval_count,
                seconds=seconds,
            )
        if not total:
            return CheckRun(
                fixture=fixture.name,
                temperature=temperature,
                verdict=Verdict.NO_SENTENCES,
                done_reason=stats.done_reason,
                prompt_eval_count=stats.prompt_eval_count,
                eval_count=stats.eval_count,
                seconds=seconds,
            )
        source_plain = TextFormatter.plain_text(fixture.text, fixture.text_format)
        corrected_plain = TextFormatter.plain_text(corrected, fixture.text_format)
        answer, _error = validate_answer(raw)
        explained = count_explained(
            source_plain,
            corrected_plain,
            to_review(answer) if answer is not None else None,
        )
        verdict = Verdict.UNCHANGED if corrected_plain == source_plain else Verdict.OK
        return CheckRun(
            fixture=fixture.name,
            temperature=temperature,
            verdict=verdict,
            placed=placed,
            total=total,
            explained=explained.explained,
            needed=explained.needed,
            orphans=explained.orphans,
            done_reason=stats.done_reason,
            prompt_eval_count=stats.prompt_eval_count,
            eval_count=stats.eval_count,
            seconds=seconds,
        )


def _num_ctx(chat: ChatPort) -> int:
    """Return the context window *chat* sends at (the app's default when it does not say)."""
    from teachers_teammate.infrastructure.ollama_utils import PROOFREADING_NUM_CTX  # noqa: PLC0415

    return int(getattr(chat, "num_ctx", PROOFREADING_NUM_CTX))


def report_lines(runs: Iterator[CheckRun] | list[CheckRun], rule: CheckRule) -> list[str]:
    """Return the per-run report lines of *runs*, judged by *rule*."""
    lines: list[str] = []
    for run in runs:
        verdict = rule.verdict(run)
        lines.append(f"  {run.fixture:<12} {run.settings_line()}  [{verdict.value or 'OK'}]")
    return lines
