"""The speed test: how fast this computer runs the models chosen on the AI models page.

The test measures the **machine**, not the answer length: how many errors a class makes is not
something a test can know, so the answer length comes from the catalog and from real runs.  One
warm-up request loads the model (its ``load_duration`` is the load time) and one measuring
request of about :data:`MEASURE_ANSWER_TOKENS` answer tokens gives the reading rate, the writing
rate and, for a reading model, the tokens of one page image.

It runs **only when the teacher clicks "Test speed"**: it occupies the graphics card, possibly
on a server other people use.  A measuring request is capped at :data:`MEASURE_TIMEOUT_S`; the
first-token wait stays at the client's own limit, which covers loading a large model from a slow
disk.

The test inputs are drawn here, so no corpus text and no licence question is involved: a full A4
page of printed text for the reading models, and one paragraph per language with about ten
typical learner errors for the writing models.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
import logging
from pathlib import Path
import tempfile
import threading

from ..config import OcrConfig
from ..domain.model_catalog import (
    OCR_TEMPERATURE,
    normalize_tag,
    request_profile,
    review_temperature,
)
from ..domain.performance_estimate import ModelSpeed, SpeedStep, StepOutcome
from ..exceptions import (
    OCRDeadlineError,
    OCRError,
    OCRTimeoutError,
    OllamaUnreachableError,
    RunStoppedError,
    stop_requested,
)
from .ollama_utils import (
    PROOFREADING_NUM_CTX,
    ChatStats,
    OllamaClient,
    ServerFacts,
    proofreading_options,
)
from .onnx.store import ModelStore

_logger = logging.getLogger(__name__)

#: Seconds one measuring request may take.  A model that needs longer is reported as too slow
#: for that step instead of holding the panel.
MEASURE_TIMEOUT_S = 90.0

#: Tokens the measuring request may answer.  Enough to read a steady writing rate off it, short
#: enough that the whole test stays under a minute and a half on a graphics card.
MEASURE_ANSWER_TOKENS = 200

#: The warm-up request: one token, so it only loads the model and reports ``load_duration``.
WARMUP_PROMPT = "Reply with OK."
WARMUP_ANSWER_TOKENS = 1

#: Seconds per nanosecond, the unit Ollama reports its durations in.
_NS = 1e9

#: A4 in millimetres, the page the test image is drawn at.
_A4_MM = (210.0, 297.0)

#: Words the test page carries.  Enough that the answer reaches the token cap, so the writing
#: rate is measured on a full answer rather than on a short one.
TEST_PAGE_WORDS = 350


@dataclass(frozen=True)
class StepResult:
    """What one step of the test found."""

    step: SpeedStep
    model: str
    outcome: StepOutcome
    speed: ModelSpeed | None = None
    message: str = ""


@dataclass(frozen=True)
class ProbeResult:
    """Everything one run of the test found."""

    steps: tuple[StepResult, ...] = ()
    facts: ServerFacts = field(default_factory=ServerFacts)
    """The server's version, the model digests and what it held in memory afterwards."""
    on_battery: bool | None = None
    shared: bool = False
    """Another model was loaded when the test started: the times may be too long."""
    swaps: bool = False
    """The reading model was evicted by the writing model, so every file pays both loads."""
    gpu_found: bool = False
    """This computer has a graphics card, so a share of 0 means Ollama did not use it."""
    error: str = ""
    """One line when the server could not be reached at all."""

    def step(self, step: SpeedStep) -> StepResult | None:
        """Return the result of *step*, or ``None`` when it did not run."""
        return next((item for item in self.steps if item.step is step), None)


@dataclass(frozen=True)
class SpeedTestRequest:
    """What the test measures: the models chosen on the page, and how they are asked.

    An empty model means the step does not run.  The temperatures and the request profile are
    read from the model tag (:func:`~teachers_teammate.domain.model_catalog.request_profile`), so
    the test sends the request a run would send.
    """

    reading_model: str = ""
    writing_model: str = ""
    review_model: str = ""
    """The content review's own model; empty when it shares the proofreading one."""
    language: str = "English"
    num_ctx: int = PROOFREADING_NUM_CTX
    ocr: OcrConfig = field(default_factory=OcrConfig)

    def models(self) -> tuple[str, ...]:
        """Return the models the test will ask the server about, without duplicates."""
        seen: list[str] = []
        for tag in (self.reading_model, self.writing_model, self.review_model):
            if tag and tag not in seen:
                seen.append(tag)
        return tuple(seen)


