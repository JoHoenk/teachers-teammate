# Advanced User Guide

This guide goes deeper than *Using the App*: how text recognition works, which AI models suit
your computer, what the image preparation and the anonymization do, and how to get better
proofreading. Everything here is set in the app's window; the settings file and the command line
are at the end.

## Text recognition

Pages are only ever read on a computer you control. A page cannot be anonymised before it is
read, so online services are not offered for reading, and Ollama *cloud* models (names ending in
`cloud`) are refused.

### Ollama (default)

Ollama runs AI reading models on your computer and gives the best results for handwriting and
poor scans. The app finds Ollama on your own computer by itself. For Ollama on another computer,
enter its address under **Settings → This computer**, for example:

```text
http://192.168.1.50:11434
```

**Find…** on that page looks for a server:

- **This computer** — when the saved address does not answer, the app looks here too and offers a
  server it finds with **Use it**. It never switches by itself.
- **Windows, when the app runs in WSL** — Ollama on Windows has to listen on the network. Set
  this environment variable on Windows and restart Ollama:

  ```text
  OLLAMA_HOST=0.0.0.0
  ```

  For the privacy box, the Windows computer counts as this computer.
- **The network** — only after **Search the network…** and a confirmation. The app asks every
  address of your local network whether Ollama answers; nothing else is sent. Some school
  networks notice such a search, so ask your IT department first. A server found there counts as
  another computer.

### The built-in reader (PP-OCR)

Two small models from the [PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR) project
(Apache-2.0) that run inside the app, on the processor, without a server or a graphics card. They
read about fifty languages and read print better than handwriting. Choose **Built-in reader
(PP-OCR)** under **Settings → AI models…**. The files (31 MB) come with the app.

---

## AI models

### Your computer type

The app sorts computers into three types, shown as **My computer** under **Settings → This
computer**: a basic computer or small graphics card, a graphics card with about 8 GB, and one with
16 GB or more. It detects the type from the graphics card (on Apple Silicon, from the shared
memory). For Ollama on another computer it cannot see the hardware and assumes 8 GB; pick the
type yourself if you know better. The type decides which models are recommended; it never changes
the model you chose.

### Reading models

