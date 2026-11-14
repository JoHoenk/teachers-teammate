# Advanced User Guide

## Who this guide is for

This guide is for technically-minded users who want to configure the processing pipeline precisely, choose between different text recognition engines and AI model combinations deliberately, and understand how each step works. No programming knowledge is needed — but familiarity with system settings, file paths, and hardware specifications is assumed.

## Text recognition (OCR) engines

Text recognition — also called OCR (Optical Character Recognition) — is the process of converting an image of a document into machine-readable text. From this point the abbreviation **OCR** is used throughout.

The OCR engine can be switched in **Settings → AI models…** or by changing the `ocr_engine` key in the settings file (TOML format). On the command line use `--ocr-engine`.

Only engines that read pages on a computer you control are available: `ollama` and `ppocr` (the built-in reader). Online vision models (the former `langchain` engine and `--ocr-provider`) were removed because a page cannot be anonymised before it is read. A settings file that still names one falls back to `ollama` with a warning, and Ollama cloud models (tags ending in `cloud`) are refused for OCR.

### 1. Ollama (default)

Ollama runs AI vision models locally on your machine. Teacher's Teammate sends each prepared image to the Ollama service along with a reading prompt, and the model returns the extracted text. This approach delivers the highest accuracy for handwriting and low-quality scans.

