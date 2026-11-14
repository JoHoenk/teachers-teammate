"""The model files the app runs itself (not through Ollama), each pinned to one revision.

Pure data: where a model set lives, which files it has and what they must hash to.  Fetching
and loading are in ``infrastructure/onnx/``.  Pinning a revision and a SHA-256 per file means a
model that changes upstream is noticed (the download is refused) instead of silently read
differently.
"""

from __future__ import annotations

from dataclasses import dataclass

HUGGING_FACE_HOST = "huggingface.co"
"""The one host the files come from; named in the download error so an IT department knows what
to allow."""


@dataclass(frozen=True)
class ModelFile:
    """One file of a model set, with the size and checksum it must have."""

    name: str
    size: int
    sha256: str


@dataclass(frozen=True)
class LocalModel:
    """A model file set the app runs itself, pinned to one revision."""

    key: str
    repo: str
    revision: str
    files: tuple[ModelFile, ...]
    licence: str = "Apache-2.0"

    @property
    def size(self) -> int:
        """Return the bytes of all files together."""
        return sum(f.size for f in self.files)

    @property
    def short_revision(self) -> str:
        """Return the first 8 characters of the revision."""
        return self.revision[:8]

    def url(self, file: ModelFile) -> str:
        """Return the download address of *file* at the pinned revision."""
        return f"https://{HUGGING_FACE_HOST}/{self.repo}/resolve/{self.revision}/{file.name}"


PPOCR_SMALL_DET = LocalModel(
    key="ppocr-small-det",
    repo="PaddlePaddle/PP-OCRv6_small_det_onnx",
    revision="28fe5895c24fd108c19eb3e8479f4ab385fbfc62",
    files=(
        ModelFile(
            "inference.onnx",
            9_880_512,
            "d73e0058b7a8086bbd57f3d10b8bcd4ff95363f67e06e2762b5e814fe9c9410e",
        ),
        ModelFile(
            "inference.yml",
            885,
            "193f435274bf9f0b5f71a929bbfbcf148282df7e633b34e7c373e8f44741b516",
        ),
    ),
)

PPOCR_SMALL_REC = LocalModel(
    key="ppocr-small-rec",
    repo="PaddlePaddle/PP-OCRv6_small_rec_onnx",
    revision="b8f84f0b80c529de40b4fbb3544b84fa7233a513",
    files=(
        ModelFile(
            "inference.onnx",
            21_159_378,
            "5435fd747c9e0efe15a96d0b378d5bd157e9492ed8fd80edf08f30d02fa24634",
        ),
        ModelFile(
            "inference.yml",
            150_579,
            "ab078671bb49f06228eadccd34f1bb501e157f7a047095ffb943ba81512c77d1",
        ),
    ),
)

DOC_ORI = LocalModel(
    key="doc-ori",
    repo="PaddlePaddle/PP-LCNet_x1_0_doc_ori_onnx",
    revision="7330ab7039123e46af2dc03154b9969aa412c61d",
    files=(
        ModelFile(
            "inference.onnx",
            6_788_069,
            "af9a0a4f317ff0709ce752067807f819cb15d883f8ecad89f28df1c6ee2d9c92",
        ),
        ModelFile(
            "inference.yml",
            766,
            "9e195eb729a8173588cd0e8a852c8b373aa606e79e77b4ac7d8346f5426caf26",
        ),
    ),
)
"""Tells how far a page is turned (0, 90, 180 or 270 degrees): the ``turn`` step."""

UVDOC = LocalModel(
    key="uvdoc",
    repo="PaddlePaddle/UVDoc_onnx",
    revision="3bcf535371727d11e783101f79a504c68848aae3",
    files=(
        ModelFile(
            "inference.onnx",
            31_684_150,
            "54cab30dc2cf347b4f3d6971c833b1c3d84f5ef17280f1f6d15796bff20e63d6",
        ),
        ModelFile(
            "inference.yml",
            330,
            "be83d537b358f3ff87740e77e14a83ee9e9a7bb215c33d091b69e8bd5904fe39",
        ),
    ),
)
"""Flattens a curved or photographed page: the ``flatten`` step."""

PAGE_MODELS: tuple[LocalModel, ...] = (DOC_ORI, UVDOC)
"""The page models of the automatic image preparation: turn, then flatten."""

PAGE_MODELS_ADDON_ID = "page_models"
"""What the "installed" signals of the GUI call the page models (the add-on id)."""

PAGE_MODELS_DOWNLOAD_COMMAND = "teachers-teammate --download-page-models"
"""The command line that downloads the page models."""

PAGE_MODELS_SIZE_LABEL = "39 MB"
"""The page models' download size as teachers read it (``sum(m.size)`` is 38,473,315 bytes)."""

PAGE_MODELS_NOT_DOWNLOADED = "page models not downloaded"
"""Why a model step of the automatic preparation did not act (debug log and CLI line)."""

PAGE_MODELS_SKIPPED_NOTICE = (
    "Turning and flattening pages is skipped: "
    f"{PAGE_MODELS_NOT_DOWNLOADED} (--download-page-models)"
)
"""The one line a run prints when the automatic preparation has no page models."""

READER_MODELS: tuple[LocalModel, ...] = (PPOCR_SMALL_DET, PPOCR_SMALL_REC)
"""The built-in reader's model sets: text detection, then text recognition."""

READER_MODEL_NAME = "PP-OCRv6 small"
"""The reader's model as the summary rows name it."""

READER_ADDON_ID = "reader"
"""What the "installed" signals of the GUI call the built-in reader (the add-on id)."""

READER_DOWNLOAD_COMMAND = "teachers-teammate --download-reader"
"""The command line that downloads the reader's model files."""

READER_NOT_DOWNLOADED = (
    "The built-in reader is not downloaded yet. Download it in the app's Add-ons settings, or "
    f"run: {READER_DOWNLOAD_COMMAND}"
)
"""The sentence for a reader that has no model files (engine error and run refusal alike)."""

READER_SIZE_GB = round(sum(m.size for m in READER_MODELS) / 1e9, 3)
"""The reader's download size in GB, the unit the setup plan counts in."""

READER_SIZE_LABEL = "31 MB"
"""The reader's download size as teachers read it (``sum(m.size)`` is 31,191,354 bytes)."""

#: Bump when detection, recognition, reading order or joining changes what the reader returns:
#: stored texts read with the old code are then read again.
READER_VERSION = 1


def reader_fingerprint() -> str:
    """Return the reader's identity for the OCR cache key.

    Example: ``"PP-OCRv6_small@28fe5895+b8f84f0b/v1"``.  A new model revision or a new
    :data:`READER_VERSION` changes it, so stored pages are read again.
    """
    return (
        f"PP-OCRv6_small@{PPOCR_SMALL_DET.short_revision}+{PPOCR_SMALL_REC.short_revision}"
        f"/v{READER_VERSION}"
    )
