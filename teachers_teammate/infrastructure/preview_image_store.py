"""Stable storage for preprocessed preview images.

`PreviewImageStore` manages one kind of derived file: the preprocessed image
created during OCR preprocessing that is shown in the GUI preview panel.

Responsibilities
----------------
- `PreviewImageStore` — owns the canonical on-disk location for preview images:
  - computes stable paths (one image per source-document stem)
  - copies a freshly preprocessed image into its canonical location so that its
    path can be stored in `DocumentState` and survive across runs
  - deletes debug step images (`*_step*.jpg`) and intermediate PDF page images
    (`*_page*.png`) when debug mode is off

Why a separate store?
---------------------
The preprocessor (`HandwritingPreprocessor`) writes its output to a temp
directory and does not know about the long-term cache layout.  This store
provides the stable canonical path and performs the copy so the path can be
recorded in `DocumentState`.  In practice, when the preprocessor and the store
use the same root directory, the copy is a no-op (source == target).

For TXT inputs there is no preview image — `DocumentState.preview_img` stays
empty and this store is never called.

What this module does NOT do
-----------------------------
- It does not perform image transformation — that is `HandwritingPreprocessor`.
- It does not manage JSON state records — that is `StateRepository`.
- It does not decide *when* a preview is stale — that is `domain/freshness.py`.
"""

from __future__ import annotations

from dataclasses import dataclass
import glob
import os
from pathlib import Path
import shutil


@dataclass(frozen=True)
class PreviewImagePaths:
    """Canonical artifact file paths for one document stem."""

    preview_img: Path


class PreviewImageStore:
    """Filesystem service for stable preview image paths and I/O."""

    def __init__(self, artifact_root: Path) -> None:
        self._root = artifact_root
        self._root.mkdir(parents=True, exist_ok=True)

    @property
    def root(self) -> Path:
        return self._root

    def paths_for_stem(self, stem: str) -> PreviewImagePaths:
        return PreviewImagePaths(
            preview_img=self._root / f"{stem}_preprocessed.png",
        )

    def persist_preview_image(self, *, stem: str, source_path: Path) -> Path:
        """Copy *source_path* to the canonical preview path for *stem* and return it."""
        target = self.paths_for_stem(stem).preview_img
        target.parent.mkdir(parents=True, exist_ok=True)
        if source_path.resolve() != target.resolve():
            shutil.copy2(source_path, target)
        return target

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
            # Unlink first: copying onto a hard link would write through to the source file.
            target.unlink(missing_ok=True)
            try:
                os.link(source_path, target)
            except OSError:  # unsupported filesystem or cross-device link
                shutil.copy2(source_path, target)
        self.prune_original_images(stem, keep=target)
        return target

    def prune_original_images(self, stem: str, *, keep: Path | None = None) -> None:
        """Delete stale ``<stem>_original.*`` files except *keep* (best-effort)."""
        keep_resolved = keep.resolve() if keep is not None else None
        for stale in self._root.glob(f"{glob.escape(stem)}_original.*"):
            if keep_resolved is not None and stale.resolve() == keep_resolved:
                continue
            try:
                stale.unlink(missing_ok=True)
            except OSError:
                pass

    def delete_preview_artifacts(
        self, preview_img: str, source_image: str, original_img: str = ""
    ) -> None:
        """Delete preview image files that were recorded in a now-invalidated state.

        Only deletes files that are non-empty paths and actually exist.
        Safe to call with empty strings.
        """
        for file_path in {preview_img, source_image, original_img}:
            if file_path:
                Path(file_path).unlink(missing_ok=True)

    def cleanup_intermediate_images(self, *, keep_debug: bool) -> None:
        """Delete step snapshots and intermediate PDF page images unless *keep_debug* is set."""
        if keep_debug:
            return
        for pattern in ("*_step*.jpg", "*_page*.png"):
            for file in self._root.glob(pattern):
                file.unlink(missing_ok=True)
