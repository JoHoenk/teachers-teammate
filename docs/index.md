# Overview

:::{image} assets/teachers_teammate.png
:width: 220px
:align: center
:alt: Teacher's Teammate logo
:::

## What is Teacher's Teammate?

Teacher's Teammate is a desktop app that reads your scanned documents and turns the handwriting or printed text into editable text. You provide a folder of PDFs, photos, or images, and the app processes every file automatically — reading the text, optionally cleaning it up with AI, and saving the results as Word documents (.docx).

All processing can run entirely on your computer, with nothing sent anywhere. If you prefer, you can connect an online AI service for higher-quality proofreading.

---

## How it works

Teacher's Teammate processes each document through a series of steps:

| What you provide | Image cleanup | Text recognition | Remove personal info | AI proofreading | Word document |
|---|:---:|:---:|:---:|:---:|:---:|
| PDF, photo (JPG/PNG) | ✓ | ✓ | optional | optional | optional |
| Text file (.txt) | — | (already text) | optional | optional | optional |

#. **Collect** — the app finds every supported file (PDF, JPG, JPEG, PNG, TXT) in the folder you choose, optionally including sub-folders.
#. **Clean up images** — each image is adjusted for brightness and contrast so the text is recognised as accurately as possible.
#. **Text recognition** — the app recognises the text in each page or image.
#. **Remove personal information** (optional) — names, phone numbers, email addresses, and bank account numbers are replaced with stand-ins before any text is sent to an AI.
#. **Proofread** (optional) — an AI reviews the text and corrects any errors.
#. **Export** — results are saved as Word documents (.docx), one per input file, or exported as plain or anonymized text files.

## Privacy — your responsibility

:::{Warning}
When AI proofreading is turned on with an **online service** (such as OpenAI, Anthropic, or Google), your document text is sent to that provider's servers. You are solely responsible for ensuring this is permitted under applicable privacy laws (such as GDPR) and for obtaining any required consent from the people whose data appears in the documents.
:::

## Quick workflow

#. Open an **input folder** — drag it onto the window or click the folder button.
#. Run the **Setup Assistant** once (it opens on the first start): it checks what handwriting recognition needs on your computer and downloads it (see *Set up text recognition* below).
#. Optionally turn on **AI proofreading** in the settings panel.
#. Select the files you want to process (or leave nothing selected to process everything), then click **Run Selected**.
#. Click any row in the results list to open the **Preview** tab — the text is fully editable, so you can fix any remaining mistakes by hand before sending the text to correction or export.

On a fresh install the **Setup Assistant** does steps 2 and 3 for you — see *Set up text recognition* below.

For detailed guidance on choosing the best settings for your documents, see the {doc}`Advanced User Guide <advanced_user_guide>`.

---

## Getting started

### Install Teacher's Teammate

#. Go to the [releases page](https://github.com/JoHoenk/teachers-teammate/releases/latest) and download the installer for your operating system.
#. Run the installer like any other program. On Windows its last page offers **Start Teacher's Teammate**; leave it ticked and the app opens right away, with the Setup Assistant on the first start.
#. Launch **Teacher's Teammate** from the Start menu (Windows), the Applications folder (macOS), or your application launcher (Linux).

:::{note}
**Linux — two downloads:** `teachers-teammate_<version>_amd64.deb` installs the app into
`/opt/teachers-teammate` and adds a launcher entry; install it with
`sudo apt install ./teachers-teammate_<version>_amd64.deb`, which also pulls in the system
libraries it needs. `teachers-teammate-<version>-linux-x86_64.tar.gz` is the portable build:
unpack it anywhere and run `./teachers-teammate`.
:::

