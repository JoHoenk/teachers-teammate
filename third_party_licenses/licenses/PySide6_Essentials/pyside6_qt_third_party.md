# Qt third-party components bundled with Teacher's Teammate

Teacher's Teammate uses Qt 6.12.0 through PySide6 6.12.0 (`PySide6_Essentials`, `shiboken6`) under
the **GNU Lesser General Public License, version 3** (LGPL-3.0-only; the GPL-3.0-only text and the
Qt GPL exception are included for the licenses LGPL-3.0 builds on). Only these Qt parts ship:

- Libraries: QtCore, QtGui, QtWidgets, QtDBus (Linux), QtOpenGL, QtOpenGLWidgets, QtSvg,
  QtWaylandClient and QtWlShellIntegration (Linux), QtXcbQpa (Linux).
- Plugins: platform plugins (xcb, wayland, offscreen; Windows and macOS equivalents), image formats
  (GIF, ICO, JPEG, SVG, ICNS, TGA, TIFF, WBMP, WebP), icon engines, platform input contexts, the
  desktop-portal platform theme and the Wayland integrations.

No QtCharts, QtWebEngine, QtMultimedia, QtQuick, QtNetwork or other GPL-3-only or commercial-only Qt
module ships; the build fails if one does.

Source code of exactly these versions: see `NOTICE` (Qt 6.12.0 modules qtbase, qtsvg,
qtimageformats, qtwayland; PySide6 6.12.0 `pyside-setup`).

## Third-party components inside the Qt libraries

Qt's own sources bundle the components below (`qt_attribution.json` of each module at 6.12.0). The
list is the union over Windows, macOS and Linux for the parts listed above; the *Platforms* column
says where a component is compiled in. Each file in `qt_third_party/` holds the copyright notice and
the full license text of that component.

