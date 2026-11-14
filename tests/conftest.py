"""Shared pytest fixtures for the professor's-pet test suite."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import shutil

import pytest

# Use headless Qt in CI/local Bazel test runs unless explicitly overridden.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_API", "pyside6")

# ── Image / PDF fixtures ───────────────────────────────────────────────────


@pytest.fixture
def sample_png(tmp_path: Path) -> Path:
    """A white PNG rendering legible black text ("Hello World").

    The text is large enough that a real OCR engine (the built-in reader) returns a
    non-empty result, which the integration tests rely on. Other consumers only
    need a valid, openable image.
    """
    from PIL import Image, ImageDraw, ImageFont  # noqa: PLC0415

    img = Image.new("RGB", (400, 120), color=(255, 255, 255))
    draw = ImageDraw.Draw(img)
    font = ImageFont.load_default(size=48)
    draw.text((20, 30), "Hello World", fill=(0, 0, 0), font=font)
    path = tmp_path / "sample.png"
    img.save(path)
    return path


@pytest.fixture
def sample_pdf(tmp_path: Path, sample_png: Path) -> Path:  # pylint: disable=redefined-outer-name
    """A single-page PDF wrapping the sample PNG."""
    from PIL import Image  # noqa: PLC0415

    pdf_path = tmp_path / "sample.pdf"
    Image.open(sample_png).convert("RGB").save(str(pdf_path), "PDF")
    return pdf_path


_ANNOTATIONS: dict[str, bytes] = {
    "Ink": (
        b"<< /Type /Annot /Subtype /Ink /Rect [100 100 200 200] "
        b"/InkList [[100 100 150 150 200 100]] /C [1 0 0] >>"
    ),
    "FreeText": (
        b"<< /Type /Annot /Subtype /FreeText /Rect [100 100 300 140] "
        b"/Contents (42 apples) /DA (/Helv 12 Tf 0 g) >>"
    ),
    "Widget": (
        b"<< /Type /Annot /Subtype /Widget /FT /Tx /T (answer) /V (42 apples) "
        b"/Rect [100 100 300 140] >>"
    ),
    "Highlight": (
        b"<< /Type /Annot /Subtype /Highlight /Rect [72 715 300 735] "
        b"/QuadPoints [72 735 300 735 72 715 300 715] /C [1 1 0] >>"
    ),
}


def _page_content_parts(
    escaped_text: str,
    media_box: tuple[int, int, int, int],
    *,
    image: str | None,
    image_size: tuple[int, int] | None,
    scribbles: int,
    ruled_lines: int,
) -> list[str]:
    """Return the content-stream operators of one :func:`write_text_pdf` page."""
    left, bottom, right, top = media_box
    parts: list[str] = []
    if image:
        width, height = image_size or (right - left, top - bottom)
        parts.append(f"q {width} 0 0 {height} {left} {bottom} cm /{image} Do Q")
    parts.append(f"BT /F1 12 Tf {left + 72} {top - 72} Td ({escaped_text}) Tj ET")
    for i in range(scribbles):
        x, y = left + 100 + 10 * i, bottom + 300
        parts.append(f"{x} {y} m {x + 5} {y + 20} {x + 10} {y - 20} {x + 15} {y} c S")
    for i in range(ruled_lines):
        y = bottom + 200 + 10 * i
        parts.append(f"{left + 50} {y} m {right - 50} {y} l S")
    return parts


def write_text_pdf(  # noqa: PLR0913  # each keyword toggles one PDF feature under test
    path: Path,
    pages: list[str],
    *,
    full_page_image: bool = False,
    image_in_form: bool = False,
    image_size: tuple[int, int] | None = None,
    annotation: str | None = None,
    scribbles: int = 0,
    ruled_lines: int = 0,
    media_box: tuple[int, int, int, int] = (0, 0, 612, 792),
) -> Path:
    """Write a PDF whose pages carry *pages* as a real (embedded) text layer.

    ``full_page_image`` adds a media-box-sized image under the text (a scan with a text
    layer); ``image_in_form`` draws that image from a scaled Form XObject instead of
    directly.  ``image_size`` (width, height in points) adds an image of that size at the
    page's lower-left corner instead (a pasted photo or logo).  ``annotation`` names a
    subtype of :data:`_ANNOTATIONS` to add to every page.  ``scribbles`` adds that many
    curved pen strokes as vector paths, ``ruled_lines`` that many horizontal lines.
    ``media_box`` sets the page box, which need not start at the origin.
    """
    objects: list[bytes] = [b""]  # 1-based ids; index 0 unused

    def add(body: bytes) -> int:
        objects.append(body)
        return len(objects) - 1

    left, bottom, right, top = media_box
    has_image = full_page_image or image_in_form or image_size is not None

    catalog_id = add(b"")
    pages_id = add(b"")
    font_id = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    image_id = (
        add(
            b"<< /Type /XObject /Subtype /Image /Width 1 /Height 1 /ColorSpace /DeviceGray "
            b"/BitsPerComponent 8 /Length 1 >>\nstream\n\x80\nendstream"
        )
        if has_image
        else 0
    )
    form_id = 0
    if image_in_form:
        form_stream = b"q 1 0 0 1 0 0 cm /Im1 Do Q"
        form_id = add(
            b"<< /Type /XObject /Subtype /Form /BBox [0 0 1 1] "
            b"/Resources << /XObject << /Im1 %d 0 R >> >> /Length %d >>\nstream\n"
            % (image_id, len(form_stream))
            + form_stream
            + b"\nendstream"
        )
    page_ids: list[int] = []
    for text in pages:
        escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        parts = _page_content_parts(
            escaped,
            media_box,
            image="Fm1" if image_in_form else "Im1" if has_image else None,
            image_size=image_size,
            scribbles=scribbles,
            ruled_lines=ruled_lines,
        )
        stream = " ".join(parts).encode("latin-1")
        content_id = add(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
        annots = b""
        if annotation:
            annot_id = add(_ANNOTATIONS[annotation])
            annots = b" /Annots [%d 0 R]" % annot_id
        xobjects = b""
        if image_in_form:
            xobjects = b" /XObject << /Fm1 %d 0 R >>" % form_id
        elif has_image:
            xobjects = b" /XObject << /Im1 %d 0 R >>" % image_id
        page_ids.append(
            add(
                b"<< /Type /Page /Parent %d 0 R /MediaBox [%d %d %d %d] /Contents %d 0 R "
                b"/Resources << /Font << /F1 %d 0 R >>%s >>%s >>"
                % (pages_id, left, bottom, right, top, content_id, font_id, xobjects, annots)
            )
        )
    kids = b" ".join(b"%d 0 R" % pid for pid in page_ids)
    objects[pages_id] = b"<< /Type /Pages /Kids [%s] /Count %d >>" % (kids, len(page_ids))
    objects[catalog_id] = b"<< /Type /Catalog /Pages %d 0 R >>" % pages_id

    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for obj_id in range(1, len(objects)):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % obj_id + objects[obj_id] + b"\nendobj\n"
    xref_pos = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % len(objects)
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    out += b"trailer\n<< /Size %d /Root %d 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects),
        catalog_id,
        xref_pos,
    )
    path.write_bytes(bytes(out))
    return path


# ── Config helper ──────────────────────────────────────────────────────────


def make_config(tmp_path: Path, **overrides: object):
    """Return a minimal :class:`~teachers_teammate.config.Config` suitable for testing.

    ``output_dir`` is created automatically.  Any field can be overridden via
    keyword arguments.
    """
    from teachers_teammate.config import Config, OcrConfig  # noqa: PLC0415

    output_dir = overrides.pop("output_dir", tmp_path / "output")
    assert isinstance(output_dir, Path)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Accept flat OCR overrides (ocr_engine=, ocr_model=, ...) for backwards
    # compatibility and assemble them into the nested OcrConfig value object.
    ocr_field_map = {
        "ocr_engine": "engine",
        "ocr_model": "model",
        "preprocess_method": "preprocess_method",
        "ocr_temperature": "temperature",
    }
    # "Original page" so tests don't depend on (or pay for) the automatic preparation.
    ocr_defaults: dict = {
        "engine": "ppocr",
        "model": "",
        "preprocess_method": "none",
    }
    for flat_key, ocr_key in ocr_field_map.items():
        if flat_key in overrides:
            ocr_defaults[ocr_key] = overrides.pop(flat_key)
    ocr = overrides.pop("ocr", OcrConfig(**ocr_defaults))

    defaults: dict = {
        "input_dir": tmp_path / "input",
        "output_dir": output_dir,
        "recursive": False,
        "debug": False,
        "ocr": ocr,
        "language": "English",
        "ollama_url": "http://127.0.0.1:11434",
        "correction_enabled": False,
        "correction_provider": "openai",
        "correction_model": "",
        "ocr_timeout": 60,
    }
    defaults.update(overrides)
    input_dir = defaults["input_dir"]
    assert isinstance(input_dir, Path)
    input_dir.mkdir(parents=True, exist_ok=True)
    return Config(**defaults)


# ── Network isolation ──────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _no_background_network_threads(monkeypatch: pytest.MonkeyPatch) -> None:
    """Never let MainWindow start real network threads during tests.

    MainWindow schedules an UpdateCheckThread (GitHub API) at construction and a
    _ConnectionCheckThread (Ollama) shortly after. If a test tears the window down while
    one is still blocked on a socket, Qt aborts the whole pytest process
    ("QThread: Destroyed while thread is still running"). Tests that exercise ``run()``
    call it directly and are unaffected.
    """
    try:
        from teachers_teammate.gui._update_check import UpdateCheckThread  # noqa: PLC0415
        from teachers_teammate.gui._worker import _ConnectionCheckThread  # noqa: PLC0415
    except ImportError:  # pure-unit targets without PySide6
        return
    monkeypatch.setattr(UpdateCheckThread, "start", lambda self, *_a: None)
    monkeypatch.setattr(_ConnectionCheckThread, "start", lambda self, *_a: None)


@pytest.fixture(autouse=True)
def _models_dir_in_tmp(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Point the model store at an empty temporary folder: no test sees or fills the real one.

    A test that needs the built-in reader's models builds its own store (``reader_store``) or
    fakes the reader; none downloads.
    """
    monkeypatch.setenv("TEACHERS_TEAMMATE_MODELS_DIR", str(tmp_path_factory.mktemp("models")))


