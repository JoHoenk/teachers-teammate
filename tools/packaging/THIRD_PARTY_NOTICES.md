# Third-Party Notices — Installers

Third-party code embedded in the **installers** built from this directory. The application inside
them is covered by `third_party_licenses/` (in the app: Help → Licenses).

| Platform | Built with | Third-party code in the installer |
| --- | --- | --- |
| Windows | `windows.nsi`, compiled by NSIS `makensis` into `teachers-teammate-<version>-w64-setup.exe` | the NSIS stub, plugins and LZMA compression module (below) |
| macOS | `make_dmg.sh`, Apple's `hdiutil` | none |
| Linux | `make_deb.sh`, `dpkg-deb` | none; the system's libraries come from the distribution's packages named in `Depends:` |

## NSIS (Nullsoft Scriptable Install System)

The NSIS stub and the plugins and macros `windows.nsi` uses (Modern UI 2, LogicLib, nsDialogs,
System, FileFunc, WordFunc) are under the zlib/libpng license, reproduced below. This notice is the
acknowledgment that license calls "appreciated".

The installer compresses with NSIS's **LZMA** module, licensed under the Common Public License 1.0
(CPL-1.0). NSIS grants an exception that lets code link to the LZMA module without coming under
CPL; changes to the module itself stay under CPL-1.0. Full CPL-1.0 text and the exception:
https://github.com/kichik/nsis/blob/master/COPYING

Upstream license: https://github.com/kichik/nsis/blob/master/COPYING

```
COPYRIGHT
---------

Copyright (C) 1999-2026 Contributors

More detailed copyright information can be found in the individual source code files.

APPLICABLE LICENSES
-------------------

* All NSIS source code, plug-ins, documentation, examples, header files and graphics, with the exception of the compression modules and where otherwise noted, are licensed under the zlib/libpng license.

* The zlib compression module for NSIS is licensed under the zlib/libpng license.

* The bzip2 compression module for NSIS is licensed under the bzip2 license.

* The LZMA compression module for NSIS is licensed under the Common Public License version 1.0.

ZLIB/LIBPNG LICENSE
-------------------

This software is provided 'as-is', without any express or implied warranty. In no event will the authors be held liable for any damages arising from the use of this software.

Permission is granted to anyone to use this software for any purpose, including commercial applications, and to alter it and redistribute it freely, subject to the following restrictions:

      1. The origin of this software must not be misrepresented; you must not claim that you wrote the original software. If you use this software in a product, an acknowledgment in the product documentation would be appreciated but is not required.

      2. Altered source versions must be plainly marked as such, and must not be misrepresented as being the original software.

      3. This notice may not be removed or altered from any source distribution.
```