| Component | Used in | Platforms | License | File |
|---|---|---|---|---|
| Apache Tika MimeType Definitions | QtCore | all | Apache-2.0 | `qt_third_party/tika-mimetypes.txt` |
| BLAKE2 (reference implementation) | QtCore | all | CC0-1.0 OR Apache-2.0 | `qt_third_party/blake2.txt` |
| Data Compression Library (zlib) | QtCore, QtGui | Windows, macOS (Linux uses the system zlib) | Zlib | `qt_third_party/zlib.txt` |
| Easing Equations by Robert Penner | QtCore | all | BSD-3-Clause | `qt_third_party/easing.txt` |
| Efficient Binary-Decimal and Decimal-Binary Conversion Routines for IEEE Doubles | QtCore | all | BSD-3-Clause | `qt_third_party/doubleconversion.txt` |
| forkfd | QtCore | Linux, macOS | MIT | `qt_third_party/forkfd.txt` |
| MD4 | QtCore | all | CC0-1.0 | `qt_third_party/md4.txt` |
| MD5 | QtCore | all | CC0-1.0 | `qt_third_party/md5.txt` |
| PCRE2 | QtCore | all | LicenseRef-BSD-3-Clause-with-PCRE2-Binary-Like-Packages-Exception | `qt_third_party/pcre2.txt` |
| PCRE2 - Stack-less Just-In-Time Compiler | QtCore | all | BSD-2-Clause | `qt_third_party/pcre2-sljit.txt` |
| QEventDispatcher on macOS | QtCore | macOS | BSD-3-Clause | `qt_third_party/qeventdispatcher_cf.txt` |
| Secure Hash Algorithm SHA-1 | QtCore | all | LicenseRef-SHA1-Public-Domain | `qt_third_party/sha1.txt` |
| Secure Hash Algorithm SHA-3 - brg_endian | QtCore | all | BSD-2-Clause | `qt_third_party/sha3_endian.txt` |
| Secure Hash Algorithm SHA-3 - Keccak | QtCore | all | CC0-1.0 | `qt_third_party/sha3_keccak.txt` |
| Secure Hash Algorithms SHA-384 and SHA-512 | QtCore | all | BSD-3-Clause | `qt_third_party/rfc6234.txt` |
| SipHash Algorithm | QtCore | all | CC0-1.0 | `qt_third_party/siphash.txt` |
| TinyCBOR | QtCore | all | MIT | `qt_third_party/tinycbor.txt` |
| tl::expected | QtCore | all | CC0-1.0 | `qt_third_party/tlexpected.txt` |
| Unicode Character Database (UCD) | QtCore | all | Unicode-3.0 | `qt_third_party/unicode-character-database.txt` |
| Unicode Common Locale Data Repository (CLDR) | QtCore | all | Unicode-3.0 | `qt_third_party/unicode-cldr.txt` |
| Adobe Glyph List For New Fonts | QtGui | all | BSD-3-Clause | `qt_third_party/aglfn.txt` |
| Anti-aliasing rasterizer from FreeType 2 | QtGui | all | FTL OR GPL-2.0-only | `qt_third_party/grayraster.txt` |
| D3D12 Memory Allocator | QtGui | Windows | MIT | `qt_third_party/d3d12memoryallocator.txt` |
| Freetype 2 | QtGui | Windows, macOS (Linux uses the system FreeType) | FTL OR GPL-2.0-only | `qt_third_party/freetype.txt` |
| Freetype 2 - Bitmap Distribution Format (BDF) support | QtGui | Windows, macOS | MIT | `qt_third_party/freetype-bdf.txt` |
| Freetype 2 - Portable Compiled Format (PCF) support | QtGui | Windows, macOS | MIT AND MIT-open-group | `qt_third_party/freetype-pcf.txt` |
| Freetype 2 - zlib | QtGui | Windows, macOS | Zlib | `qt_third_party/freetype-zlib.txt` |
| HarfBuzz-NG | QtGui | all | MIT | `qt_third_party/harfbuzz-ng.txt` |
| LibJPEG-turbo | QtGui, image format plugin | all | IJG AND BSD-3-Clause | `qt_third_party/libjpeg.txt` |
| LibPNG | QtGui | all | Libpng AND libpng-2.0 | `qt_third_party/libpng.txt` |
| MD4C | QtGui | all | MIT | `qt_third_party/md4c.txt` |
| Mipmap generator for D3D12 | QtGui | Windows | MIT | `qt_third_party/rhi-miniengine-d3d12-mipmap.txt` |
| OpenGL ES 2 Headers | QtGui | all | MIT | `qt_third_party/opengl-es2-headers.txt` |
| OpenGL Headers | QtGui, QtOpenGL | all | MIT | `qt_third_party/opengl-headers.txt` |
| Pixman | QtGui | all | MIT | `qt_third_party/pixman.txt` |
| Smooth Scaling Algorithm | QtGui | all | BSD-2-Clause AND Imlib2 | `qt_third_party/smooth-scaling-algorithm.txt` |
| sRGB color profile icc file | QtGui | all | LicenseRef-ICC-License | `qt_third_party/icc-srgb-color-profile.txt` |
| Vulkan API Registry | QtGui | all | Apache-2.0 OR MIT | `qt_third_party/vulkan-xml-spec.txt` |
| Vulkan Memory Allocator | QtGui | all | MIT | `qt_third_party/vulkanmemoryallocator.txt` |
| WebGradients | QtGui | all | MIT | `qt_third_party/webgradients.txt` |
| Wintab API | QtGui | Windows | LicenseRef-Lcs-Telegraphics | `qt_third_party/wintab.txt` |
| X Server helper | QtGui | all | X11 AND HPND | `qt_third_party/xserverhelper.txt` |
| XSVG | QtGui | all | HPND-sell-variant | `qt_third_party/xsvg.txt` |
| libdbus-1 headers | QtDBus | Linux | AFL-2.1 OR GPL-2.0-or-later | `qt_third_party/libdbus-1-headers.txt` |
| XCB-XInput | QtXcbQpa, xcb platform plugin | Linux | MIT | `qt_third_party/xcb-xinput.txt` |
| Presentation Time Protocol | QtWaylandClient (Wayland protocol) | Linux | MIT | `qt_third_party/presentation-time.xml.txt` |
| Wayland Color Management Protocol | QtWaylandClient (Wayland protocol) | Linux | MIT | `qt_third_party/wayland-color-management-protocol.txt` |
| Wayland Dialog Protocol | QtWaylandClient (Wayland protocol) | Linux | MIT | `qt_third_party/xdg-dialog-v1.txt` |
| Wayland EGLStream Controller Protocol | QtWaylandClient (Wayland protocol) | Linux | MIT | `qt_third_party/wayland-eglstream-controller.txt` |
| Wayland Fractional Scale Protocol | QtWaylandClient (Wayland protocol) | Linux | MIT | `qt_third_party/fractional-scale-v1.txt` |
| Wayland Fullscreen Shell Protocol | QtWaylandClient (Wayland protocol) | Linux | MIT | `qt_third_party/wayland-fullscreen-protocol.txt` |
| Wayland IVI Extension Protocol | QtWaylandClient (Wayland protocol) | Linux | MIT | `qt_third_party/wayland-ivi-extension-protocol.txt` |
| Wayland KDE DBus Menu Protocol | QtWaylandClient | Linux | LGPL-2.1-or-later | `qt_third_party/appmenu.txt` |
| Wayland Linux Dmabuf Unstable V1 Protocol | QtWaylandClient (Wayland protocol) | Linux | MIT | `qt_third_party/wayland-linux-dmabuf-unstable-v1.txt` |
| Wayland Linux Dmabuf V1 Protocol | QtWaylandClient (Wayland protocol) | Linux | MIT | `qt_third_party/wayland-linux-dmabuf-v1.txt` |
| Wayland Pointer Gestures Protocol | QtWaylandClient (Wayland protocol) | Linux | MIT | `qt_third_party/wayland-pointer-gestures-protocol.txt` |
| Wayland Pointer Warp Protocol | QtWaylandClient (Wayland protocol) | Linux | MIT | `qt_third_party/wayland-pointer-warp-protocol.txt` |
| Wayland Primary Selection Protocol | QtWaylandClient (Wayland protocol) | Linux | MIT | `qt_third_party/wayland-primary-selection-protocol.txt` |
| Wayland Protocol | QtWaylandClient (Wayland protocol) | Linux | MIT | `qt_third_party/wayland-protocol.txt` |
| Wayland Scaler Protocol | QtWaylandClient (Wayland protocol) | Linux | MIT | `qt_third_party/wayland-scaler-protocol.txt` |
| Wayland Session Management Protocol | QtWaylandClient (Wayland protocol) | Linux | MIT | `qt_third_party/wayland-session-management-protocol.txt` |
| Wayland Tablet Protocol | QtWaylandClient (Wayland protocol) | Linux | MIT | `qt_third_party/wayland-tablet-protocol.txt` |
| Wayland Text Input Protocol | QtWaylandClient (Wayland protocol) | Linux | MIT | `qt_third_party/wayland-text-input-unstable-v3.txt` |
| Wayland Text Input Protocol v1 | QtWaylandClient (Wayland protocol) | Linux | MIT | `qt_third_party/wayland-text-input-unstable-v1.txt` |
| Wayland Text Input Protocol v2 | QtWaylandClient (Wayland protocol) | Linux | HPND | `qt_third_party/wayland-text-input-unstable-v2.txt` |
| Wayland Viewporter Protocol | QtWaylandClient (Wayland protocol) | Linux | MIT | `qt_third_party/wayland-viewporter-protocol.txt` |
| Wayland XDG Foreign Protocol | QtWaylandClient (Wayland protocol) | Linux | MIT | `qt_third_party/xdg-foreign-unstable-v2.txt` |
| Wayland XDG Output Protocol | QtWaylandClient (Wayland protocol) | Linux | MIT | `qt_third_party/wayland-xdg-output-protocol.txt` |
| Wayland XDG Shell Protocol | QtWaylandClient (Wayland protocol) | Linux | MIT | `qt_third_party/wayland-xdg-shell-protocol.txt` |
| Wayland XDG System Bell Protocol | QtWaylandClient (Wayland protocol) | Linux | MIT | `qt_third_party/wayland-xdg-system-bell-protocol.txt` |
| Wayland xdg-activation Protocol | QtWaylandClient (Wayland protocol) | Linux | MIT | `qt_third_party/wayland-xdg-activation.txt` |
| Wayland xdg-decoration Protocol | QtWaylandClient (Wayland protocol) | Linux | MIT | `qt_third_party/wayland-xdg-decoration-protocol.txt` |
| Wayland xdg-toplevel-icon Protocol | QtWaylandClient (Wayland protocol) | Linux | MIT | `qt_third_party/wayland-xdg-toplevel-icon-protocol.txt` |
| Wlr Data Control Unstable V1 Protocol | QtWaylandClient (Wayland protocol) | Linux | MIT | `qt_third_party/wlr-data-control-unstable-v1-protocol.txt` |
| Cocoa Platform Plugin | cocoa platform plugin | macOS | BSD-3-Clause | `qt_third_party/cocoa-platform-plugin.txt` |
| TIFF Software Distribution (libtiff) | image format plugin (TIFF) | all | libtiff | `qt_third_party/libtiff.txt` |
| WebP (libwebp) | image format plugin (WebP) | all | BSD-3-Clause | `qt_third_party/libwebp.txt` |

## SPDX license texts

`LGPL-3.0-only.txt`, `GPL-3.0-only.txt`, `GPL-2.0-only.txt`, `GFDL-1.3-no-invariants-only.txt`,
`Apache-2.0.txt`, `BSD-3-Clause.txt`, `Qt-GPL-exception-1.0.txt` and `LicenseRef-Qt-Commercial.txt`
next to this file are the texts the Qt modules themselves name. Qt is offered under LGPL-3.0, GPL-2.0,
GPL-3.0 or a commercial license; Teacher's Teammate uses the **LGPL-3.0** option.

## Updating

When PySide6 moves to a new Qt version: read `qt_attribution.json` of qtbase, qtsvg, qtimageformats and
qtwayland of that version (`https://download.qt.io/archive/qt/`), keep the components of the parts
listed above, regenerate `qt_third_party/` and this table, and change the version in this file and in
`NOTICE`.
