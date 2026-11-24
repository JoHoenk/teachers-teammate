# Overview

## What is Teacher's Teammate?

Teacher's Teammate is a desktop app that reads your scanned documents and turns the handwriting or printed text into editable text. You provide a folder of PDFs, photos, or images, and the app processes every file automatically — reading the text, optionally cleaning it up with AI, and saving the results as Word documents (.docx).

All processing can run entirely on your computer, with nothing sent anywhere. If you prefer, you can connect an online AI service for higher-quality proofreading.

---

## How it works

Teacher's Teammate processes each document through a series of steps:

| What you provide | Image preparation | Text recognition | Remove personal info | AI proofreading | Word document |
|---|:---:|:---:|:---:|:---:|:---:|
| PDF, photo (JPG/PNG) | ✓ | ✓ | optional | optional | optional |
| Text file (.txt) | — | (already text) | optional | optional | optional |

1. **Collect** — the app finds every supported file (PDF, JPG, JPEG, PNG, TXT) in the folder you choose, including its sub-folders.
2. **Prepare images** — each page is straightened, turned upright, flattened and evened out where it needs it, so the text is recognised as accurately as possible.
3. **Text recognition** — the app recognises the text in each page or image.
4. **Remove personal information** (optional) — names, phone numbers, email addresses, and bank account numbers are replaced with stand-ins before any text is sent to an AI.
5. **Proofread** (optional) — an AI reviews the text, corrects errors and explains each change.
6. **Export** — results are saved as Word documents (.docx), one per input file, or exported as plain or anonymized text files.

## Privacy — your responsibility

> **Warning:** When AI proofreading is turned on with an **online service** (such as Mistral), your document text is sent to that provider's servers. You are solely responsible for ensuring this is permitted under applicable privacy laws (such as GDPR) and for obtaining any required consent from the people whose data appears in the documents.

## Quick workflow

1. Run the **Setup Assistant** once (it opens on the first start): it checks what handwriting recognition needs on your computer and downloads it (see *Set up text recognition* below).
2. Open an **input folder** — drag it onto the window or click the folder button.
3. Under **3 What should happen** in the left panel, tick the steps you want, for example **Proofread**.
4. Select the files you want to process (or leave nothing selected to process everything), then click **Run Selected**.
5. Double-click any row in the results list to open it in the **Check** tab — the text is fully editable, so you can fix any remaining mistakes by hand before you export it.

For detailed guidance on choosing the best settings for your documents, see the *Advanced User Guide*.

---

## Getting started

### Install Teacher's Teammate

1. Go to the [releases page](https://github.com/JoHoenk/teachers-teammate/releases/latest) and download the installer for your operating system.
2. Run the installer like any other program. On Windows its last page offers **Start Teacher's Teammate**; leave it ticked and the app opens right away, with the Setup Assistant on the first start.
3. Launch **Teacher's Teammate** from the Start menu (Windows), the Applications folder (macOS), or your application launcher (Linux).

> **Linux:** `teachers-teammate_<version>_amd64.deb` installs the app and adds a launcher entry; install it with `sudo apt install ./teachers-teammate_<version>_amd64.deb`.

> **Windows — security notice:** Windows may show a "Windows protected your PC" message when running the installer. Click **More info**, then **Run anyway**. This warning appears only because the installer does not carry a paid security certificate — not because anything is wrong with the app. What is signed, where the files come from and what the app does with your data is written down in the [code signing policy](https://github.com/JoHoenk/teachers-teammate/blob/main/docs/code_signing_policy.md).

> **macOS — security notice:** macOS may say the app "cannot be verified" and refuse to open it. Go to **System Settings → Privacy & Security** and click **Open Anyway**. This restriction appears only because the app does not have a paid Apple Developer certificate — not because it is unsafe.

Optional features — such as privacy protection (removing personal information before proofreading) — can be added from inside the app. See *Installing extra features* in *Using the App*.

Your settings are saved automatically and restored every time you start the app.

Every release also ships a `SHA256SUMS` file and a build attestation, so you or your IT department can check that a download is the file this project built; the commands are in the [deployment guide](https://github.com/JoHoenk/teachers-teammate/blob/main/docs/deployment.md#checksums-and-provenance).

### System requirements

| | Windows | macOS | Linux |
|---|---|---|---|
| **Version** | 10 (version 1809 or newer) or 11 | 15 Sequoia or newer | Ubuntu 24.04+, Debian 13+, Mint 22+ (glibc 2.39 or newer) |
| **Processor** | x64 (Windows 11 on ARM: x64 emulation, not tested) | Apple Silicon (M1 or newer) | x86_64 |
| **Download** | setup `.exe` | `.dmg` | `.deb` |

**Main memory:** 8 GB. A recommended model needs 2.5 to 9.5 GB while it runs; with a graphics card it runs
there instead, and the app shows what fits your computer before anything is downloaded.

**Free disk space:** about 700 MB for the app, which brings the built-in reader (31 MB) and the
page models (39 MB) with it, plus 1.6 to 8 GB per model that runs in Ollama. The app names the
size of every download before it starts.

> **Ubuntu 22.04, Mint 21 and Debian 12** are not supported by this version: use v0.5.x.

### Set up text recognition

The first time you start the app, the **Setup Assistant** opens and asks three plain questions: the language of your class, what your students hand in (handwritten pages or typed texts), and where the proofreading may run. It then shows you what it will set and what it will download, and you press **Set up** once. The downloads run in the background and the main window shows how far they are.

Teacher's Teammate reads handwriting in one of two ways. The default is a reading model that runs in a local AI program called **Ollama**, which runs on your computer and never sends data anywhere without your permission. If Ollama is not installed yet, the assistant says so and offers **Get Ollama…**, which opens [ollama.com/download](https://ollama.com/download); run its installer like any other program. Once Ollama is running, the assistant's line turns green by itself and it downloads the right reading and writing models for your computer. Ollama is also what runs AI proofreading on your own computer. The **built-in reader** (about 31 MB) runs inside the app and needs no extra program; choose it under **Settings → AI models**, and its files are already there.

For photographed pages, **Automatic** image preparation can turn a sideways page upright and flatten a curved one. This needs two small **page models** (about 39 MB), which come with the app, whichever way the pages are read.

You can run the assistant again at any time from **Tools → Setup Assistant…**. Everything it sets can also be changed by hand in the Settings window.

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