| Model | Memory | Download | Basic | 8 GB | 16 GB | Notes |
|---|---|---|---|---|---|---|
| `hf.co/prithivMLmods/chandra-ocr-2-GGUF:Q8_0` | ~6.5 GB | 5.5 GB | too large | ★ best | ★ best | Default. Full pages with tables and handwriting; keeps the layout. Free for personal use; a school using it as an institution should check the [licence](https://github.com/datalab-to/chandra#commercial-usage) |
| `glm-ocr:q8_0` | ~2.5 GB | 1.6 GB | ★ best | good | good | Small and fast; printed and mixed-language pages |
| `qwen3-vl:8b` | ~7 GB | 6.1 GB | too large | good | good | Strong on handwriting |
| `deepseek-ocr:latest` | ~7 GB | 6.7 GB | too large | good | good | Dense, structured pages |
| `qwen3.5:9b` | ~7 GB | 6.6 GB | too large | good | good | A writing model that also reads scans |
| `qwen2.5vl:7b` | ~7 GB | 6.0 GB | too large | works | works | Older version of qwen3-vl |

### Writing models

| Model | Memory | Download | Basic | 8 GB | 16 GB | Notes |
|---|---|---|---|---|---|---|
| `gemma4:e4b` | ~5 GB | 6.6 GB | ★ best | ★ best | works | Default. Fast; without a graphics card about 2 to 3 minutes a page (16 GB of main memory or more) |
| `gemma4:12b` | ~9.5 GB | 8 GB | too large | too large | ★ best | Best for 16 GB graphics cards |
| `qwen3.5:9b` | ~7.2 GB | 6.6 GB | too large | good | good | Another model family; also reads scans |
| `qwen3:4b` | ~4 GB | 2.5 GB | works | works | works | Small, but thinks first, so slow |
| `gpt-oss:20b` | ~14 GB | 13.8 GB | too large | too large | works | Large; asked to think as little as possible |
| `mistral:latest` | ~5.5 GB | 4.4 GB | too large | works | works | Does not reliably give explanations |
| `mistral-nemo:12b` | ~8.5 GB | 7.1 GB | too large | too large | works | Does not reliably give explanations |

These ratings look at the graphics card only. On your own computer the app also knows the main
memory, so in **Settings → Add-ons…** a model that is too large for the graphics card shows as
**Slower** or **Very slow** when the rest fits in main memory, and as **Won't fit** when it does
not. Before a run the app checks the memory that is free right now and warns you if it is not
enough.

### Other models

Any model from the [Ollama library](https://ollama.com/library) can be used: add it under
**Settings → Add-ons… → AI models for this computer**. The app asks Ollama what the model can do —
one that reads images may be used for reading and writing, a text-only one for writing only.

Some models are published on [Hugging Face](https://huggingface.co/) instead. Add them with the
`hf.co/` prefix, for example:

```text
hf.co/prithivMLmods/chandra-ocr-2-GGUF:Q8_0
```

The part after the colon is the *quantisation*: `Q4…` versions are smaller and faster, `Q8_0`
versions need about twice the memory and stay closer to the original model. If a model does not
fit your computer, choose a smaller model or a `Q4` version.

---

## Image preparation

Set under **Settings → Scan quality…**:

- **Automatic** (recommended) does only what a page needs, in this order:

  | Step | Acts when | What it does |
  |---|---|---|
  | Page | a sheet-shaped outline brighter than its surroundings is found | cuts the page out and corrects the perspective |
  | Flatten | the image is a photo, or the page's edges are curved | flattens the page (*page models*) |
  | Turn | the page is turned by 90°, 180° or 270° | turns it upright (*page models*) |
  | Size | the page is larger than A4 at 300 DPI | scales it down |
  | Light | the paper is unevenly lit | evens out shadows |
  | Tilt | the writing is tilted by 0.3° to 10° | straightens it |
  | Gray | the page is read by Ollama | sends a gray page (the built-in reader gets colour) |

- **Original page** sends the pages as they are, to check what the preparation changes.

**Preview…** shows a page before and after, and says what was done, for example "Prepared: page
turned, shadows evened out".

**Page models.** *Turn* and *Flatten* need two small models (39 MB, PaddlePaddle, Apache-2.0),
which come with the app. Without them *Automatic* skips those two steps.

**PDFs** (under **Expert settings**). **PDF DPI** is the resolution a PDF page is turned into an
image at: 300 (the default) is right for reading; 150 is faster for a draft. **PDF text layer**
decides when a PDF's own text is used instead of reading the page: *auto* (the default) only when
every page is clean typed text, *always* whenever every page has text, *never* always reads the
page.

---

## Removing personal information

With **Remove personal info** ticked, every item found is replaced by a numbered stand-in such as
`[PERSON_1]`; the same value always gets the same stand-in. After the proofreading the stand-ins
are put back.

| Type | Found by | Examples |
|---|---|---|
| Person names | AI name detection (spaCy) | "Alice Müller", "Dr. Smith" |
| Email addresses | pattern | `alice@example.com` |
| Phone numbers | pattern | `+49 30 12345`, `(030) 9876 543` |
| IBANs | pattern | `DE89 3704 0044 0532 0130 00` |
| Street addresses (German formats) | pattern | `Hauptstraße 12`, `10115 Berlin` |
| Dates | pattern | `01.02.2010`, `5. März 2024`, `March 5, 2024` |

> **Anonymization is not a guarantee.** Name detection misses names it was not trained on,
> patterns do not cover every format, and reading errors hide information from both. Check
> **What is sent for this file…** before you send sensitive documents to an online service.

**Limits of the patterns.** Streets and postcodes are recognised in German formats only. Every
date is replaced, so the proofreading does not see `9.11.1989` in a history essay, and a section
number like `Aufgabe 1.2.10` may be taken for a date. If that gets in the way, remove the `DATE`
patterns under **Settings → Privacy… → Personal info: patterns**.

**Name detection models.** Install spaCy and a model under **Settings → Add-ons… → Name
detection**. The multilingual model (`xx_ent_wiki_sm`) is a safe default; the German
(`de_core_news_sm`) and English (`en_core_web_sm`) models are more accurate for their language. A
second model under **Settings → Privacy… → Personal info: detection** finds names the first one
misses.

---

## Getting better proofreading

- **Set the Level to your class** (step 2). The AI keeps the student's own words and does not
  raise the language above this level, so at A2 it fixes errors without turning simple sentences
  into polished ones.
- **Write Notes for the AI** (**Options…** of **Proofread**) when the class or task has rules the
  AI cannot know: "Year 9, second foreign language", "ignore missing capital letters in headings",
  "the text is a draft, only mark grammar".
- **Explanations in another language.** For a foreign-language class, set **Explanation
  language** to the students' own language, so they understand why something was changed.
- **Corrections only (faster)** when you only need the corrected text, or when a model often
  comes back without explanations (models without `explanations ✓`). It takes about a third of the
  time.
- **A larger model** gives better corrections if your computer holds it: on a 16 GB graphics card,
  `gemma4:12b` instead of `gemma4:e4b`. **Settings → This computer** has a speed test.
- **Check what the AI was asked.** **What the AI is asked** (in **Options…** of **Proofread**) and
  **What is sent for this file…** show the exact instructions and text.

**Content review.** The review answers whatever its **Instructions** ask (**Options…** of
**Review content**). Write them for your task, for example:

```text
The students wrote a letter of application. Check whether it names the job, gives two reasons
why the writer is suitable, and ends with a request for an interview. List what is missing.
```

If the review should use another model than the proofreading — for example a larger one for long
essays — tick **Use a different model for content review** under **Settings → AI models…**.

**Online proofreading.** With an online service (such as Mistral (EU)), keep **Remove personal
info** on. Access keys are kept in your system's keychain (macOS Keychain, Windows Credential
Manager, the Linux keyring). Without one, the key is stored unencrypted in the app's settings, and
the page says so.

---

## Comparing reading setups (Benchmark)

**Tools → Benchmark…** reads one document with one setup at a time and keeps every result, so you
can compare engines, models and image preparation on your own pages. It only reads — no
proofreading, no Word files.

1. **Choose document…** — a PDF, image or text file.
2. Pick the engine, model and image preparation, then **Run & store**.
3. Repeat with another setup. Each run is listed under **Stored runs**.
4. Select one run → **Set as A**, another → **Set as B**. **Compare** shows the page, both texts,
   the differences and a similarity score.

The similarity says how much two runs agree, not which one is right — judge that against the
page. The newest 20 runs per document are kept, also when you rename the file; **Delete** and
**Clear all runs for this document** remove them. A benchmark and a batch can run at the same time,
but they share the graphics card.

---

## If the app starts slowly or stops answering

Start the app once with diagnostics switched on. On Windows, copy the shortcut and add this after
the target; on macOS and Linux, add it to the command:

```text
--diagnose
```

Do what was slow, close the app, and send the file `diagnostics.log` to the developers. It is in
the app's data folder:

| System | Folder |
|---|---|
| Windows | `%LOCALAPPDATA%\teachers_teammate` |
| macOS | `~/Library/Application Support/teachers_teammate` |
| Linux | `~/.local/share/teachers_teammate` |

It records how long each part of the start took and what the program was busy with while the
window did not answer. It contains no documents, file names or settings.

---

## The settings file

The window saves its settings in `ocr.toml`:

| System | Folder |
|---|---|
| Windows | `%LOCALAPPDATA%\teachers_teammate` |
| macOS | `~/Library/Application Support/teachers_teammate` |
| Linux | `~/.config/teachers_teammate` |

Each setting is one line, for example:

```toml
ocr_model = "glm-ocr:q8_0"
correction_provider = "mistral_eu"
anonymization_enabled = true
```

A value the app does not know falls back to the default, with a warning in the log.

## The command line

The command line runs the same processing as the window and reads the same `ocr.toml`; its
options override the file. To see every option:

```bash
teachers-teammate --help
```

A typical run:

```bash
teachers-teammate -i ./scans -o ./results --docx
```

It exits with `0` when no file failed and `1` when a file failed or the run could not start. A
scan skipped because recognition is off does not change the exit code.
