#!/usr/bin/env python3
"""Generate installer assets from source PNGs.

Produces:
  teachers_teammate/assets/teachers_teammate.ico       — window/taskbar icon
  teachers_teammate/assets/teachers_teammate_welcome.bmp — MUI2 welcome-page side panel

Usage (from repo root):
    python tools/packaging/make_ico.py

Called automatically by tools/build/build_standalone.py on Windows before PyInstaller.
"""

from __future__ import annotations

from pathlib import Path
import sys

# MUI2 welcome/finish page side-panel size (pixels)
_WELCOME_BMP_SIZE = (164, 314)
_ICO_SIZES = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]

# Dark grey matching qdarkstyle; used to letterbox the installer welcome panel
_WELCOME_BG: tuple[int, int, int] = (43, 43, 43)


def _fit_into(img: object, target_w: int, target_h: int, bg: tuple) -> object:
    """Scale *img* proportionally to fit (target_w × target_h) and center on a *bg* canvas."""
    from PIL import Image  # noqa: PLC0415

    scale = min(target_w / img.width, target_h / img.height)  # type: ignore[union-attr]
    new_w = max(1, round(img.width * scale))  # type: ignore[union-attr]
    new_h = max(1, round(img.height * scale))  # type: ignore[union-attr]
    resized = img.resize((new_w, new_h), Image.LANCZOS)  # type: ignore[union-attr]
    mode = "RGBA" if len(bg) == 4 else "RGB"
    canvas = Image.new(mode, (target_w, target_h), bg)
    paste_x = (target_w - new_w) // 2
    paste_y = (target_h - new_h) // 2
    if resized.mode == "RGBA":
        canvas.paste(resized, (paste_x, paste_y), resized)
    else:
        canvas.paste(resized, (paste_x, paste_y))
    return canvas


def main() -> int:
    root = Path(__file__).resolve().parents[2]
    assets = root / "teachers_teammate" / "assets"

    try:
        from PIL import Image  # noqa: PLC0415
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: Pillow is required: {exc}", file=sys.stderr)
        return 1

    rc = 0

    # ── .ico from icon PNG ────────────────────────────────────────────────────
    icon_src = assets / "teachers_teammate_icon.png"
    icon_out = assets / "teachers_teammate.ico"
    if not icon_src.exists():
        print(f"ERROR: source image not found: {icon_src}", file=sys.stderr)
        rc = 1
    else:
        with Image.open(icon_src) as img:
            img.convert("RGBA").save(icon_out, format="ICO", sizes=_ICO_SIZES)
        print(f"Created: {icon_out}")

    # ── installer welcome BMP from splash PNG ─────────────────────────────────
    splash_src = assets / "teachers_teammate.png"
    splash_out = assets / "teachers_teammate_welcome.bmp"
    if not splash_src.exists():
        print(f"ERROR: source image not found: {splash_src}", file=sys.stderr)
        rc = 1
    else:
        with Image.open(splash_src) as img:
            bmp = _fit_into(img.convert("RGB"), *_WELCOME_BMP_SIZE, _WELCOME_BG)
        bmp.save(splash_out, format="BMP")  # type: ignore[union-attr]
        print(f"Created: {splash_out}")

    return rc


if __name__ == "__main__":
    raise SystemExit(main())