# ── the test inputs ───────────────────────────────────────────────────────────

#: One paragraph per language for the writing models: about 150 words with about ten typical
#: learner errors each, written for this purpose (no corpus text).  The batch language picks
#: one; any other language uses English.
TEST_PARAGRAPHS: dict[str, str] = {
    "english": (
        "Last weekend my class maked a trip to the museum in the city. We was very exciting "
        "because we never visit a museum before. The teacher telled us to bring a notebook and "
        "a pencil, but I forgotted mine at home. In the museum we seed many old things from the "
        "roman time. I finded a small coin and a broken pot very interesting. My friend she said "
        "that the guide speaked too fast, so we dont understanded everything. After the lunch we "
        "walked in the garden and taked many photos. On the way home the bus was late and we "
        "waited more then one hour. I think it was a good day, but next time I will bring my "
        "notebook."
    ),
    "german": (
        "Am letzte Wochenende hat meine Klasse einen Ausflug in das Museum gemacht. Wir waren "
        "sehr aufgeregt, weil wir noch nie in einem Museum gewesen sind. Der Lehrer hat uns "
        "gesagt, dass wir ein Heft und ein Stift mitbringen sollen, aber ich habe meine Heft zu "
        "Hause vergessen. Im Museum haben wir viele alte Sachen aus der römischen Zeit gesehen. "
        "Ich fand eine kleine Münze und ein kaputtes Topf sehr interessant. Meine Freundin hat "
        "gesagt, dass der Führer zu schnell spricht, deshalb haben wir nicht alles verstanden. "
        "Nach dem Mittagessen sind wir im Garten gelaufen und haben viele Fotos gemacht. Auf dem "
        "Rückweg war der Bus verspätet und wir haben mehr als eine Stunde gewartet. Ich denke, "
        "es war ein guter Tag, aber nächstes Mal bringe ich mein Heft mit."
    ),
    "french": (
        "Le week-end dernier, ma classe a fait une sortie au musée de la ville. Nous étions très "
        "excité parce que nous n'avons jamais visité un musée avant. Le professeur nous a dit de "
        "apporter un cahier et un crayon, mais j'ai oublié le mien à la maison. Dans le musée, "
        "nous avons vu beaucoup de choses anciennes de l'époque romaine. J'ai trouvé une petite "
        "pièce et un pot cassé très intéressant. Mon amie elle a dit que le guide parlait trop "
        "vite, donc nous n'avons pas compris tout. Après le déjeuner, nous avons marché dans le "
        "jardin et nous avons pris beaucoup de photos. Sur le chemin du retour, le bus était en "
        "retard et nous avons attendu plus de une heure. Je pense que c'était une bonne journée, "
        "mais la prochaine fois je vais apporter mon cahier."
    ),
}

#: The sentence the test page is filled with.  Printed text, so the page needs no handwriting and
#: no licence: the image size is what sets the image token count, not the content.
_PAGE_SENTENCE = (
    "The class read the chapter about the water cycle and wrote down what happens when the "
    "sun heats a lake in summer. "
)


def sample_paragraph(language: str) -> str:
    """Return the paragraph the writing models proofread, for *language* (English otherwise)."""
    return TEST_PARAGRAPHS.get((language or "").strip().lower(), TEST_PARAGRAPHS["english"])


def page_size_px(dpi: int) -> tuple[int, int]:
    """Return the pixel size of an A4 page at *dpi*."""
    width_mm, height_mm = _A4_MM
    return round(width_mm / 25.4 * dpi), round(height_mm / 25.4 * dpi)


def draw_test_page(path: Path, *, dpi: int) -> Path:
    """Draw a full A4 page of printed text at *dpi* and save it to *path*.

    The page is what a reading model is asked to read: the image size sets the image token
    count, so a page of printed text measures the same thing a handwritten page would.
    """
    from PIL import Image, ImageDraw, ImageFont  # noqa: PLC0415  # optional dependency

    width, height = page_size_px(dpi)
    image = Image.new("RGB", (width, height), color=(255, 255, 255))
    draw = ImageDraw.Draw(image)
    # 11 pt at the requested resolution, the size of a printed page's body text.
    font_size = max(8, round(11 / 72 * dpi))
    font = ImageFont.load_default(size=font_size)
    margin = round(width * 0.08)
    line_height = round(font_size * 1.6)

    words: list[str] = []
    while len(words) < TEST_PAGE_WORDS:
        words.extend(_PAGE_SENTENCE.split())
    lines: list[str] = []
    current = ""
    for word in words[:TEST_PAGE_WORDS]:
        candidate = f"{current} {word}".strip()
        if draw.textlength(candidate, font=font) > width - 2 * margin:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)

    y = margin
    for line in lines:
        if y + line_height > height - margin:
            break
        draw.text((margin, y), line, fill=(0, 0, 0), font=font)
        y += line_height
    image.save(path)
    return path


