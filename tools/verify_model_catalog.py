"""Developer tool: verify the Ollama model catalog against the real registry.

NOT run in CI — it needs network access to the Ollama registry (and optionally a
local Ollama server).  Run it after editing
``teachers_teammate/domain/model_catalog.py``:

    bazel run //tools:verify_model_catalog            # registry checks only
    bazel run //tools:verify_model_catalog -- --ollama # + capabilities via local server

What it does:

* For every catalog tag, fetches the manifest from
  ``https://registry.ollama.ai/v2/library/<name>/manifests/<tag>`` and reports
  the sum of layer sizes as the real download size, comparing it with the
  catalog ``download_gb`` estimate (tolerance: ±25 % + 0.5 GB).
* For ``hf.co/<user>/<repo>:<quant>`` tags, sums the GGUF file of that quant
  (plus the smallest ``mmproj`` file, if the repository has one) from the
  Hugging Face file list instead.
* With ``--ollama``, additionally asks a local Ollama server (``ollama show``)
  for each model's capabilities and prints whether it can read images — this is
  how the gemma4:12b open question (plan §10) can be settled.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request

sys.path.insert(0, ".")

from teachers_teammate.domain.model_catalog import OLLAMA_CATALOG  # noqa: E402

_REGISTRY = "https://registry.ollama.ai"
_TOLERANCE_FACTOR = 0.25
_TOLERANCE_GB = 0.5


def _fetch_manifest(tag: str) -> dict | None:
    """Return the registry manifest for *tag*, or None on any error."""
    name, _, version = tag.partition(":")
    url = f"{_REGISTRY}/v2/library/{name}/manifests/{version}"
    request = urllib.request.Request(
        url,
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


def _manifest_total_gb(manifest: dict) -> float | None:
    """Return the summed layer size of a (single-arch) manifest in decimal GB.

    Decimal on purpose: it is the unit of the Ollama library page and of the
    catalog's ``download_gb``, so the numbers compare like for like.
    """
    layers = manifest.get("layers") if isinstance(manifest, dict) else None
    if not layers:
        return None
    total = sum(int(layer.get("size", 0)) for layer in layers)
    return total / 1e9


def _resolve_to_single_manifest(manifest: dict) -> dict | None:
    """Follow a manifest index down to a single-arch manifest."""
    seen: dict = manifest
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
            f"{_REGISTRY}/v2/library/{digest.replace(':', '/')}/manifests/{digest}"
        )
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                seen = json.load(response)
        except Exception:  # noqa: BLE001
            return None
    return None


def _hf_total_gb(tag: str) -> float | None:
    """Return the download size (decimal GB) of an ``hf.co/<user>/<repo>:<quant>`` tag."""
    repo, _, quant = tag.removeprefix("hf.co/").partition(":")
    url = f"https://huggingface.co/api/models/{repo}?blobs=true"
    try:
        with urllib.request.urlopen(url, timeout=15) as response:
            files = json.load(response).get("siblings", [])
    except Exception as exc:  # noqa: BLE001  # any network/parse error is reported per tag
        print(f"  ERROR fetching Hugging Face file list: {exc}")
        return None
    ggufs = [(f["rfilename"].lower(), int(f.get("size") or 0)) for f in files]
    ggufs = [(name, size) for name, size in ggufs if name.endswith(".gguf")]
    model = [size for name, size in ggufs if "mmproj" not in name and quant.lower() in name]
    if not model:
        print(f"  ERROR: no GGUF file for quant {quant!r}")
        return None
    projector = [size for name, size in ggufs if "mmproj" in name]
    return (sum(model) + (min(projector) if projector else 0)) / 1e9


def _check_download_size(spec) -> bool:
    print(f"{spec.tag} ({spec.category}, memory ~{spec.memory_gb:g} GB)")
    if spec.tag.startswith("hf.co/"):
        real_gb = _hf_total_gb(spec.tag)
        if real_gb is None:
            return False
    else:
        manifest = _fetch_manifest(spec.tag)
        if manifest is None:
            return False
        single = _resolve_to_single_manifest(manifest)
        real_gb = _manifest_total_gb(single) if single is not None else None
        if real_gb is None:
            print("  ERROR: manifest has no layers (index without a resolvable child?)")
            return False
    ok = abs(real_gb - spec.download_gb) <= (
        _TOLERANCE_FACTOR * max(spec.download_gb, real_gb) + _TOLERANCE_GB
    )
    verdict = "OK" if ok else "MISMATCH"
    print(
        f"  download: catalog {spec.download_gb:g} GB vs registry {real_gb:.2f} GB  "
        f"[{verdict}] — note: {spec.note}"
    )
    return ok


def _check_capabilities(tag: str, url: str) -> None:
    from teachers_teammate.infrastructure.ollama_utils import OllamaClient

    caps = OllamaClient(url).model_capabilities(tag)
    if caps is None:
        print(f"{tag}: capabilities could not be read (model not pulled?)")
        return
    vision = "vision" in caps
    print(f"{tag}: capabilities={sorted(caps)} → {'READS images' if vision else 'text only'}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--ollama",
        action="store_true",
        help="Also ask a local Ollama server (OLLAMA_URL, default "
        "http://127.0.0.1:11434) for each model's capabilities.",
    )
    parser.add_argument("--ollama-url", default="http://127.0.0.1:11434")
    args = parser.parse_args()

    failures = 0
    for spec in OLLAMA_CATALOG:
        if not _check_download_size(spec):
            failures += 1
    if args.ollama:
        print("\n── capabilities via local Ollama server ──")
        for spec in OLLAMA_CATALOG:
            _check_capabilities(spec.tag, args.ollama_url)

    print(f"\n{len(OLLAMA_CATALOG) - failures}/{len(OLLAMA_CATALOG)} tags verified")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