@pytest.fixture(autouse=True)
def _never_block_system_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """A test must never really keep the computer awake (nor start ``caffeinate`` & co).

    The application service asks ``power.default_sleep_blocker()`` when it is built; tests that
    care about the blocker pass their own fake.
    """
    try:
        from teachers_teammate.infrastructure import power  # noqa: PLC0415
    except ImportError:  # pure-unit targets without the package
        return
    monkeypatch.setattr(power, "default_sleep_blocker", power.NullSleepBlocker)


@pytest.fixture(autouse=True)
def credential_file(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    """Keep API keys off the real machine: no system keychain, an in-memory settings file.

    Returns the stored ``{"credentials/<ENV_KEY>": value}`` entries, so a test can read what
    was saved or plant a key as if an earlier session had saved it.
    """
    stored: dict[str, object] = {}
    try:
        from teachers_teammate.gui import _credential_store  # noqa: PLC0415
    except ImportError:  # pure-unit targets without PySide6
        return stored

    class MemorySettings:
        def __init__(self, *_args: object) -> None:
            pass

        def value(self, key: str, default: object = None) -> object:
            return stored.get(key, default)

        def setValue(self, key: str, value: object) -> None:
            stored[key] = value

        def remove(self, key: str) -> None:
            stored.pop(key, None)

    monkeypatch.setattr(_credential_store, "QSettings", MemorySettings)
    monkeypatch.setattr(_credential_store, "_keyring", lambda: None)
    # What start-up found in the environment is process-wide state; no test inherits it.
    monkeypatch.setattr(_credential_store, "_SET_OUTSIDE_APP", set())
    monkeypatch.setattr(_credential_store, "_LOADED_BY_APP", set())
    return stored


# ── Pytest markers ─────────────────────────────────────────────────────────


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "unit: pure unit test with no external dependencies")
    config.addinivalue_line("markers", "needs_ollama: requires a running Ollama instance")
    config.addinivalue_line(
        "markers",
        "needs_reader_models: runs the built-in reader's real model files (Bazel fetches them)",
    )
    config.addinivalue_line(
        "markers",
        "needs_page_models: runs the page models' real model files (Bazel fetches them)",
    )
    config.addinivalue_line("markers", "gui: exercises PySide6 widgets/event loop")
    config.addinivalue_line(
        "markers", "use_case: links test to a named use case from use_cases.trlc"
    )


