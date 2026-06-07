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
#. **Export** — results are saved as Word documents (.docx), one per input file.

## Privacy — your responsibility

:::{Warning}
When AI proofreading is turned on with an **online service** (such as OpenAI, Anthropic, or Google), your document text is sent to that provider's servers. You are solely responsible for ensuring this is permitted under applicable privacy laws (such as GDPR) and for obtaining any required consent from the people whose data appears in the documents.
:::

## Quick workflow

#. Open an **input folder** — drag it onto the window or click the folder button.
#. Text recognition is already set to Ollama by default — just make sure Ollama is running on your computer (see *Set up text recognition* below).
#. Optionally turn on **AI proofreading** in the settings panel.
#. Select the files you want to process (or leave nothing selected to process everything), then click **Run Selected**.
#. Click any row in the results list to open the **Preview** tab — the text is fully editable, so you can fix any remaining mistakes by hand before sending the text to correction or export.

For detailed guidance on choosing the best settings for your documents, see the {doc}`Advanced User Guide <advanced_user_guide>`.

---

## Getting started

### Install Teacher's Teammate

#. Go to the [releases page](https://github.com/JoHoenk/teachers-teammate/tags) and download the installer for your operating system.
#. Run the installer like any other program.
#. Launch **Teacher's Teammate** from the Start menu (Windows), the Applications folder (macOS), or your application launcher (Linux).

:::{note}
**Windows — security notice:** Windows may show a "Windows protected your PC" message when running the installer. Click **More info**, then **Run anyway**. This warning appears only because the installer does not carry a paid security certificate — not because anything is wrong with the app. The full build process is publicly visible on [GitHub](https://github.com/JoHoenk/teachers-teammate), so you can inspect every step yourself.
:::

:::{note}
**macOS — security notice:** macOS may say the app "cannot be verified" and refuse to open it. Go to **System Settings → Privacy & Security** and click **Open Anyway**. This restriction appears only because the app does not have a paid Apple Developer certificate — not because it is unsafe. The build is fully transparent on [GitHub](https://github.com/JoHoenk/teachers-teammate).
:::

Optional features — such as privacy protection (removing personal information before proofreading) — can be added and downloaded from inside the app. See {doc}`Using the App <using_the_app>`, section *Installing extra features*.

Your settings are saved automatically and restored every time you start the app.

### Set up text recognition

By default, Teacher's Teammate recognises text using a local AI program called **Ollama**, which runs on your computer and never sends data anywhere without your permission. If Ollama is not yet installed, download it from [ollama.com/download](https://ollama.com/download) and run its installer like any other program.

Once Ollama is running, open **Advanced Settings → Downloads…** inside Teacher's Teammate, go to the **Ollama Models** tab, and download the recommended model (`deepseek-ocr:latest`). After that, set your input and output folders and click **Run Selected** — that's it.

## License

Teacher's Teammate is open-source software, released under the **Apache License 2.0**.
The app bundles the Qt user-interface toolkit (via **PySide6**) under the **GNU LGPL v3**;
Qt is dynamically linked and the complete source code is publicly available, so the
toolkit can be replaced with a compatible build. You can read the full license and notice
texts for every bundled component inside the app under **Advanced Settings → About →
Third-Party Licenses**.

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
deployment
testing
```

```{toctree}
:hidden:
:maxdepth: 2
:caption: Software Documentation

requirements/use_cases_rst
test_specs
```
