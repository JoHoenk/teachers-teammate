# Advanced User Guide

Text recognition, AI models, image preparation, anonymization and better proofreading, in more
depth than *Using the App*. The settings file and the command line are at the end.

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

A server on your own network counts as another computer you control: an address in a private
range (`192.168.…`, `10.…`, `172.16.…`–`172.31.…`, Tailscale's `100.64.…`), a name ending in
`.local`, `.lan`, `.fritz.box`, `.intern`, `.internal`, `.home` or `.corp`, or a name without a
dot. Any other address counts as an online service, and the app refuses to read pages there and
asks before it sends text. The app does not look names up, so a name under your school's own
domain (`ollama.myschool.de`) counts as online: enter the server's address instead.
Plain `http://` to another computer or server is not encrypted; the app says so next to the engine
or service you pick, not as an error.

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
(Apache-2.0) that come with the app and run on the processor, without a server or a graphics
card. They read about fifty languages, print better than handwriting. Choose **Built-in reader
(PP-OCR)** under **Settings → AI models…**.

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

Each computer type has up to two picks: the model with the **best quality** it runs, and a
**faster** one that costs a little accuracy. The app names them beside the model in **Settings → AI
models…** and in the Setup Assistant. If your computer runs the best-quality model only very
slowly (a graphics card under 8 GB) or has too little main memory for it, the faster model takes
its place.

| Model | Memory | Download | Basic | 8 GB | 16 GB | Notes |
|---|---|---|---|---|---|---|
| `gemma4:12b` | ~9.5 GB | 8 GB | too large | ★ best quality | ★ best quality | The most accurate model the app offers; on an 8 GB card a page takes about a minute |
| `gemma4:e2b` | ~3.6 GB | 4.6 GB | ★ best quality | faster | faster | Smallest Gemma 4 build; also the one for a computer without a graphics card |
| `gemma4:e4b` | ~5 GB | 6.6 GB | works | works | works | Fast; without a graphics card about 2 to 3 minutes a page (16 GB of main memory or more) |
| `qwen3.5:9b` | ~7.2 GB | 6.6 GB | too large | works | works | Another model family; also reads scans |
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

- **Original page** sends the pages as they are, to check what the preparation changes.

**Preview…** shows a page before and after, and says what was done, for example "Fixed for
reading: page turned, shadows evened out" (or "As scanned — no fixes needed").

**Page models.** *Turn* and *Flatten* use two small models (PaddlePaddle, Apache-2.0) that come
with the app. Without them *Automatic* skips those two steps.

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

**Words to always hide, words to never hide.** Under **Settings → Privacy… → Personal info:
patterns**, *Always hidden* lists words that are replaced wherever they stand, even when no
detection finds them, and *Never hidden* lists words that are sent as written, even when a
detection finds them. Both ignore capital letters and match whole words. A word in both lists is
sent as written. The **Check** tab adds to them: select a word in **What was read** and choose
**Hide this**, or **Don't hide this** on a stand-in. It applies to the next proofreading; a result
made before says "made with other privacy settings".

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
- **Task text is left alone.** The header, the tasks and the form labels repeat on every sheet of a
  worksheet; the student's own words do not. From three documents on, a run recognises them and
  sends only the student's text: the task lines come back as they were read, grey in the Word file.
  **Options…** of **Proofread** has the switch **Don't proofread the task text**. Below three
  documents nothing is recognised; **Proofread this after all** on the strip above the text sends
  it for one document (see *Task text* in *Using the app*).

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

## Comparing models on one text

**Tools → Compare proofreading models…**, or **Compare models…** in the Check tab of a document you
have read. The window lists the writing models of this computer (ticked unless they answered this
text already) and the online services whose key you set.

- **Cost and time.** *Run selected* asks the ticked models one after another, one request each; a
  stored answer of the same settings costs none, and neither does the document's own proofreading
  when it is that model's. The time shown in a row before the run is an estimate from the speed
  measurements of this computer; the time a model took includes loading it, as the first file of a
  run does. A comparison counts as a run: the stored results cannot be deleted meanwhile and no
  second run starts.
- **Privacy.** An answer goes the way a run's text goes: with **Remove personal info** on, names are
  hidden before an online model is asked, and what was sent is stored with the result. Nothing is
  sent before *Run selected*, and before the text goes to an online model you are asked the same
  question as before a run.
- **What is compared.** The transcription as it stands, with your reading fixes, its notation and
  the batch language — exactly what a run sends. Notes, level, explanation language and corrections
  only apply too; nothing but the provider and the model differs between the columns.
- **What the figures say.** One text cannot rank models. The numbers show what each of them changed,
  how much of it they agree on and how long they took; the same text may come back differently at a
  later run. Larger models change more correct sentences; the *Changes* tab shows every one of them
  next to each other.
- **Chance.** *Run again* asks the chosen model a second time; both answers are kept. Its column
  then splits its changes into *In both runs* and *Only once* — with a temperature above 0 a model
  may fix a place in one answer and leave it in the next, so "only once" is a difference you cannot
  count on. *Changes* writes "(only once)" beside such a wording. One extra run costs one request
  and a second copy of the answer in the cache.
- **Marking a change.** The *Changes* tab lets you call each option right or wrong: right means the
  student was wrong there and the wording belongs in the text, wrong that the student's own words
  are. A mark is on the wording, so models that agreed are judged together, and a second click
  takes it back. With the marks the *Overview* counts per model what it got right, what it got
  wrong, and the right changes it missed — a change it neither made nor covered with another right
  change. The score (F0.5) appears only when every change of that model is marked, because an
  unmarked change says nothing about the model.
- **Keeping a model.** *Use … for proofreading* writes **Settings → AI models…**'s provider and
  model. The document keeps the proofreading it has, so nothing you worked on is lost.

Answers live in the cache (`compare/` below the storage folder), so *Delete stored results…* and
the retention remove them. Your marks do not: they are your own work, `compare_marks/<text hash>.json`
in the data folder, one file per text, holding the place and a hash of the wording — no text.

---

## Where the app keeps its files

| System | Settings (`ocr.toml`) | Data, logs and stored results |
|---|---|---|
| Windows | `%LOCALAPPDATA%\teachers_teammate` | `%LOCALAPPDATA%\teachers_teammate` |
| macOS | `~/Library/Application Support/teachers_teammate` | `~/Library/Application Support/teachers_teammate` |
| Linux | `~/.config/teachers_teammate` | `~/.local/share/teachers_teammate` |

The log file is `logs/teachers-teammate.log` in the data folder, rotated: four files of about a
megabyte, the oldest dropped. **Help → Open Log Folder** opens the folder it sits in; that file is
what to send to whoever helps you. It holds file names, step names and error messages — never a
document's text or an access key.

**Settings → General → Log detail** decides how much is written: **Normal** keeps what a run did,
**Detailed** adds what a developer needs and makes the file grow faster. A start can decide instead
of the setting: `--log-level errors|normal|detailed`, the environment variable
`TEACHERS_TEAMMATE_LOG_LEVEL`, or `--diagnose`, which means Detailed.

## If the app starts slowly or stops answering

Start the app once with `--diagnose` (Windows: copy the shortcut and add it after the target).
Do what was slow, close the app, and send `logs/diagnostics.log` from the data folder to the
developers. It records how long each part of the start took and what the app was busy with while
it did not answer — no documents, file names or settings.

## The settings file

The window saves its settings in `ocr.toml`, one setting per line:

```toml
ocr_model = "glm-ocr:q8_0"
correction_provider = "mistral_eu"
anonymization_enabled = true
```

An unknown value falls back to the default, with a warning in the log. A file the app cannot read
is copied to `ocr.toml.broken` and the app starts with default settings; your settings stay in that
copy.

---

## The command line

The command line runs the same processing as the window, reads the same `ocr.toml`, and its options
override the file. `teachers-teammate --help` lists every option; `--version` prints the version
and license.

```bash
teachers-teammate -i ./scans -o ./results --docx
```

**Access keys** saved under **Settings → Online services** work here too; a key in the environment
wins. The keychain is asked only when a run needs an online service (macOS may ask for permission
once).

**Exit code:** `0` when no file failed, `1` when a file failed or the run could not start. A scan
skipped because recognition is off does not count.

**Before the first file** it runs the same check as the window's line above **Run Selected**. When
something is missing it says what and how to fix it, and exits with `1` without touching a file:

- the built-in reader's model files are missing: the command that downloads them;
- a model is missing in Ollama: `ollama pull <model>`;
- no key for an online service: the environment variable to set, or the key in Settings;
- Ollama is not running, not installed or not answering at `--ollama-url`;
- an add-on is missing, out of date, or waits for a restart;
- text files would be written into the input folder: use `-o` with another folder;
- recognition is off and no queued file has text: add `--recognition` or queue text files.

Too little free memory only warns; `--ignore-memory-check` skips the check. When Ollama is busy with
another model, the first file waits and a line says so.