:::{note}
**Windows — security notice:** Windows may show a "Windows protected your PC" message when running the installer. Click **More info**, then **Run anyway**. This warning appears only because the installer does not carry a paid security certificate — not because anything is wrong with the app. The full build process is publicly visible on [GitHub](https://github.com/JoHoenk/teachers-teammate), so you can inspect every step yourself. What is signed, where the files come from and what the app does with your data is written down in the {doc}`code signing policy <code_signing_policy>`.
:::

:::{note}
**macOS — security notice:** macOS may say the app "cannot be verified" and refuse to open it. Go to **System Settings → Privacy & Security** and click **Open Anyway**. This restriction appears only because the app does not have a paid Apple Developer certificate — not because it is unsafe. The build is fully transparent on [GitHub](https://github.com/JoHoenk/teachers-teammate).
:::

Optional features — such as privacy protection (removing personal information before proofreading) — can be added and downloaded from inside the app. See {doc}`Using the App <using_the_app>`, section *Installing extra features*.

Your settings are saved automatically and restored every time you start the app.

### System requirements

| | Windows | macOS | Linux |
|---|---|---|---|
| **Version** | 10 (version 1809 or newer) or 11 | 14.4 Sonoma or newer | Ubuntu 24.04+, Debian 13+, Mint 22+ (glibc 2.39 or newer); other distributions with glibc 2.39 or newer: the `.tar.gz` |
| **Processor** | x64 (Windows 11 on ARM: x64 emulation, not tested) | Apple Silicon (M1 or newer) | x86_64 |
| **Download** | setup `.exe` | `.dmg` | `.deb`, `.tar.gz` |

**Main memory:** 8 GB. A model needs 2.5 to 7.2 GB while it runs; with a graphics card it runs
there instead, and the app shows what fits your computer before anything is downloaded.

**Free disk space:** about 700 MB for the app, which brings the built-in reader (31 MB) and the
page models (39 MB) with it, plus 1.6 to 7.2 GB per model that runs in Ollama. The app names the
size of every download before it starts.

**Python wheel:** Python 3.14.

:::{note}
**Ubuntu 22.04, Mint 21 and Debian 12:** stay on v0.5.x. The app's update check offers the newer
release, but apt refuses it (`libc6 (>= 2.39)`) and the portable tarball does not start.
:::

### Check your download

Every release ships a `SHA256SUMS` file next to the installers, and every installer carries a
build-provenance attestation. If you or your IT department want to be sure a download is the
file this project built:

```bash
# Linux, in the folder that holds the download and SHA256SUMS
sha256sum -c SHA256SUMS --ignore-missing
# macOS
shasum -a 256 -c SHA256SUMS --ignore-missing
# Any system, with the GitHub CLI (https://cli.github.com)
gh attestation verify teachers-teammate-<version>-w64-setup.exe --repo JoHoenk/teachers-teammate
```

The first two compare the file against the checksum list. The third asks GitHub whether the file
was built by this repository's release workflow, which is stronger: it names the workflow and the
commit the file came from.

### Set up text recognition

The first time you start the app, the **Setup Assistant** opens and asks three plain questions: the language of your class, what your students hand in (handwritten pages or typed texts), and where the proofreading may run. It then shows you what it will set and what it will download, and you press **Set up** once. The downloads run in the background and the main window shows how far they are.

Teacher's Teammate reads handwriting in one of two ways. The **built-in reader** (about 31 MB) runs inside the app and needs no extra program; choose it under **Settings → AI models**, and its files are already there. The default is a reading model that runs in a local AI program called **Ollama**, which runs on your computer and never sends data anywhere without your permission. If Ollama is not installed yet, the assistant says so and offers **Get Ollama…**, which opens [ollama.com/download](https://ollama.com/download); run its installer like any other program. Once Ollama is running, the assistant's line turns green by itself — no click needed — and it downloads the right reading and writing models for your computer. Ollama is also what runs AI proofreading on your own computer.

For photographed pages, **Automatic** image preparation can turn a sideways page upright and flatten a curved one. This needs two small **page models** (about 39 MB), which come with the app, whichever way the pages are read.

You can run the assistant again at any time from **Tools → Setup Assistant…**. It starts from your saved answers and keeps a model that already works, so running it a second time changes nothing you did not ask for. Everything it sets can also be changed by hand in the Settings window.

## License

Teacher's Teammate is open-source software, released under the **Apache License 2.0**.
The app bundles the Qt user-interface toolkit (via **PySide6**) under the **GNU LGPL v3**.
The Qt libraries are separate files in the application folder
(`_internal/PySide6/Qt/lib`), not compiled into the program, so they can be replaced with a
compatible build; the complete source code of Teacher's Teammate is publicly available, which
satisfies the LGPL's relinking requirement. You can read the full license and notice texts for
every bundled component inside the app under **Help → Third-Party Licenses**.

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
extension_points
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
