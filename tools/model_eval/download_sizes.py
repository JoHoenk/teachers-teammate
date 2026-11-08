"""The real download size of a catalog tag, read from the registry.

This was the first of the three jobs of ``tools/verify_model_catalog.py``.  It stays out of the
catalog itself for one reason: the numbers are entered by hand after a network check, and a wrong
estimate is cheap to fix but expensive to discover late (the app uses ``download_gb`` to warn
before a pull).

Both sources are read in decimal GB, the unit of the Ollama library page and of the catalog's
``download_gb``, so the numbers compare like for like.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import urllib.request

from teachers_teammate.domain.model_catalog import OllamaModelSpec

REGISTRY = "https://registry.ollama.ai"

TOLERANCE_FACTOR = 0.25
"""How far the catalog may be off, relative to the larger of the two numbers."""

TOLERANCE_GB = 0.5
"""How far the catalog may be off in absolute terms, for the small models."""


def fetch_manifest(tag: str) -> dict | None:
    """Return the registry manifest for *tag*, or ``None`` on any error."""
    name, _, version = tag.partition(":")
    request = urllib.request.Request(
        f"{REGISTRY}/v2/library/{name}/manifests/{version}",
        headers={
            "Accept": "application/vnd.docker.distribution.manifest.v2+json,"
            "application/vnd.oci.image.manifest.v1+json,"
            "application/vnd.docker.distribution.manifest.list.v2+json,"
            "application/vnd.oci.image.index.v1+json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return json.load(response)
    except Exception as exc:  # noqa: BLE001  # any network/parse error is reported per tag
        print(f"  ERROR fetching manifest: {exc}")
        return None


def manifest_total_gb(manifest: dict) -> float | None:
    """Return the summed layer size of a single-architecture manifest in decimal GB."""
    layers = manifest.get("layers") if isinstance(manifest, dict) else None
    if not layers:
        return None
    return sum(int(layer.get("size", 0)) for layer in layers) / 1e9


def resolve_to_single_manifest(manifest: dict) -> dict | None:
    """Follow a manifest index down to a single-architecture manifest."""
    seen: dict | None = manifest
    for _ in range(3):
        if not isinstance(seen, dict):
            return None
        if "layers" in seen:
            return seen
        manifests = seen.get("manifests")
        if not manifests:
            return None
        digest = manifests[0].get("digest")
        if not digest:
            return None
        request = urllib.request.Request(
            f"{REGISTRY}/v2/library/{digest.replace(':', '/')}/manifests/{digest}"
        )
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                seen = json.load(response)
        except Exception:  # noqa: BLE001
            return None
    return None


def hf_total_gb(tag: str) -> float | None:
    """Return the download size (decimal GB) of an ``hf.co/<user>/<repo>:<quant>`` tag.

    The GGUF file of that quant plus the smallest ``mmproj`` file, if the repository has one:
    the projector is needed to read images and is downloaded together with the model.
    """
    repo, _, quant = tag.removeprefix("hf.co/").partition(":")
    try:
        with urllib.request.urlopen(
            f"https://huggingface.co/api/models/{repo}?blobs=true", timeout=15
        ) as response:
            files = json.load(response).get("siblings", [])
    except Exception as exc:  # noqa: BLE001  # any network/parse error is reported per tag
        print(f"  ERROR fetching Hugging Face file list: {exc}")
        return None
    ggufs = [
        (str(file["rfilename"]).lower(), int(file.get("size") or 0))
        for file in files
        if str(file["rfilename"]).lower().endswith(".gguf")
    ]
    model = [size for name, size in ggufs if "mmproj" not in name and quant.lower() in name]
    if not model:
        print(f"  ERROR: no GGUF file for quant {quant!r}")
        return None
    projector = [size for name, size in ggufs if "mmproj" in name]
    return (sum(model) + (min(projector) if projector else 0)) / 1e9


def real_download_gb(tag: str) -> float | None:
    """Return how much a pull of *tag* really downloads, or ``None`` when it cannot be read."""
    if tag.startswith("hf.co/"):
        return hf_total_gb(tag)
    manifest = fetch_manifest(tag)
    if manifest is None:
        return None
    single = resolve_to_single_manifest(manifest)
    if single is None:
        print("  ERROR: manifest has no layers (index without a resolvable child?)")
        return None
    return manifest_total_gb(single)


@dataclass(frozen=True)
class SizeCheck:
    """One catalog entry's download estimate against the registry."""

    spec: OllamaModelSpec
    real_gb: float | None

    @property
    def matches(self) -> bool:
        """Return whether the catalog estimate is within the tolerance of *real_gb*."""
        if self.real_gb is None:
            return False
        return abs(self.real_gb - self.spec.download_gb) <= (
            TOLERANCE_FACTOR * max(self.spec.download_gb, self.real_gb) + TOLERANCE_GB
        )

    def report(self) -> str:
        """Return the two-line report of this entry."""
        head = f"{self.spec.tag} ({self.spec.category}, memory ~{self.spec.memory_gb:g} GB)"
        if self.real_gb is None:
            return f"{head}\n  download: could not be read"
        verdict = "OK" if self.matches else "MISMATCH"
        return (
            f"{head}\n"
            f"  download: catalog {self.spec.download_gb:g} GB vs registry {self.real_gb:.2f} GB"
            f"  [{verdict}] — note: {self.spec.note}"
        )


def check_size(spec: OllamaModelSpec) -> SizeCheck:
    """Read the real size of *spec* from its registry or Hugging Face file list."""
    return SizeCheck(spec, real_download_gb(spec.tag))


def report_real_size(tag: str) -> str:
    """Return one line with how much a pull of *tag* really downloads (no catalog estimate)."""
    real_gb = real_download_gb(tag)
    if real_gb is None:
        return f"{tag}: download size could not be read"
    return f"{tag}: download {real_gb:.2f} GB (not in the catalog, so nothing to compare)"
