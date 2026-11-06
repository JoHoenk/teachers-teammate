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
* With ``--structured-output``, sends the proofreading answer format to every writing
  model and checks that the answer is valid JSON-schema output and that at least
  90 % of the returned sentences can be spliced back into the text.  The verdict
  is what ``OllamaModelSpec.structured_output`` records; enter it in the catalog
  by hand, as the download sizes are.  Needs a pulled model.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request

sys.path.insert(0, ".")

from teachers_teammate.domain.correction_review import splice  # noqa: E402
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


# ── Structured output (proofreading review) ───────────────────────────────────
#
# Three fixture texts cover the shapes the answer format has to handle: plain English, plain
# German and Markdown with formatting marks.  A model passes when every answer is valid
# JSON for the schema and at least 90 % of its sentences can be placed in the text.

_PLACEMENT_THRESHOLD = 0.90
_STRUCTURED_TIMEOUT = 300.0

_FIXTURES: tuple[tuple[str, str, str, str], ...] = (
    (
        "plain EN",
        "english",
        "Yesterday she have gone to the cinema with his friend. "
        "It was very nice because they was there for the first time.",
        "plain",
    ),
    (
        "plain DE",
        "german",
        "Gestern hat sie ins Kino gegangen mit sein Freundin. "
        "Es war sehr schön weil sie war dort zum ersten Mal.",
        "plain",
    ),
    (
        "Markdown",
        "english",
        "Dear **diary**, my ~~backpack~~ passport and me went to school.\n\n"
        "- it was *very* nice\n- we have see a film",
        "markdown",
    ),
)


def _review_prompt(language: str, text_format: str, text: str) -> tuple[str, str]:
    """Return ``(system, user)`` of the request the corrector would send for *text*."""
    from teachers_teammate.infrastructure.correction import (  # noqa: PLC0415
        _human_message,
        _instructions,
    )

    system = _instructions(
        "",
        language,
        level="B2",
        text_format=text_format,
        mode="review",
    )
    return system, _human_message(text, language, language, "review")


def _placeable(answer_json: str, source: str) -> tuple[int, int] | None:
    """Return ``(placed, total)`` sentences of an answer for *source*; ``None`` if it is invalid."""
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
        return 0, 0
    _text, unplaced = splice(source, review.sentences)
    return total - len(unplaced), total


def _check_structured_output(tag: str, url: str) -> bool:
    """Return whether *tag* answers the review answer format reliably."""
    from teachers_teammate.infrastructure.correction_schema import schema_json  # noqa: PLC0415
    from teachers_teammate.infrastructure.ollama_utils import OllamaClient  # noqa: PLC0415

    client = OllamaClient(url)
    ok = True
    for name, language, text, text_format in _FIXTURES:
        try:
            system, user = _review_prompt(language, text_format, text)
            raw = client.chat(
                tag,
                user,
                options={"temperature": 0},
                timeout=_STRUCTURED_TIMEOUT,
                format=schema_json(),
                system=system,
            )
        except Exception as exc:  # noqa: BLE001  # a missing or failing model must not stop the other models
            print(f"{tag} {name}: request failed ({exc})")
            ok = False
            continue
        counts = _placeable(raw, text)
        if counts is None:
            print(f"{tag} {name}: answer did not validate against the schema")
            ok = False
            continue
        placed, total = counts
        if not total:
            # The fixtures are full of mistakes: an empty list is a useless answer.
            print(f"{tag} {name}: valid answer, but it lists no sentences [NO SENTENCES]")
            ok = False
            continue
        ratio = placed / total
        verdict = "OK" if ratio >= _PLACEMENT_THRESHOLD else "TOO FEW PLACED"
        print(f"{tag} {name}: {placed}/{total} sentences placed [{verdict}]")
        ok = ok and ratio >= _PLACEMENT_THRESHOLD
    return ok


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--ollama",
        action="store_true",
        help="Also ask a local Ollama server (OLLAMA_URL, default "
        "http://127.0.0.1:11434) for each model's capabilities.",
    )
    parser.add_argument(
        "--structured-output",
        action="store_true",
        help="Send the proofreading answer format to every writing model and report whether "
        "it answers it reliably (sets OllamaModelSpec.structured_output).",
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
    if args.structured_output:
        print("\n── structured output (proofreading review) ──")
        for spec in OLLAMA_CATALOG:
            if spec.category != "general":
                continue
            verdict = _check_structured_output(spec.tag, args.ollama_url)
            print(f"  → catalog: structured_output={verdict} (currently {spec.structured_output})")
            if verdict != spec.structured_output:
                failures += 1

    print(f"\n{len(OLLAMA_CATALOG) - failures}/{len(OLLAMA_CATALOG)} tags verified")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
