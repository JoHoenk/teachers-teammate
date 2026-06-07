# Advanced User Guide

## Who this guide is for

This guide is for technically-minded users who want to configure the processing pipeline precisely, choose between different text recognition engines and AI model combinations deliberately, and understand how each step works. No programming knowledge is needed — but familiarity with system settings, file paths, and hardware specifications is assumed.

## Text recognition (OCR) engines

Text recognition — also called OCR (Optical Character Recognition) — is the process of converting an image of a document into machine-readable text. From this point the abbreviation **OCR** is used throughout.

The OCR engine can be switched in **Settings → Text Recognition…** or by changing the `ocr_engine` key in the settings file (TOML format).

### 1. Ollama (default)

Ollama runs AI vision models locally on your machine. Teacher's Teammate sends each prepared image to the Ollama service along with a reading prompt, and the model returns the extracted text. This approach delivers the highest accuracy for handwriting and low-quality scans.

**Ollama must be running** before you start a processing run. Download it from [ollama.com/download](https://ollama.com/download) and install it like any other program. By default Teacher's Teammate connects to `http://127.0.0.1:11434` (your own computer). If you run Ollama on a dedicated machine on your local network, change the address in **Advanced Settings → Connections & API Keys…** or set `ollama_url` in the settings file — for example `http://192.168.1.50:11434`.

**Reading models and hardware requirements:**

The memory a model needs depends on its size, measured in billions of parameters (b). Ollama loads models using 4-bit quantisation by default, which significantly reduces memory usage compared to the full model weight.

| Model | Parameters | Min. VRAM (GPU) | Min. RAM (CPU-only) | Notes |
|---|---|---|---|---|
| `deepseek-ocr:latest` | ~3 b | 4 GB | 8 GB | Strong on dense and structured documents |
| `qwen3-vl:8b` | ~8 b | 6 GB | 12 GB | Best overall for handwriting — recommended |
| `glm-ocr:latest` | ~9 b | 8 GB | 16 GB | Good for printed and mixed-language scans |

A dedicated graphics card (GPU) with enough video memory (VRAM) gives the best throughput. CPU-only inference works but is considerably slower — expect several minutes per page for larger models.

**Downloading reading models:**

Open **Advanced Settings → Downloads…** inside Teacher's Teammate, go to the **Ollama Models** tab, tick the model you want, and click **Install / Download Selected** (the **OCR Engines** tab is for the Tesseract and PaddleOCR engines instead). The download runs in the background and the model is available immediately when finished. You can also pull models directly from the command line:

```bash
ollama pull qwen3-vl:8b
```

Any [vision model available on Ollama](https://ollama.com/library) can be used; the models listed above are the tested and recommended choices. Some newer reading models are distributed through Hugging Face rather than the Ollama library — see *Understanding AI models* below for how to install those.

### 2. [Tesseract](https://tesseract-ocr.github.io/) OCR

Tesseract is a classic, rule-based OCR engine. It works without any internet connection, requires no AI model, and runs entirely on the CPU. It is a solid choice for clearly printed text in offline environments or on hardware without a GPU. Accuracy on handwriting is limited.

Tesseract must be installed at the operating-system level separately from Teacher's Teammate:

```bash
# Ubuntu / Debian
sudo apt install tesseract-ocr tesseract-ocr-eng tesseract-ocr-deu

# macOS (requires Homebrew — https://brew.sh)
brew install tesseract
```

**Windows:** Download and run the installer from the [UB Mannheim Tesseract releases page](https://github.com/UB-Mannheim/tesseract/wiki). Alternatively, if you have the Windows Package Manager installed, run:

```powershell
winget install UB-Mannheim.TesseractOCR
```

After installation, Tesseract's status indicator in Teacher's Teammate will turn green automatically.

Language packs determine which languages Tesseract can read. Install additional packs via your package manager (Linux/macOS) or by selecting them during the Windows installer setup.

### 3. [PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR)

PaddleOCR is a deep-learning OCR engine developed by Baidu. It often outperforms Tesseract on degraded or mixed-language scans and handles a wider range of scripts. It works best with non-binarised input — use the **None** or **Grayscale** image preparation setting.

PaddleOCR is an optional add-on that can be installed from within Teacher's Teammate when the PaddleOCR engine is selected — see {doc}`Using the App <using_the_app>`, *Installing extra features*. The download is approximately 400 MB.

---

## Understanding AI models

Both the reading (OCR) models and the proofreading and content-review models are AI
language models. Two settings change how they behave and how much hardware they need:
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

**Temperature** controls how predictable a model's output is. It ranges from `0.0` to
`2.0` and can be set separately for reading, proofreading, and content review in
**Settings → Text Recognition… / Proofreading Settings… / Content Review Settings…**.

- **`0.0` (recommended for reading and proofreading)** makes the model as consistent
  and literal as possible. The same input gives the same output, which is what you want
  when transcribing or correcting text — you do not want the model inventing wording.
- **Higher values (0.5–1.0)** allow more variation. This is occasionally useful for
  content review, where a little more freedom can produce a more natural-sounding
  report, but it also raises the risk of the model straying from the source.

When in doubt, leave temperature at `0.0`.

### Models from Hugging Face

Most models are available directly from the [Ollama library](https://ollama.com/library)
and can be downloaded from **Advanced Settings → Downloads…** inside the app. Some newer
or specialised models are published on [Hugging Face](https://huggingface.co/) instead.
Ollama can pull these directly using an `hf.co/` prefix — for example:

```bash
ollama pull hf.co/unsloth/gpt-oss-20b-GGUF:UD-Q4_K_XL
```

The part after the colon (`UD-Q4_K_XL`) is the quant level described above. When a
Hugging Face model offers several quant tags, pick a 4-bit (`Q4`) tag for the best
balance of quality and memory use. Once pulled, the model appears in the app's model
lists just like any Ollama-library model — no further setup is required.

---

## Image preparation (preprocessing)

Image preparation — referred to as *preprocessing* in the settings file — is applied to each image before it is passed to the OCR engine. The chosen method has a significant impact on recognition accuracy. Set it in **Settings → Text Recognition…**.

**`grayscale`** converts the image to greyscale without any further adjustments. It preserves tonal detail and is the recommended starting point for Ollama vision models, which handle contrast internally.

**`adaptive_threshold`** applies locally adaptive thresholding to produce a high-contrast black-and-white image. It compensates well for uneven lighting across the page and is the most effective option for handwritten documents.

**`clahe`** applies contrast-limited adaptive histogram equalisation (CLAHE), boosting local contrast without overexposing bright regions. This is often the best choice for Tesseract on low-contrast or faded scans.

**`none`** passes the image through without modification. Use this when the input is already clean, or when colour information is important — for example, PaddleOCR sometimes performs better on colour input.

Use the preview tool in **Settings → Text Recognition…** to compare methods side by side before committing to a large batch. Ctrl + mouse wheel zooms the preview image.

---

## Anonymization before proofreading

When proofreading is enabled, the raw OCR text is sent to an AI model. If documents contain personal information (PII) — names, email addresses, phone numbers, or IBANs — you can enable anonymization to replace that information with stand-ins before it leaves your machine.

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

### spaCy and language models

Person-name detection is powered by [spaCy](https://spacy.io/), an open-source natural language processing library. spaCy requires a **language model** to work — the model determines which language's names and naming patterns it recognises. Without a model that matches your documents' language, many names will be missed.

The Privacy add-on (which includes spaCy) and the language models are both installed from inside the app:

- **Privacy add-on**: click the *Install Privacy Add-on* button (shown automatically when the feature is missing).
- **Language models**: open **Advanced Settings → Anonymizer Settings…** → **Download Model** and choose a model from the list.

**Available language models:**

| Model | Languages | Download size | When to use |
|---|---|---|---|
| `xx_ent_wiki_sm` | Multilingual (~70 languages) | ~15 MB | Best default; handles mixed-language documents |
| `de_core_news_sm` | German | ~15 MB | Higher accuracy for German-only documents |
| `en_core_web_sm` | English | ~15 MB | Higher accuracy for English-only documents |

Teacher's Teammate supports a **primary** and an optional **secondary** language model simultaneously. Using two models — for example, multilingual + German — increases recall for names that the first model misses. Configure this in **Advanced Settings → Anonymizer Settings…**.

Enable anonymization via the **Remove personal information before proofreading** checkbox, or set `anonymization_enabled = true` in the settings file. Anonymization only runs when proofreading is also enabled.

---

## Proofreading and content review

### Proofreading

When proofreading is enabled (`correction_enabled = true`), each file's OCR text is sent to the configured AI model for correction. The corrected text is stored in a per-file result cache so it is not recomputed on subsequent runs unless the source file or relevant settings change.

The AI service and model are set in **Settings → Proofreading Settings…**. Local Ollama models require no internet access or account. Online services (OpenAI, Anthropic, Google) require an API access key entered in **Advanced Settings → Connections & API Keys…**.

### Content review

Content review uses an AI model to read the proofread text and produce a short written report — assessing whether the content is complete and consistent, noting gaps or unclear passages, and flagging anything that may need closer attention. The review focuses on the content itself, not on spelling or the quality of the proofreading step. The result is stored alongside the corrected text.

In automatic mode the review runs immediately after proofreading; in manual mode it is triggered by selecting rows in the results list and choosing **Run Content Review** from the right-click menu.

Lighter models are sufficient for content review because the task is analytical rather than generative:

| Provider | Example model |
|---|---|
| Ollama | `gemma3:12b`, `llama3.1:8b` |
| OpenAI | `gpt-4o-mini` |
| Anthropic | `claude-3-haiku-20240307` |
| Google | `gemini-2.0-flash` |

---

## Example configurations

### High-quality local setup (Ollama-centric)

Runs all stages locally with the best available models. Requires a GPU with at least 8 GB VRAM for acceptable OCR throughput.

- OCR: `ocr_engine=ollama`, `ocr_model=qwen3-vl:8b`
- Image preparation: `preprocess_method=grayscale`
- Proofreading: `correction_provider=ollama`, `correction_model=gpt-oss:20b`
- Content review: `evaluate_provider=ollama`, lightweight local model (e.g. `llama3.1:8b`)

### Fast offline setup

Prioritises speed and works without internet access. Best suited for cleanly printed text.

- OCR: `ocr_engine=tesseract`
- Image preparation: `preprocess_method=clahe`
- Proofreading: disabled
- Content review: disabled

### Hybrid cloud setup

Keeps documents on the local machine for text recognition while using cloud AI to maximise proofreading and content review quality.

- OCR: `ocr_engine=ollama` (local vision model)
- Proofreading: `correction_provider=openai`
- Content review: `evaluate_provider=anthropic`

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

Choosing between engines, models, and image-preparation methods is easiest when you can see the results side by side on one of your own documents. Teacher's Teammate ships a separate **Benchmark** app for exactly this: it runs a single document through one OCR configuration, keeps every result in a timestamped history, and lets you compare any two of them.

The Benchmark app is **text-recognition only** — it does not anonymize, proofread, review content, or write Word documents. It is a tool for tuning the OCR stage in isolation, not for producing finished output.

### Launching the Benchmark app

The Benchmark app is a separate command, not a menu item inside the main window. It is available when Teacher's Teammate is installed as a Python package (it is not bundled into the standalone installers):

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