def sample_page_image(
    tmp_dir: Path, *, ocr: OcrConfig, store: ModelStore | None = None
) -> tuple[Path, Path]:
    """Draw the test page and run it through *ocr*'s preprocessing.

    Returns ``(drawn, preprocessed)``.  The preprocessing is the one a run applies, so the image
    the model sees is the image a run would send.
    """
    from .preprocessing.recipes import steps_for  # noqa: PLC0415
    from .preprocessing.steps import StepPipeline  # noqa: PLC0415

    drawn = draw_test_page(tmp_dir / "speed-test-page.png", dpi=ocr.pdf_render_dpi)
    preprocessor = StepPipeline(steps_for(ocr, store or ModelStore()), tmp_dir=tmp_dir)
    processed, _steps = preprocessor.preprocess(drawn)
    return drawn, processed


# ── the probe ─────────────────────────────────────────────────────────────────


def _rate(tokens: int, duration_ns: int) -> float:
    """Return tokens per second of *tokens* over *duration_ns* nanoseconds (0 when unknown)."""
    return tokens / (duration_ns / _NS) if duration_ns > 0 else 0.0


def _speed_from(
    stats: ChatStats,
    *,
    load_s: float | None,
    image_tokens: int | None = None,
    chars_per_token: float | None = None,
) -> ModelSpeed | None:
    """Return the measured speed of one request, or ``None`` when a rate is missing."""
    read_tps = _rate(stats.prompt_eval_count, stats.prompt_eval_duration)
    write_tps = _rate(stats.eval_count, stats.eval_duration)
    if read_tps <= 0 or write_tps <= 0:
        return None
    return ModelSpeed(
        read_tps=read_tps,
        write_tps=write_tps,
        load_s=load_s,
        image_tokens=image_tokens,
        chars_per_token=chars_per_token,
    )


