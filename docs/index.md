# Overview

Teacher's Teammate is a desktop app that turns scanned handwriting and printed text into editable
text. Give it a folder of PDFs, photos or text files; it reads every file, can proofread it with
AI, and saves the results as Word documents. Everything can run on your computer; an online AI
service is optional.

## How it works

Each file goes through these steps:

1. **Collect** — every PDF, JPG, JPEG, PNG and TXT file in the folder and its sub-folders.
2. **Prepare images** — pages are straightened, turned upright, flattened and evened out where
   needed. Text files skip this and the next step.
3. **Recognize text** — the text of each page is read, always on your computer.
4. **Remove personal information** (optional) — names, email addresses, phone numbers, bank
   account numbers, street addresses and dates are replaced with stand-ins before any text goes to
   an AI.
5. **Proofread** (optional) — an AI corrects the text and explains each change.
6. **Review the content** (optional) — an AI reports on what the text says: gaps, unclear passages.
7. **Export** — one Word document per file, or plain or anonymized text files.

> **Privacy is your responsibility.** With an **online service** for proofreading, the text is sent
> to that provider's servers. You must make sure this is allowed under privacy law (such as GDPR)
> and get any consent needed from the people named in the documents.

## Quick start

1. Run the **Setup Assistant**. It opens on the first start and downloads what your computer needs
   (see *Setting up* in *Using the app*).
2. Open a folder: drag it onto the window or click the folder button.
3. Under **3 What should happen**, tick the steps you want, for example **Proofread**.
4. Select files (or none, for all) and click **Run Selected**.
5. Double-click a row to open it in the **Check** tab, fix what is left, and export.

Settings are saved and restored on the next start. Installation and system requirements are on the
[project page](https://github.com/JoHoenk/teachers-teammate#installation). Every release has a
`SHA256SUMS` file and a build attestation to check a download; the commands are in the
[deployment guide](https://github.com/JoHoenk/teachers-teammate/blob/main/docs/deployment.md#checksums-and-provenance).
Which files are signed, where they come from and what the app sends on its own: the
[code signing policy](code_signing_policy.md).

## License

Teacher's Teammate is free software, released under the **GNU General Public License, version 3
or later**: free to use, share and change, and if you pass it on, changed or not, pass on its
source too, or say where to get it. It bundles the Qt user-interface toolkit (via **PySide6**)
under the **GNU LGPL v3**.
The app's license and the license and notice texts of every bundled component are inside the app
under **Help → Licenses**.

```{toctree}
:hidden:
:maxdepth: 2
:caption: User Guide

self
using_the_app
advanced_user_guide
```

```{toctree}
:hidden:
:maxdepth: 2
:caption: Developer Guide

development
gui
extension_points
error_handling
deployment
testing
```

```{toctree}
:hidden:
:maxdepth: 2
:caption: Software Documentation

requirements/use_cases_rst
test_specs
code_signing_policy
```
