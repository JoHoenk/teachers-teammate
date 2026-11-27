"""Stable storage for preprocessed preview images.

`PreviewImageStore` manages one kind of derived file: the preprocessed images
created during OCR preprocessing that are shown in the GUI preview panel.

Responsibilities
----------------
- `PreviewImageStore` — owns the canonical on-disk location for preview images:
  - computes stable paths (one image per source-document stem and page)
  - copies freshly preprocessed images into their canonical location so that
    their paths can be stored in `DocumentState` and survive across runs
  - deletes debug step images (`*_step*.jpg`) and intermediate PDF page images
    (`*_page<digits>.png`) when debug mode is off

Why a separate store?
---------------------
The preprocessor (`StepPipeline`) writes its output to a temp
directory and does not know about the long-term cache layout.  This store
provides the stable canonical path and performs the copy so the path can be
recorded in `DocumentState`.  In practice, when the preprocessor and the store
use the same root directory, the copy is a no-op (source == target).

For TXT inputs there is no preview image — `DocumentState.preview_img` stays
empty and this store is never called.

What this module does NOT do
-----------------------------
- It does not perform image transformation — that is `StepPipeline` with its steps.
- It does not manage JSON state records — that is `StateRepository`.
- It does not decide *when* a preview is stale — that is `domain/freshness.py`.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
import logging
import os
from pathlib import Path
import re
import shutil

_logger = logging.getLogger(__name__)

#: The run's own intermediate page images: ``<anything>_page<digits>[_preprocessed].png``.
#: Matched on the whole file name, so a document whose stem ends in ``_page`` keeps its
#: stored pages (``week_page_preprocessed.png`` has no digits after ``_page``).
_INTERMEDIATE_PAGE = re.compile(r".*_page\d+(_preprocessed)?\.png")

#: Only the rendered pages, before preparation: ``<anything>_page<digits>.png``.
_RENDERED_PAGE = re.compile(r".*_page\d+\.png")


@dataclass(frozen=True)
class PreviewImagePaths:
    """Canonical artifact file paths for one document stem."""

    preview_img: Path


class PreviewImageStore:
    """Filesystem service for stable preview image paths and I/O."""

    def __init__(self, artifact_root: Path, *, keep_debug: bool = False) -> None:
        self._root = artifact_root
        self._keep_debug = keep_debug
        self._root.mkdir(parents=True, exist_ok=True)

    @property
    def root(self) -> Path:
        return self._root

    def paths_for_stem(self, stem: str) -> PreviewImagePaths:
        return PreviewImagePaths(
            preview_img=self._root / f"{stem}_preprocessed.png",
        )

    def paths_for_page(self, stem: str, number: int) -> Path:
        """Return the canonical path of page *number* (1-based) of *stem*.

        Page 1 keeps the name it always had (``<stem>_preprocessed.png``): the results table
        and the Word builder read it, and a document recognized before this existed has only
        that page.
        """
        if number == 1:
            return self.paths_for_stem(stem).preview_img
        return self._root / f"{stem}_p{number}.png"

    def persist_preview_image(self, *, stem: str, source_path: Path) -> Path:
        """Copy *source_path* to the canonical preview path for *stem* and return it."""
        target = self.paths_for_stem(stem).preview_img
        target.parent.mkdir(parents=True, exist_ok=True)
        if source_path.resolve() != target.resolve():
            shutil.copy2(source_path, target)
        return target

    def persist_page_images(self, *, stem: str, sources: Sequence[Path]) -> list[Path]:
        """Store every prepared page of *stem* in page order and return their paths.

        Page 1 goes through :meth:`persist_preview_image`, so a failure there is the error it
        always was.  A later page that cannot be stored is not worth failing the file over:
        the pages written so far are removed again and only page 1 is returned, so the state
        never lists a page that is not on disk.
        """
        if not sources:
            return []
        first = self.persist_preview_image(stem=stem, source_path=sources[0])
        stored = [first]
        try:
            for number, source in enumerate(sources[1:], start=2):
                stored.append(self._link_or_copy(source, self.paths_for_page(stem, number)))
        except OSError:
            for extra in stored[1:]:
                extra.unlink(missing_ok=True)
            self.prune_page_images(stem, keep=1)
            return [first]
        self.prune_page_images(stem, keep=len(stored))
        return stored

    def prune_page_images(self, stem: str, *, keep: int) -> None:
        """Delete stored pages of *stem* beyond *keep* (best-effort).

        Matched with a full-name regex rather than a glob: the stem ``Anna_p1`` has its own
        ``Anna_p1_preprocessed.png`` and ``Anna_p1_p2.png``, which must survive when the pages
        of ``Anna`` are pruned.
        """
        pattern = re.compile(rf"{re.escape(stem)}_p(\d+)\.png")
        for candidate in self._root.iterdir():
            match = pattern.fullmatch(candidate.name)
            if match is None or int(match.group(1)) <= keep:
                continue
            try:
                candidate.unlink(missing_ok=True)
            except OSError as exc:
                _logger.debug("Cannot delete the stale page preview %s: %s", candidate, exc)

    def persist_original_image(self, *, stem: str, source_path: Path) -> Path:
        """Store the un-preprocessed page *source_path* next to the preview image.

        Keeps the original file suffix (``<stem>_original.<ext>``) so the file stays a
        valid image for whatever format the input was.  Used by the GUI to toggle
        between the original and the preprocessed page.  The file is hard-linked when
        possible (no extra I/O or disk usage) and copied otherwise; any earlier
        ``<stem>_original.*`` file with a different suffix is removed.
        """
        target = self._root / f"{stem}_original{source_path.suffix.lower() or '.png'}"
        target.parent.mkdir(parents=True, exist_ok=True)
        if not (target.exists() and source_path.resolve() == target.resolve()):
            self._link_or_copy(source_path, target)
        self.prune_original_images(stem, keep=[target])
        return target

    def persist_original_images(self, *, stem: str, sources: Sequence[Path]) -> list[Path]:
        """Store the page as it was scanned for every prepared page of *stem*.

        Page 1 keeps the name it always had (``<stem>_original.<ext>``); pages 2…N are
        ``<stem>_o<N>.<ext>``.  A later page that cannot be stored is not worth failing the
        file over: the pages stored so far are kept and returned, so the list is always a
        prefix of *sources* and the state never names a page that is not on disk.
        """
        if not sources:
            return []
        stored = [self.persist_original_image(stem=stem, source_path=sources[0])]
        for number, source in enumerate(sources[1:], start=2):
            try:
                stored.append(
                    self._link_or_copy(source, self.paths_for_original(stem, number, source))
                )
            except OSError:
                break
        self.prune_original_images(stem, keep=stored)
        return stored

    def paths_for_original(self, stem: str, number: int, source: Path) -> Path:
        """Return the canonical path of the original of page *number* (2-based here).

        Page 1 is not covered: its name keeps the suffix of the file it came from, which
        only :meth:`persist_original_image` knows.
        """
        return self._root / f"{stem}_o{number}{source.suffix.lower() or '.png'}"

    @staticmethod
    def _link_or_copy(source: Path, target: Path) -> Path:
        """Hard-link *source* to *target*, copying when the filesystem cannot link."""
        # Unlink first: copying onto a hard link would write through to the source file.
        target.unlink(missing_ok=True)
        try:
            os.link(source, target)
        except OSError:  # unsupported filesystem or cross-device link
            shutil.copy2(source, target)
        return target

    def prune_original_images(self, stem: str, *, keep: Sequence[Path] = ()) -> None:
        """Delete stale original pages of *stem* except *keep* (best-effort).

        Matched with full-name patterns rather than a glob: the stem ``Anna_o2`` has its own
        ``Anna_o2_original.png`` and ``Anna_o2_preprocessed.png``, which must survive when the
        originals of ``Anna`` are pruned.
        """
        kept = {path.resolve() for path in keep}
        page = re.compile(rf"{re.escape(stem)}_o(\d+)\..+")
        for stale in self._root.iterdir():
            if not (
                stale.name.startswith(f"{stem}_original.") or page.fullmatch(stale.name) is not None
            ):
                continue
            if stale.resolve() in kept:
                continue
            try:
                stale.unlink(missing_ok=True)
            except OSError as exc:
                _logger.debug("Cannot delete the stale page image %s: %s", stale, exc)

    def delete_preview_artifacts(
        self,
        preview_img: str,
        source_image: str,
        original_img: str = "",
        page_images: Sequence[str] = (),
        original_images: Sequence[str] = (),
    ) -> None:
        """Delete preview image files that were recorded in a now-invalidated state.

        Only deletes files that are non-empty paths and actually exist.
        Safe to call with empty strings.
        """
        for file_path in {preview_img, source_image, original_img, *page_images, *original_images}:
            if file_path:
                Path(file_path).unlink(missing_ok=True)

    def cleanup_intermediate_images(self, *, keep_prepared_pages: bool = False) -> None:
        """Delete step snapshots and intermediate PDF page images unless debug mode keeps them.

        A preview run has no later step: the prepared pages it wrote are its result, so with
        *keep_prepared_pages* only the pages as they were rendered go.
        """
        if self._keep_debug:
            return
        intermediate = _RENDERED_PAGE if keep_prepared_pages else _INTERMEDIATE_PAGE
        for file in self._root.glob("*_step*.jpg"):
            file.unlink(missing_ok=True)
        for file in self._root.iterdir():
            if intermediate.fullmatch(file.name):
                file.unlink(missing_ok=True)