class PerformanceProbe:
    """Measures how fast the server runs the chosen models; see the module docstring."""

    def __init__(
        self,
        client: OllamaClient,
        *,
        cancel_event: threading.Event | None = None,
        gpu_found: bool = False,
        on_battery: bool | None = None,
        tmp_dir: Path | None = None,
    ) -> None:
        """Create the probe.

        Args:
            client: The server to measure.  Build it with the same *cancel_event* (the client
                abandons a request that is waiting when the event is set); the probe itself
                only checks the event between requests.
            cancel_event: Set by the panel's Stop button and by closing the window.
            gpu_found: Whether this computer has a graphics card, so a share of 0 on it means
                Ollama did not use the card.
            on_battery: Whether the computer runs on battery power, recorded with the result.
            tmp_dir: Where the test page is drawn; a temporary directory when ``None``.
        """
        self._client = client
        self._cancel_event = cancel_event
        self._gpu_found = gpu_found
        self._on_battery = on_battery
        self._tmp_dir = tmp_dir

    # ── public API ─────────────────────────────────────────────────────────

    def run(self, request: SpeedTestRequest) -> ProbeResult:
        """Measure every step of *request* and return what was found.

        Raises:
            :exc:`~teachers_teammate.exceptions.RunStoppedError`: the teacher stopped the test.
        """
        if stop_requested(self._cancel_event):
            raise RunStoppedError
        # One request tells "unreachable" from "no model installed": a server that cannot be
        # reached must not look like a test that found every model missing.
        installed, unreachable = self._client.list_models_or_error()
        if unreachable:
            _logger.info("speed test: server unreachable: %s", unreachable)
            return ProbeResult(
                on_battery=self._on_battery,
                gpu_found=self._gpu_found,
                error=unreachable.removeprefix("✗").strip(),
            )
        before = self._client.server_facts()
        tested = {normalize_tag(tag) for tag in request.models()}
        shared = any(normalize_tag(name) not in tested for name in before.loaded_names())
        try:
            steps, swaps = self._measure_steps(request, installed=installed, before=before)
        except OllamaUnreachableError as exc:
            _logger.info("speed test: server unreachable: %s", exc)
            return ProbeResult(
                facts=before,
                on_battery=self._on_battery,
                gpu_found=self._gpu_found,
                error=str(exc),
            )
        after = self._client.server_facts()
        return ProbeResult(
            steps=self._decorate(steps, after=after),
            facts=after,
            on_battery=self._on_battery,
            shared=shared,
            swaps=swaps,
            gpu_found=self._gpu_found,
        )

    # ── the steps ──────────────────────────────────────────────────────────

    def _measure_steps(
        self,
        request: SpeedTestRequest,
        *,
        installed: Sequence[str],
        before: ServerFacts,
    ) -> tuple[list[StepResult], bool]:
        """Measure the reading, proofreading and review steps in that order.

        Returns the results and whether the models swap: the reading model was loaded by its own
        step and gone once the writing model was loaded.
        """
        results: list[StepResult] = []
        swaps = False
        with tempfile.TemporaryDirectory(prefix="speed-test-") as tmp:
            tmp_dir = self._tmp_dir or Path(tmp)
            if request.reading_model:
                results.append(
                    self._reading_step(request, tmp_dir, installed=installed, before=before)
                )
                reading_loaded = self._is_loaded(request.reading_model)
            else:
                reading_loaded = False
            if request.writing_model:
                results.append(
                    self._writing_step(
                        SpeedStep.PROOFREADING,
                        request.writing_model,
                        request,
                        installed=installed,
                        before=before,
                    )
                )
                if reading_loaded and request.reading_model != request.writing_model:
                    swaps = not self._is_loaded(request.reading_model)
            if request.review_model and request.review_model != request.writing_model:
                results.append(
                    self._writing_step(
                        SpeedStep.REVIEW,
                        request.review_model,
                        request,
                        installed=installed,
                        before=before,
                    )
                )
        return results, swaps

    def _reading_step(
        self,
        request: SpeedTestRequest,
        tmp_dir: Path,
        *,
        installed: Sequence[str],
        before: ServerFacts,
    ) -> StepResult:
        """Warm the reading model up, then read the test page once."""
        import base64  # noqa: PLC0415

        from .ocr_processor import get_ocr_prompt  # noqa: PLC0415

        model = request.reading_model
        if not _is_installed(model, installed):
            return StepResult(SpeedStep.READING, model, StepOutcome.SKIPPED)
        load_s = self._warm_up(model, before=before)
        _drawn, page = sample_page_image(tmp_dir, ocr=request.ocr)
        image = base64.b64encode(page.read_bytes()).decode("utf-8")
        stats, outcome, message = self._measure(
            model,
            prompt=get_ocr_prompt(request.language),
            images=[image],
            options={"temperature": OCR_TEMPERATURE, "num_predict": MEASURE_ANSWER_TOKENS},
        )
        if stats is None:
            return StepResult(SpeedStep.READING, model, outcome, message=message)
        speed = _speed_from(stats, load_s=load_s, image_tokens=stats.prompt_eval_count)
        if speed is None:
            return StepResult(
                SpeedStep.READING,
                model,
                StepOutcome.FAILED,
                message="the server reported no token counts for the page",
            )
        return StepResult(SpeedStep.READING, model, StepOutcome.MEASURED, speed=speed)

    def _writing_step(
        self,
        step: SpeedStep,
        model: str,
        request: SpeedTestRequest,
        *,
        installed: Sequence[str],
        before: ServerFacts,
    ) -> StepResult:
        """Warm a writing model up, then send the app's own request for one paragraph."""
        if not _is_installed(model, installed):
            return StepResult(step, model, StepOutcome.SKIPPED)
        load_s = self._warm_up(model, before=before)
        system, prompt, schema, temperature, think = _writing_request(step, model, request)
        stats, outcome, message = self._measure(
            model,
            prompt=prompt,
            system=system,
            format=schema,
            think=think,
            options={
                **proofreading_options(temperature, request.num_ctx),
                "num_predict": MEASURE_ANSWER_TOKENS,
            },
        )
        if stats is None:
            return StepResult(step, model, outcome, message=message)
        chars = len(system) + len(prompt)
        chars_per_token = chars / stats.prompt_eval_count if stats.prompt_eval_count else None
        speed = _speed_from(stats, load_s=load_s, chars_per_token=chars_per_token)
        if speed is None:
            return StepResult(
                step,
                model,
                StepOutcome.FAILED,
                message="the server reported no token counts for the paragraph",
            )
        return StepResult(step, model, StepOutcome.MEASURED, speed=speed)

    # ── one request ────────────────────────────────────────────────────────

    def _warm_up(self, model: str, *, before: ServerFacts) -> float | None:
        """Load *model* with one tiny request and return its load time.

        ``None`` when the model was already loaded: its load time is then unknown, and a stored
        ``0`` would claim the model loads instantly.
        """
        already_loaded = before.loaded_model(model) is not None
        stats, _outcome, _message = self._measure(
            model,
            prompt=WARMUP_PROMPT,
            options={"num_predict": WARMUP_ANSWER_TOKENS},
        )
        if stats is None or already_loaded:
            return None
        return stats.load_duration / _NS

    def _measure(
        self,
        model: str,
        *,
        prompt: str,
        system: str = "",
        images: list[str] | None = None,
        options: dict | None = None,
        format: dict | None = None,  # pylint: disable=redefined-builtin  # the ollama API's name
        think: bool | str = False,
    ) -> tuple[ChatStats | None, StepOutcome, str]:
        """Send one request and return ``(stats, outcome, message)``.

        A request that hits :data:`MEASURE_TIMEOUT_S` is ``TOO_SLOW``; a stop is re-raised.
        """
        if stop_requested(self._cancel_event):
            raise RunStoppedError
        try:
            _text, stats = self._client.chat_with_stats(
                model,
                prompt,
                images=images,
                options=options,
                think=think,
                format=format,
                system=system,
                total_timeout=MEASURE_TIMEOUT_S,
            )
        except RunStoppedError:
            raise
        except (OCRDeadlineError, OCRTimeoutError) as exc:
            _logger.info("speed test: %s too slow: %s", model, exc)
            return None, StepOutcome.TOO_SLOW, str(exc)
        except OllamaUnreachableError:
            raise
        except OCRError as exc:
            _logger.info("speed test: %s failed: %s", model, exc)
            return None, StepOutcome.FAILED, str(exc)
        return stats, StepOutcome.MEASURED, ""

    def _is_loaded(self, model: str) -> bool:
        """Return whether the server holds *model* in memory right now."""
        return any(
            normalize_tag(item.name) == normalize_tag(model)
            for item in self._client.loaded_models()
        )

    # ── what the server held in memory ─────────────────────────────────────

    @staticmethod
    def _decorate(steps: Sequence[StepResult], *, after: ServerFacts) -> tuple[StepResult, ...]:
        """Fill in each measured step's share on the graphics card from ``/api/ps``."""
        decorated: list[StepResult] = []
        for item in steps:
            if item.speed is None:
                decorated.append(item)
                continue
            loaded = after.loaded_model(item.model)
            decorated.append(
                StepResult(
                    item.step,
                    item.model,
                    item.outcome,
                    speed=ModelSpeed(
                        read_tps=item.speed.read_tps,
                        write_tps=item.speed.write_tps,
                        load_s=item.speed.load_s,
                        on_card=loaded.on_card_share if loaded is not None else None,
                        image_tokens=item.speed.image_tokens,
                        chars_per_token=item.speed.chars_per_token,
                    ),
                    message=item.message,
                )
            )
        return tuple(decorated)