READER_MODEL_ENV = {
    "PPOCR_SMALL_DET_ONNX": ("ppocr-small-det", "inference.onnx"),
    "PPOCR_SMALL_DET_YML": ("ppocr-small-det", "inference.yml"),
    "PPOCR_SMALL_REC_ONNX": ("ppocr-small-rec", "inference.onnx"),
    "PPOCR_SMALL_REC_YML": ("ppocr-small-rec", "inference.yml"),
}
"""Environment variables (set by the Bazel target) naming the pinned model files."""


def _reader_model_files() -> dict[str, Path]:
    """Return the pinned model files Bazel fetched, by variable name; empty if not available."""
    root = os.environ.get("TEST_SRCDIR", "")
    found: dict[str, Path] = {}
    for name in READER_MODEL_ENV:
        value = os.environ.get(name)
        if not value or not root:
            return {}
        path = Path(root) / value
        if not path.is_file():
            return {}
        found[name] = path
    return found


skip_no_reader_models = pytest.mark.skipif(
    not _reader_model_files(),
    reason="the built-in reader's model files are only fetched under Bazel",
)


PAGE_MODEL_ENV = {
    "DOC_ORI_ONNX": ("doc-ori", "inference.onnx"),
    "DOC_ORI_YML": ("doc-ori", "inference.yml"),
    "UVDOC_ONNX": ("uvdoc", "inference.onnx"),
    "UVDOC_YML": ("uvdoc", "inference.yml"),
}
"""Environment variables (set by the Bazel target) naming the page models' pinned files."""