**Ollama must be running** before you start a processing run. Download it from [ollama.com/download](https://ollama.com/download) and install it like any other program. By default Teacher's Teammate connects to `http://127.0.0.1:11434` (your own computer). If you run Ollama on a dedicated machine on your local network, change the address in **Settings → This computer…** or set `ollama_url` in the settings file — for example `http://192.168.1.50:11434`.

You do not have to know the address: **Settings → This computer… → Find…** (also offered as **Find server…** when Ollama is not reachable) looks for a server that answers.

- **This computer** — the address in `OLLAMA_HOST`, and `http://127.0.0.1:11434`. When the saved address is unreachable the app also looks here quietly and offers a server it finds with **Use it**; it never switches by itself.
- **The Windows computer, when the app runs in WSL** — in WSL's default network mode `127.0.0.1` is the Linux side, so the dialog also asks the Windows computer (the default gateway and name server of the WSL session). Ollama on Windows must listen on the network for this (`OLLAMA_HOST=0.0.0.0`, then restart it), or WSL must use `networkingMode=mirrored`. For the *privacy* box the Windows computer counts as this computer, because it is.
- **The network** — only after you press **Search the network…** and confirm. The app then asks every address of the private `/24` around each network connection (at most 254 per connection) for `GET /api/version`, nothing else; no document or setting is sent. Some school networks notice such a search, so ask your IT department first. A server found there is another computer: the privacy box shows it as such and the usual question before a run that leaves this computer applies.

Only a reply that looks like Ollama's counts, so a router or web server on the same port is ignored.

**Default models.** Without explicit model settings the app (and the command line) uses `hf.co/prithivMLmods/chandra-ocr-2-GGUF:Q8_0` for reading and `gemma4:e4b` for proofreading and content review. These defaults apply on every computer; the recommendations in the tables below depend on your hardware profile. Download them in **Settings → Add-ons…**, or choose other models with `ocr_model`, `correction_model` and `evaluate_model` in the settings file.

**Reading models and hardware requirements:**

Teacher's Teammate distinguishes three computer types ("hardware profiles"): a basic computer or small graphics card (`cpu`), a graphics card with about 8 GB of memory (`8gb`), and a graphics card with 16 GB or more (`16gb`). The type is detected automatically from the graphics card and its memory (on Apple Silicon, from the shared memory), and shown as the **My computer** setting. The profile decides which models the app calls a best choice and which it marks as too large.

**Reading models** (turn scans into text):

| Model | Memory (running) | Download | cpu | 8 GB | 16 GB | Notes |
|---|---|---|---|---|---|---|
| `hf.co/prithivMLmods/chandra-ocr-2-GGUF:Q8_0` | ~6.5 GB | 5.5 GB | too large | ★ best | ★ best | Default. Full pages with tables and handwriting; keeps the page layout |
| `glm-ocr:q8_0` | ~2.5 GB | 1.6 GB | ★ best | good | good | Small and fast; printed and mixed-language pages |
| `qwen3-vl:8b` | ~7 GB | 6.1 GB | too large | good | good | Best overall for handwriting among the Ollama library models |
| `deepseek-ocr:latest` | ~7 GB | 6.7 GB | too large | good | good | Strong on dense, structured pages |
| `qwen3.5:9b` | ~7 GB | 6.6 GB | too large | good | good | Alternative: a writing model that can also read scanned pages (listed below) |
| `qwen2.5vl:7b` | ~7 GB | 6.0 GB | too large | works | works | Older version of qwen3-vl |

**Writing models** (proofreading and content review):

| Model | Memory (running) | Download | cpu | 8 GB | 16 GB | Notes |
|---|---|---|---|---|---|---|
| `qwen3:4b` | ~4 GB | 2.5 GB | good | works | works | Small model for computers without a graphics card; it thinks first, so a page takes about twice as long as with `gemma4:e4b` |
| `gemma4:e4b` | ~5 GB | 6.6 GB | ★ best | ★ best | works | Default. Fast, fits an 8 GB graphics card entirely, and a page takes about 2 to 3 minutes without a graphics card (a computer with 16 GB of main memory or more) |
| `gemma4:12b` | ~9.5 GB | 8 GB | too large | too large | ★ best | Best for 16 GB graphics cards; on an 8 GB card it runs partly on the processor, so slower |
| `qwen3.5:9b` | ~7.2 GB | 6.6 GB | too large | good | good | Alternative from another model family; can also read scanned pages |
| `mistral:latest` | ~5.5 GB | 4.4 GB | too large | works | works | General-purpose writing model. It does not reliably follow the review format. |
| `mistral-nemo:12b` | ~8.5 GB | 7.1 GB | too large | too large | works | General-purpose writing model. It does not reliably follow the review format. |
| `gpt-oss:20b` | ~14 GB | 13.8 GB | too large | too large | works | Large reasoning model; its answers do not fit the proofreading format, so it gives corrections only |

The ratings in these tables are for a computer whose main memory is not taken into account. With the main memory known (always, for Ollama on your own computer), "too large" becomes "slower" where the rest of the model fits there; see below.

The `hardware_profile` setting in the settings file (TOML format) controls the profile. Allowed values are `auto` (detect — default), `cpu`, `8gb` and `16gb`; an invalid value falls back to `auto` with a warning. With `auto` the app picks the profile from the largest detected graphics card (≥ 15 GB → `16gb`, ≥ 7 GB → `8gb`, otherwise `cpu`; on Apple Silicon it uses two thirds of the shared memory with the same thresholds). When `ollama_url` points at another computer, local detection is skipped and `8gb` is assumed — change the profile yourself if you know what that machine has. An address that names *this* computer (its own host name or network address) counts as this computer. The setting affects the recommendations and the memory warning before a run; it never changes which model is configured. When you pick a profile by hand, the ratings use that card size instead of the detected one — and the memory warning before a run uses it too. The check, its result and any error are written to the log (`INFO` level); when the check itself fails, the **My computer** line says so.

**How a model is rated against your memory.** The app reads your graphics card and your computer's main memory (RAM) and rates every model for **this** computer:

- **Works** (or *good* / *best*): the model fits on the graphics card (about 85 % of the card's memory can be used for models; the rest belongs to the operating system) — or, on a computer without a graphics card, it is small (up to 4 GB) and fits in main memory.
- **Slower** or **very slow**: less than 90 % of the model fits on the card, so part of it runs on the processor in main memory. This works, but a page takes longer — minutes, when less than 70 % fits. It is rated this way only if the part that does not fit is held by your main memory **after** keeping the larger of 8 GB and 20 % of it free for the system.
- **Does not fit** ("too large"): the rest would not fit in main memory. The model could only run by using the disk as memory, which makes the computer unusable; the app never counts that as memory. **Settings → Add-ons…** warns you before you download such a model.

Before a run the app also checks the memory that is free at that moment (keeping 2 GB spare) for the models it will use. If it is not enough it warns you and explains what would happen — the computer would have to use its disk as memory and become extremely slow — and you can still start the run if you know better (other programs may close meanwhile). On the command line the run stops with an error unless you pass `--ignore-memory-check`. This check is skipped when Ollama runs on another computer, whose memory the app cannot see.

**Downloading reading models:**

Open **Settings → Add-ons…** inside Teacher's Teammate, go to the **AI models for this computer** tab, tick the model you want, and click **Install / Download Selected** (the **Reading models** tab is for the built-in reader instead). The download runs in the background and the model is available immediately when finished. You can also pull models directly from the command line:

```bash
ollama pull qwen3-vl:8b
```

Any [vision model available on Ollama](https://ollama.com/library) can be used; the models listed above are the tested and recommended choices. Models that are not in the built-in catalog stay in the **Other models** group. The app asks Ollama what such a model can do: a model that can read images may be used for reading *and* writing, a text-only model for writing only; a model whose abilities cannot be determined is marked "No suggestion available". The size of these models is unknown, so they are never marked as too large. The built-in reading models are the other way round: they are meant for turning scans into text and are not offered for proofreading or content review. Some newer reading models are distributed through Hugging Face rather than the Ollama library — see *Understanding AI models* below for how to install those.

### 2. The built-in reader (PP-OCR)

The built-in reader runs on this computer without a server and without an AI model server. It is made of two small neural networks from the [PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR) project (PP-OCRv6, Apache-2.0): one finds the lines on the page, the other reads each line. OpenCV, which the app already contains, runs them on the processor — no graphics card and no extra Python package are needed. It reads about fifty languages with one model, so the document language does not matter to it.

It is a good choice for printed pages and for computers that cannot run an AI vision model. It reads handwriting less well than the vision models above. Pages are read one column at a time, top to bottom; two columns are read as rows across both.

The model files (about 31 MB) are downloaded **only when you ask for them**:

- in the app: choose *Built-in reader (PP-OCR)* as the recognition method and press **Download built-in reader (31 MB)**, or tick the reader under **Settings → Add-ons… → Reading models**;
- on the command line: `teachers-teammate --download-reader`, once, then `--ocr-engine ppocr`.

They are kept in the app's data folder (shown under *Reading models*) and checked against a fixed checksum, so a damaged download is refused. The only host contacted is `huggingface.co`; if a school network blocks it, ask whoever looks after the network to allow it.

Settings files that still name `tesseract` or `paddleocr` (removed engines) now use the built-in reader; the app says so when it loads such a file. The Paddle add-on's old packages are removed from the add-on folder automatically.

---

## Running from the command line

### Exit codes

The command line exits with `0` when the run finished without a failed file, and with `1` when a file failed or the run could not start (for example a refused output folder or an unreachable server). A file left out on purpose (a scan while text recognition is off) is neither: it does not change the exit code.

### Keeping the computer awake

By default the computer is kept from going to sleep while a run is going: Windows uses a power request (visible in `powercfg /requests`), macOS runs `caffeinate -i`, and Linux takes a `systemd-inhibit` lock. They end with the program. Under WSL nothing is held; the Windows side decides. The screen may still turn off.

```bash
teachers-teammate -i ./scans --allow-sleep    # let the computer sleep during the run
teachers-teammate -i ./scans --keep-awake     # the default; useful to override keep_awake = false in ocr.toml
```

The same setting in `ocr.toml` is `keep_awake = true | false`.

## Image preparation

Before a page is read, the app prepares its image. There are two choices, set in **Settings → Scan quality…** (the *Image preparation* dropdown) or on the command line with `--preprocess-method`:

| Choice | Setting | What it does |
|--------|---------|--------------|
| **Automatic (recommended)** | `auto` (default) | Looks at each page and does only what it needs: cuts a photographed page out, turns a sideways page upright, flattens a curved page, scales a huge page down, evens out shadows and corrects a tilt. A clean scan passes unchanged. |
| **Original page** | `none` | Sends each page as scanned or photographed, for checking what the preparation changes. A phone photo's rotation tag (EXIF orientation) is still applied, so it does not reach the reader sideways. |

The older methods (`grayscale`, `clahe`, `adaptive_threshold`) and the switches for dewarp, deskew, border crop, denoise and brighten are gone. A settings file that still names one of those methods reads as *Automatic*; the old switch keys are ignored. Because of that, every document is read again the next time it is run after the update.

### What *Automatic* does

The steps run in this order; each acts only where the page needs it:

| Step | Acts when | What it does |
|------|-----------|--------------|
| `page` | a page outline with four straight edges is found, shaped like a sheet (about A4) and brighter than its surroundings | cuts the page out with a perspective correction. A framed table on a clean scan is not taken for a page |
| `flatten` | the image is a photo and `page` did not act, or the page's edges are curved | flattens the page with the UVDoc model (*page models*) |
| `turn` | the orientation model is at least 90 % sure the page is turned by 90°, 180° or 270° | turns it upright (*page models*) |
| `size` | the long side is more than 3508 pixels (A4 at 300 DPI) | scales the page down to that |
| `light` | the paper is unevenly lit (shadows) | divides each colour channel by the brightness of the paper under the writing; pencil stays darker than the paper |
| `deskew` | the writing is tilted by 0.3° to 10° | straightens it; a page tilted more than 10° is left alone |
| `grayscale` | the engine is Ollama | Ollama gets a gray page; the built-in reader gets colour |

There is no denoising and no black-and-white conversion. A photo is recognised by camera tags in the file, a page-like region with the wrong shape, or uneven light. Enable `--debug` (or the *Keep processed images for inspection* toggle) to save a snapshot after every step that acted.

In the preview and under each result, the app says in words what was done: "Prepared: page turned, shadows evened out, tilt corrected", or "Prepared: nothing needed".

### Page models

*Turn* and *flatten* need two small models from PaddlePaddle (Apache-2.0; 39 MB together, from huggingface.co). They are downloaded only when you ask for them:

- in the app: under *Automatic* on **Settings → Scan quality…** press **Download…**, or tick **Page models** under **Settings → Add-ons… → Reading models**;
- on the command line: `teachers-teammate --download-page-models`, once.

Without them *Automatic* still runs and skips those two steps; a run on the command line prints one line saying so. Downloading them changes what the preparation does, so pages are read again once.

### PDF render resolution

PDF files must be converted to images before OCR. The resolution of this conversion is controlled by **PDF DPI** (default **300 DPI**, under **Expert settings** on the Scan quality page).

| DPI | Scale | Use case |
|-----|-------|----------|
| 72 | 1× | Screen preview only — too low for OCR |
| 150 | 2× | Draft / fast pass |
| 300 | 4.2× | Recommended for OCR (default) |
| 600 | 8.3× | High quality; little benefit above 300 for most models |

This setting has no effect on image inputs (JPG, PNG) — those are used at their native resolution.

---

## Understanding AI models

Both the reading (OCR) models and the proofreading and content-review models are AI
language models. Two things change how they behave and how much hardware they need:
their **quantisation** (which affects size and memory) and their **temperature**
(which affects how predictable their output is). This section explains both in plain
terms, plus where the models come from.

### Model size and quantisation

A model's capability roughly tracks its **number of parameters**, measured in billions
(b) — a `12b` model is larger and generally more capable than an `8b` one, but it also
needs more memory and runs more slowly.

The full, uncompressed version of a model stores each parameter at high precision,
which is memory-hungry. **Quantisation** shrinks the model by storing each parameter at
lower precision. This is described by a *quant level* in the model's tag, such as
`Q4_K_M` (4-bit) or `Q8_0` (8-bit):

- **Lower numbers (Q4)** use the least memory and run fastest, with a small drop in
  quality. This is the best default for most computers.
- **Higher numbers (Q8)** stay closer to the original quality but need roughly twice
  the memory.

Ollama loads models at 4-bit quantisation by default, which is why the memory figures
in the table above are far smaller than the full model weights. If a model runs out of
memory, choose a smaller model or a lower quant level; if you have plenty of memory and
want the best quality, choose a higher quant level.

### Temperature

**Temperature** controls how predictable a model's output is: low values make it
consistent and literal, high values allow more variation. There is nothing to set. The
app sends each model the temperature its maker recommends on the model's card (for
example `1.0` for the Gemma models and `0.7` for Qwen), because a model works best the
way it was tuned. A model whose maker names no value, and every online service, gets
`0.2` for proofreading and `0.7` for the content review. Reading handwriting always uses
`0.0`: a page has one right transcription, so the model should not invent wording.

### Models from Hugging Face

Most models are available directly from the [Ollama library](https://ollama.com/library)
and can be downloaded from **Settings → Add-ons…** inside the app. Some newer
or specialised models are published on [Hugging Face](https://huggingface.co/) instead.
Ollama can pull these directly using an `hf.co/` prefix — for example:

```bash
ollama pull hf.co/prithivMLmods/chandra-ocr-2-GGUF:Q8_0
```

The part after the colon (`Q8_0` here) is the quant level described above — `Q8_0` is a near-lossless 8-bit quantisation, larger and a little more accurate than the 4-bit `Q4` tags. When a
Hugging Face model offers several quant tags, pick a 4-bit (`Q4`) tag for the best
balance of quality and memory use. Once pulled, the model appears in the app's model
lists just like any Ollama-library model — no further setup is required.

---

## Image preparation (preprocessing)

Image preparation — referred to as *preprocessing* in the settings file (`preprocess_method = "auto"` or `"none"`) — is applied to each image before it is passed to the OCR engine. *Automatic* is right for nearly every document and suits both engines; use *Original page* only to check what the preparation changes. The full description is under [Image preparation](#image-preparation) above.

Use the **Preview…** button in **Settings → Scan quality…** to compare *Automatic* with *Original page* on one of your own documents before committing to a large batch. Ctrl + mouse wheel zooms the preview image.

---

## Anonymization before proofreading

When proofreading is enabled, the raw OCR text is sent to an AI model. If documents contain personal information (PII) — names, email addresses, phone numbers, IBANs, street addresses, or dates — you can enable anonymization to replace that information with stand-ins before it leaves your machine.

:::{warning} Anonymization is not a guarantee
The anonymizer is a detection aid, not a certified data-protection tool. AI name-detection models miss names they were not trained on, pattern rules do not cover every possible format, and OCR errors can cause personal information to go undetected. Always review the anonymized output before sending sensitive documents to a cloud provider. You remain responsible for compliance with applicable data-protection regulations (such as GDPR).
:::

After OCR, the anonymizer scans the text for PII and replaces each detected item with a numbered placeholder such as `[PERSON_1]`, `[EMAIL_1]`, or `[IBAN_1]`. The same value always maps to the same placeholder throughout the document. Once the proofread text comes back, all placeholders are substituted with the original values before the result is stored.

| PII type | Detection method | Examples |
|---|---|---|
| Person names | AI name detection (spaCy NER) | "Alice Müller", "Dr. Smith" |
| Email addresses | Pattern matching (regex) | `alice@example.com` |
| Phone numbers | Pattern matching (regex) | `+49 30 12345`, `(030) 9876 543` |
| IBANs | Pattern matching (regex) | `DE89 3704 0044 0532 0130 00` |
| Street addresses (German formats) | Pattern matching (regex) | `Hauptstraße 12`, `Konrad-Adenauer-Straße 5`, `10115 Berlin` |
| Dates | Pattern matching (regex) | `01.02.2010`, `2010-02-01`, `5. März 2024`, `March 5, 2024` |

### Pattern rules and their limits

The street and postcode patterns only know German formats. A street needs a house number and a street word (`…straße`, `…weg`, `…platz`, `Straße`, `Weg`, …, optionally after `St.` or `Sankt`); a postcode is only recognised at the start of a line or after a comma, `in` or `D-`. Dates are recognised as numbers (`01.02.2010`, `1/2/10`, `2010-02-01`) and with a German or English month name (`5. März 2024`, `March 5, 2024`, `5th March 2024`); a month and year without a day (`März 2024`) is left alone.

Because the proofreading and the content review both receive the anonymized text, every replaced date is hidden from them. In a history essay a year such as `9.11.1989` is replaced too. A section number such as `Aufgabe 1.2.10` can also be read as a date. If that gets in the way, remove the `DATE` patterns in **Settings → Privacy… → Personal info: patterns**.

Your pattern list is only stored once you change it. Settings saved while the built-in list was still IBAN, email and phone switch to the current built-in list automatically. Proofread text that already exists is kept; the *anonymized text* files and exports always use the patterns that are current when you create them.

### spaCy and language models

Person-name detection is powered by [spaCy](https://spacy.io/), an open-source natural language processing library. spaCy requires a **language model** to work — the model determines which language's names and naming patterns it recognises. Without a model that matches your documents' language, many names will be missed.

The Privacy add-on (which includes spaCy) and the language models are both installed from inside the app:

- **Privacy add-on**: click the *Install Privacy Add-on* button (shown automatically when the feature is missing).
- **Language models**: open **Settings → Add-ons…** → **Name detection**, tick a model and install it.

**Available language models:**

| Model | Languages | Download size | When to use |
|---|---|---|---|
| `xx_ent_wiki_sm` | Multilingual (~70 languages) | ~15 MB | Best default; handles mixed-language documents |
| `de_core_news_sm` | German | ~15 MB | Higher accuracy for German-only documents |
| `en_core_web_sm` | English | ~15 MB | Higher accuracy for English-only documents |

Teacher's Teammate supports a **primary** and an optional **secondary** language model simultaneously. Using two models — for example, multilingual + German — increases recall for names that the first model misses. Configure this in **Settings → Privacy… → Personal info: detection**.

Enable anonymization via the **Remove personal information before proofreading** checkbox, or set `anonymization_enabled = true` in the settings file. Anonymization only runs when proofreading is also enabled.

---

## Proofreading and content review

### Proofreading

When proofreading is enabled (`correction_enabled = true`), each file's OCR text is sent to the configured AI model for correction. The corrected text is stored in a per-file result cache so it is not recomputed on subsequent runs unless the source file or relevant settings change.

The answer is a *review*, not just a text: the corrected text plus a short explanation per change and a list of unclear or ambiguous expressions (see {doc}`using_the_app`, *Reading the proofreading notes*). Relevant settings:

| Setting | Default | Meaning |
|---|---|---|
| `correction_level` | `B2` | Target level (CEFR `A1`–`C2`) for every language. The prompt's `{level}` placeholder is filled with it; a custom prompt without `{level}` ignores the setting |
| `explanation_language` | `""` | Language of the explanations and note tables; empty means the language of the document |
| `correction_output` | `review` | `review` explains the changes; `text` is *Corrections only (faster)* and is also the automatic fallback when a model cannot answer in the explanation format |
| `answer_format_review`, `answer_format_text` | `""` | Your own answer format for the two modes (empty = built-in). It tells the model how to fill in its reply; the app reads the reply in this format, so change it only if you know what you are doing. A custom format is tied to `answer_format_version` (written for you by the GUI; without it the current version is assumed). When an update changes the built-in format, an edit written for the older one is dropped with a warning. The **Options…** dialog of **Proofread** edits them under *Answer format (advanced)* |
| `proofreading_context` | `auto` | (Shown on **Settings → AI models…**, and only while it changes something.) The context window (in tokens) the model gets. Proofreading itself stays at 8192: a larger window doubles the length of the answer, and no check measures whether the longer answers are still as good. The setting therefore only sizes the **content review**, and only when that runs on its own Ollama model — with the same model for both stages, or with an online service, it changes nothing. `auto` grows the window with the size of the graphics card — 8192, 16384 or 32768, whichever still fits the card entirely — and stays at 8192 when there is no card, the card size is unknown or Ollama runs on another computer. It uses the card's total size, not the memory free at the moment: free memory changes between runs, and a window that changed with it would make Ollama reload the model and would invalidate the result cache. A fixed value from `8192`, `16384`, `32768` overrides `auto`; useful for a remote server whose card the app cannot see. A larger window lets longer texts through, but it also needs more memory |

**Upgrading from an earlier version.** The default proofreading model is now `gemma4:e4b`
(it was `qwen3.5:9b`). Saved settings are not changed, so an installation that never touched the
model keeps what the file says; an installation that used the defaults gets the new one. The
temperature keys of an older `ocr.toml` (`ocr_temperature`, `correction_temperature`,
`evaluate_temperature`) are ignored: the app now sends the temperature each model's maker
recommends. Files that were proofread with the default settings are proofread again once, because
the model and the temperature are part of what the result cache compares.

Long texts are proofread in pieces: the text is split at paragraph, then sentence boundaries, each piece is proofread on its own and the answers are joined back into one review. A correction that would need to see two pieces at once — a sentence split across the boundary, or a change that only makes sense together with an earlier one — cannot be made. If one piece falls back to corrections only, the file keeps the explanations it has and a warning says that explanations are missing for part of the text. If a piece fails outright the whole file counts as failed and is retried on the next run.

Review answers are bound to a JSON schema. When a model fails to answer the schema (models without the `explanations ✓` badge are more likely to), proofreading falls back to corrections only for that file and says so. A file whose request failed (no connection, timeout) is not stored as corrected, so the next run tries it again. An unknown `correction_level` or `correction_output` in the config file counts as the default (`B2`, `review`); on the command line `--explanations` turns explanations back on when the config file turned them off. The same cache is reused as long as the level, the explanation language, the output mode, the instructions, the answer format and the answer-format version stay unchanged (the built-in texts count as they are, so a changed built-in prompt also re-runs proofreading) — changing any of them re-runs proofreading.

The AI service and model are set in **Settings → AI models…**. Local Ollama models require no internet access or account. Online services (OpenAI, Anthropic, Google) require an API access key, entered right below the service on **Settings → AI models…** or in the key list on **Settings → Online services…** (both show the same keys; a key is used at once and stored when you press OK). The key is stored in the system keychain (macOS Keychain, Windows Credential Manager, Linux Secret Service such as GNOME Keyring or KWallet). Without a keychain (a minimal Linux install, WSL) it is stored unencrypted in the app's settings file (on Linux `~/.config/TeachersTeammate/TeachersTeammate.conf`), and the page says so. Keys from an older version are moved into the keychain at the next start. A key set in the environment (e.g. `OPENAI_API_KEY`) before starting the app takes priority and is never stored.

### Content review

Content review uses an AI model to read the proofread text and produce a short written report — assessing whether the content is complete and consistent, noting gaps or unclear passages, and flagging anything that may need closer attention. The review focuses on the content itself, not on spelling or the quality of the proofreading step. The result is stored alongside the corrected text.

In automatic mode the review runs immediately after proofreading; in manual mode it is triggered by selecting rows in the results list and choosing **Run Content Review** from the right-click menu.

Lighter models are sufficient for content review because the task is analytical rather than generative:

| Provider | Example model |
|---|---|
| Ollama | `gemma4:e4b`, `qwen3.5:9b` |
| OpenAI | `gpt-4o-mini` |
| Anthropic | `claude-haiku-4-5` |
| Google | `gemini-2.0-flash` |

---

## Example configurations

### High-quality local setup (Ollama-centric)

Runs all stages locally with the best available models. Requires a GPU with at least 8 GB VRAM for acceptable OCR throughput.

- OCR: `ocr_engine=ollama`, `ocr_model=qwen3-vl:8b`
- Image preparation: `preprocess_method=auto`
- Proofreading: `correction_provider=ollama`, `correction_model=gemma4:e4b`, `correction_level=B2`
- Content review: `evaluate_provider=ollama`, lightweight local model (e.g. `qwen3:4b`)

### Fast offline setup

Prioritises speed and works without internet access. Best suited for cleanly printed text.

- OCR: `ocr_engine=ppocr` (download it once with `--download-reader`)
- Image preparation: `preprocess_method=auto`
- Proofreading: disabled
- Content review: disabled

### Hybrid cloud setup

Keeps documents on the local machine for text recognition while using cloud AI to maximise proofreading and content review quality. Turn on `anonymization_enabled` so names are hidden from both online steps.

- OCR: `ocr_engine=ollama` (local vision model)
- Proofreading: `correction_provider=openai`
- Content review: `evaluate_provider=anthropic`
- Privacy: `anonymization_enabled=true`

---

## Pipeline overview

The diagram below shows all processing stages and the available options at each stage.

```{uml} assets/pipeline_stages.puml
:align: center
:alt: Teacher's Teammate processing pipeline stages and options
:width: 100%
```

---

## Comparing OCR settings (Benchmark app)

Choosing between engines, models, and image-preparation methods is easiest when you can see the results side by side on one of your own documents. Teacher's Teammate ships a **Benchmark** app for exactly this: it runs a single document through one OCR configuration, keeps every result in a timestamped history, and lets you compare any two of them.

The Benchmark app is **text-recognition only** — it does not anonymize, proofread, review content, or write Word documents. It is a tool for tuning the OCR stage in isolation, not for producing finished output.

### Launching the Benchmark app

Open it from **Tools → Benchmark…** in the main window; the standalone installers include it. It is its own window with its own taskbar entry, so you can place it next to the main window, and a second click on the menu entry brings the open one to the front. It reads the AI server and the computer profile from your saved settings when it opens and keeps them while it is open; a change you make in Settings afterwards reaches it the next time you open it. A benchmark and a batch can run at the same time, but they then share the graphics card, so both are slower. Closing the main window also closes the Benchmark window.

When Teacher's Teammate is installed as a Python package, it can also be started without the main window:

```bash
# From a package (pip) install — a console command is registered:
teachers-teammate-benchmark

# From a source checkout:
python -m teachers_teammate.gui.benchmark
```

### Running and storing a configuration

1. **Choose a document** — click *Choose document…* and pick a single PDF, image, or text file. Any input type the main app supports works here too.
2. **Pick an OCR configuration** — the same engine / model / image-preparation selector as the main app. Set it to the combination you want to try.
3. **Run & store** — runs OCR and appends the result to the history for this document. *Stop* cancels a run in progress.
4. Repeat with a different engine, model, or image-preparation method. Each run is added to the **Stored runs** list, labelled with its timestamp, a short configuration summary, and the word count.

Runs are saved against the document's **content** (not its file name or location), so renaming or moving the file keeps its history intact.

### Comparing two runs

Select a run in the list and click **Set as A**, then select another and click **Set as B**. The **Compare** tab then shows, top to bottom:

- the document image,
- the two recognised texts side by side (**Run A** and **Run B**),
- a colour-coded **diff (A → B)** highlighting exactly where they differ, and
- a **similarity** score with per-run word and character counts.

The similarity score is a percentage from `0%` to `100%`, measuring how much the two texts agree after ignoring case and whitespace differences. There is **no ground-truth reference** — the benchmark answers "how much do these two runs agree?", not "which one is correct". Use it as a guide:

- **High similarity** between two different engines or models suggests both are reading the document consistently — a sign the text is being recognised reliably.
- **Low similarity** flags the passages where they disagree; open the diff to see which engine handled difficult handwriting or layout better, and judge against the document image.

### How history is kept

The run history is **append-only** and bounded two ways so it cannot grow without limit:

- **Automatic cap** — only the most recent runs per document are kept (the newest 20 by default); older ones are evicted automatically.
- **Manual deletion** — **Delete** removes the selected run; **Clear all runs for this document** empties the history for the current document.