def _is_installed(model: str, installed: Sequence[str]) -> bool:
    """Return whether *model* is installed (a bare name means ``:latest``)."""
    wanted = normalize_tag(model)
    return any(normalize_tag(name) == wanted for name in installed)


def _writing_request(
    step: SpeedStep, model: str, request: SpeedTestRequest
) -> tuple[str, str, dict | None, float, bool | str]:
    """Return ``(system, prompt, schema, temperature, think)`` of the request a run would send."""
    from ..config import DEFAULTS  # noqa: PLC0415
    from ..domain.notation import with_notation_hint  # noqa: PLC0415
    from .correction import request_messages  # noqa: PLC0415
    from .correction_schema import schema_json  # noqa: PLC0415

    paragraph = sample_paragraph(request.language)
    if step is SpeedStep.REVIEW:
        # The content review sends its instructions inside the message, as the evaluator does.
        system = with_notation_hint(DEFAULTS["evaluate_prompt"], "plain", task="evaluation")
        prompt = f"{system}\n\nLanguage: {request.language}\n\nCorrected text:\n\n{paragraph}"
        return "", prompt, None, review_temperature(model), request_profile(model).think
    profile = request_profile(model)
    system, prompt = request_messages(
        paragraph,
        request.language,
        mode="review",
        compact_json=profile.compact_json,
    )
    return system, prompt, schema_json(), profile.temperature, profile.think
