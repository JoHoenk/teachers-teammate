# PySide6 — Bundled Qt Third-Party Components

PySide6 ships pre-compiled Qt 6 binaries that themselves bundle several third-party
libraries. You must comply with their licenses for anything you distribute.

## Qt modules actually imported by Teachers Teammate

```
PySide6.QtCore      — all files
PySide6.QtGui       — all files
PySide6.QtWidgets   — all files
PySide6.QtCharts    — all files
```

Detected via `grep -r "from PySide6" teachers_teammate/` (June 2026, Qt 6.11).

## Third-party components in those modules

Source: https://doc.qt.io/qt-6/licenses-used-in-qt.html

### QtCore

| Component | License |
|-----------|---------|
| Apache Tika MIME-type definitions | Apache-2.0 |
| BLAKE2 | CC0-1.0 or Apache-2.0 |
| zlib | Zlib |
| Easing equations by Robert Penner | BSD-3-Clause |
| Double Conversion routines | BSD-3-Clause |
| MD4 / MD5 | Public Domain |
| PCRE2 | BSD-3-Clause (with exception) |
| SHA-1, SHA-3, SHA-384/512 | Public Domain or CC0-1.0 |
| SipHash algorithm | CC0-1.0 |
| TinyCBOR | MIT |
| Unicode Character Database (UCD) | Unicode License Agreement |
| Unicode CLDR | Unicode License v3 |
| forkfd | MIT |

### QtGui

| Component | License |
|-----------|---------|
| Adobe Glyph List | BSD-3-Clause |
| FreeType 2 | Freetype Project License or GPL-2.0 |
| DejaVu Fonts | Bitstream Vera Font License |
| HarfBuzz-NG | MIT |
| libjpeg-turbo | Independent JPEG Group License and BSD-3-Clause |
| libpng | Libpng License |
| Pixman | MIT |
| Vulkan Memory Allocator | MIT |
| WebGradients | MIT |

### QtWidgets / QtCharts

No additional third-party components beyond those already listed for QtCore and
QtGui are documented in the Qt 6.11 license pages for these modules.

## Notes

- The actual bundled binaries live in the `PySide6_Essentials` and `PySide6_Addons`
  packages (dependencies of the `PySide6` meta-package), not in the small stub wheel
  downloaded here.
- Verify the current list at https://doc.qt.io/qt-6/licenses-used-in-qt.html whenever
  upgrading PySide6, as bundled components can change between Qt releases.