def _page_model_files() -> dict[str, Path]:
    """Return the page models' files Bazel fetched, by variable name; empty if not available."""
    root = os.environ.get("TEST_SRCDIR", "")
    found: dict[str, Path] = {}
    for name in PAGE_MODEL_ENV:
        value = os.environ.get(name)
        if not value or not root:
            return {}
        path = Path(root) / value
        if not path.is_file():
            return {}
        found[name] = path
    return found


skip_no_page_models = pytest.mark.skipif(
    not _page_model_files(),
    reason="the page models' files are only fetched under Bazel",
)


def page_models_store(tmp_path: Path):
    """Return a :class:`ModelStore` under *tmp_path* holding the pinned page models.

    Like :func:`reader_store`: the files Bazel fetched are linked into the store's layout.  Only
    for tests marked ``skip_no_page_models``.
    """
    import json  # noqa: PLC0415

    from teachers_teammate.domain.local_models import PAGE_MODELS  # noqa: PLC0415
    from teachers_teammate.infrastructure.onnx.store import ModelStore  # noqa: PLC0415

    files = _page_model_files()
    store = ModelStore(tmp_path / "page_models")
    for model in PAGE_MODELS:
        folder = store.folder(model)
        folder.mkdir(parents=True)
        for var, (key, name) in PAGE_MODEL_ENV.items():
            if key != model.key:
                continue
            try:
                (folder / name).symlink_to(files[var])
            except OSError:
                shutil.copy(files[var], folder / name)
        (folder / ".verified").write_text(json.dumps({}), encoding="utf-8")
    return store


def fake_page_models_store(tmp_path: Path, *, installed: bool = True):
    """Return a :class:`ModelStore` that looks like it holds the page models (empty files).

    The files have the pinned sizes but no content, so ``is_installed`` is true and nothing can
    run: for tests of identity and settings.  With ``installed=False`` the store is empty.
    """
    import json  # noqa: PLC0415

    from teachers_teammate.domain.local_models import PAGE_MODELS  # noqa: PLC0415
    from teachers_teammate.infrastructure.onnx.store import ModelStore  # noqa: PLC0415

    store = ModelStore(tmp_path / "fake_page_models")
    if not installed:
        return store
    for model in PAGE_MODELS:
        folder = store.folder(model)
        folder.mkdir(parents=True)
        for file in model.files:
            with (folder / file.name).open("wb") as handle:
                handle.truncate(file.size)
        (folder / ".verified").write_text(json.dumps({}), encoding="utf-8")
    return store


def reader_store(tmp_path: Path):
    """Return a :class:`ModelStore` under *tmp_path* holding the pinned reader models.

    The files Bazel fetched are linked (copied where links are not possible) into the store's
    layout, so the real reader runs on the real models.  Only for tests marked
    ``skip_no_reader_models``.
    """
    import json  # noqa: PLC0415

    from teachers_teammate.domain.local_models import READER_MODELS  # noqa: PLC0415
    from teachers_teammate.infrastructure.onnx.store import ModelStore  # noqa: PLC0415

    files = _reader_model_files()
    store = ModelStore(tmp_path / "models")
    for model in READER_MODELS:
        folder = store.folder(model)
        folder.mkdir(parents=True)
        for var, (key, name) in READER_MODEL_ENV.items():
            if key != model.key:
                continue
            try:
                (folder / name).symlink_to(files[var])
            except OSError:
                shutil.copy(files[var], folder / name)
        (folder / ".verified").write_text(json.dumps({}), encoding="utf-8")
    return store


def fake_reader_store(tmp_path: Path, *, installed: bool = True):
    """Return a :class:`ModelStore` under *tmp_path* that looks like it holds the reader models.

    The files have the pinned sizes but no content (sparse), so ``is_installed`` is true and
    nothing can run: for tests of requirement checks and settings, which never read a page.
    With ``installed=False`` the store is empty.
    """
    import json  # noqa: PLC0415

    from teachers_teammate.domain.local_models import READER_MODELS  # noqa: PLC0415
    from teachers_teammate.infrastructure.onnx.store import ModelStore  # noqa: PLC0415

    store = ModelStore(tmp_path / "fake_models")
    if not installed:
        return store
    for model in READER_MODELS:
        folder = store.folder(model)
        folder.mkdir(parents=True)
        for file in model.files:
            with (folder / file.name).open("wb") as handle:
                handle.truncate(file.size)
        (folder / ".verified").write_text(json.dumps({}), encoding="utf-8")
    return store
