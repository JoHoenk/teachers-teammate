# Using Teacher's Teammate

## The app window

The app is divided into two main areas:

- **Settings panel (left side)** — everything you need to set up a run: which folders to use, which reading method to apply, whether to use AI proofreading, and so on. At the top, a box called **Where your documents are processed** shows, for each step, whether it runs on this computer (green dot), on another computer (orange) or on an online service (red) — see *Where your documents are processed* below. Below the settings you will find coloured status indicators (one per feature the current setup uses) and the action buttons — **Run Selected**, **Stop**, **Export Word…**, **Restore Defaults** — with a progress bar.

- **Work area (right side)** — three tabs:
  - **Processing** — a list of your files (one row per document) with status indicators and a live activity log below it.
  - **Preview** — the original document image next to the extracted and corrected text for the selected file.
  - **Stats** — a chart showing how long each file took to process, plus live system resource usage (CPU, memory, and GPU if available).

- **Menu bar** at the top — two settings menus plus Help:
  - **Settings** — Text Recognition…, Proofreading Settings…, Content Review Settings…, Output Settings…
  - **Advanced Settings** — Connections & API Keys…, Downloads…, Anonymizer Settings…
  - **Help** — User Guide…, Third-Party Licenses…, About…

You can also **drag a folder directly onto the window** to set it as the input folder.

Status indicators turn green once each required service is up and running.

---

## How processing works

### Automatic processing

When you click **Run Selected**, the app works through your files one by one. You can watch the progress in the **Processing** tab — each file shows whether it is waiting, being read, being proofread, or finished. The app handles everything by itself. You do not need to stay on the screen. You can switch to another program and come back when it is done.

Each file goes through all the steps you have switched on — text recognition, removing personal information, and proofreading — in that order. Results appear in the list as soon as each file is finished.

### Stopping and continuing

You can click **Stop** at any time. Any files that finished before you stopped are already saved. When you click **Run Selected** again, the app picks up where it left off — already-finished files are skipped automatically.

### Editing the result by hand

After processing, click any row in the results list to open the **Preview** tab. The text shown on the right side is fully editable — click into it and type to correct anything. Your changes are saved automatically. You do not need to re-run the file.

### Re-running a specific step

If you want to redo just one part — for example, run proofreading again with a different setting — right-click a row in the results list and choose the step you want to repeat: **Re-run Text Recognition**, **Re-run Proofreading**, or **Run Content Review**. Only the chosen step is repeated (and any steps that depend on it).

### How the app remembers your results

The app stores the result for every file it has processed. If you change only the proofreading settings and click **Run Selected** again, the app reuses the text it already read — it does not read your documents a second time. If you change the reading method or the image preparation setting, any affected files are re-read from scratch automatically.

---

## Text recognition methods

The app recognises the text in your documents using the method you choose in **Settings → Text Recognition…**.

### Ollama (recommended)

Ollama is a free program that runs AI reading models on your own computer. Teacher's Teammate sends each prepared image to Ollama, which reads the text and returns it. This gives the best results for handwriting and difficult scans.

**Ollama must be running** before you click Run. Download it from [ollama.com/download](https://ollama.com/download) and install it like any other program. The default connection is set to your own computer. If you have Ollama running on a different computer on your home network, enter its address in **Advanced Settings → Connections & API Keys…**.

### Choosing AI models

Teacher's Teammate needs two AI models, and you do not need to know anything about "VRAM" or "model sizes" to pick good ones:

- a **reading model** turns your scans into text,
- a **writing model** proofreads and reviews the text.

The app looks at your computer — the **My computer** setting shows what it found — and suggests models that work well on it. It is usually right; you can change the choice yourself under **Advanced Settings → Connections & API Keys…** or in **Downloads**, and every window then uses your choice. If Ollama runs on a different computer, pick that computer's type in the list.

The best models per computer type:

| Your computer | Best reading model | Best writing model |
|---|---|---|
| Basic computer or small graphics card (under 8 GB) | `glm-ocr:q8_0` | `qwen3:4b` |
| Graphics card with about 8 GB memory | `hf.co/prithivMLmods/chandra-ocr-2-GGUF:Q8_0` | `qwen3.5:9b` |
| Graphics card with 16 GB memory or more | `hf.co/prithivMLmods/chandra-ocr-2-GGUF:Q8_0` | `gpt-oss:20b` |

To get a working setup in one step, open **Advanced Settings → Downloads…**, stay on the **Ollama Models** tab (it opens first), and click **Select the best models for my computer** — the app ticks the two models it recommends for your computer. Then click **Install / Download Selected**. On the very first start the app also pre-selects the best models for your computer, and the "model missing" message before a run takes you straight to the Downloads dialog with the missing models already ticked.

Greyed-out models are too large for your computer. They still work — but reading or proofreading can become very slow, so the app asks before downloading one. Models you add yourself stay in the "Other models" group, where each one shows whether it reads scans and writes or only writes. Reading models are meant for turning scans into text; the app warns you when you pick one for proofreading or content review.

### Other recognition methods

Additional recognition methods are available for specific situations — for example, working without internet access or on computers with limited resources. For details, see the {doc}`Advanced User Guide <advanced_user_guide>`.

### Recognition always stays on computers you control

Reading a page needs the whole page — the handwriting, the student's name at the top, the grade in the margin. None of that can be hidden beforehand, because names can only be found once the text has been read. For this reason Teacher's Teammate does **not** offer online services (such as ChatGPT, Claude or Gemini) for text recognition, and it also refuses Ollama *cloud* models (names ending in `-cloud` or `:cloud`), which run on Ollama's servers rather than on your computer. If you try to start a run with one of these, the app explains why and does not start.

If your saved settings from an older version used an online service for text recognition, the app tells you at start-up that it now uses Ollama on your computer instead.

Ollama on another computer that you or your school run (see *Connections & API Keys*) is allowed; the box described below then shows it as "On another computer" with its name.

---

## Image preparation

Before reading the text, the app can adjust each image to improve accuracy. **Grayscale** is the recommended starting point when using Ollama and works well in most situations.

To compare settings before processing a large folder, open **Settings → Text Recognition…** and use the **Preview** — it shows the original and prepared image side by side. Use Ctrl + mouse wheel to zoom.

For a full description of all image preparation options and when to use each one, see the {doc}`Advanced User Guide <advanced_user_guide>`.

---

## Protecting privacy (anonymization)

When **Remove personal information before proofreading** is turned on, the app finds names, email addresses, phone numbers, and bank account numbers in the text and replaces them with stand-ins — for example `[PERSON_1]` or `[EMAIL_1]` — before sending anything to the AI. Once the corrected text comes back, the stand-ins are replaced with the original values, so the final Word document contains the real text. The same protection applies to **content review**. This only works when proofreading is also turned on. Switching it on or off makes the app proofread and review already processed documents again, because their text is now sent in a different form.

You do not need this while everything runs on your own computer — it matters when proofreading or content review uses an online service.

This feature requires the **Privacy add-on** to be installed. You can install it from inside the app — see *Installing extra features* below.

:::{warning} Anonymization does not guarantee full privacy protection
The anonymizer helps reduce exposure of personal information but is not a certified privacy tool. AI models can miss names they were not trained on, and reading errors may cause some personal information to go undetected. Always review the anonymized text before sending sensitive documents to an online service. You remain responsible for compliance with applicable privacy laws.
:::

To see how this will affect a specific document, select its row in the results list and choose **Personal Information Preview…** — the app shows the original and anonymized text side by side.

For detailed settings and configuration options for the anonymizer, see the {doc}`Advanced User Guide <advanced_user_guide>`.

---

## Where your documents are processed

The box at the top of the settings panel lists every step of the current setup and where it runs:

| Dot | Meaning |
|---|---|
| Green — *On this computer* | Nothing leaves your computer for this step. |
| Orange — *On another computer (name)* | The step runs on an Ollama server that is not this computer, for example a school server. |
| Red — *Online service (name)* | The step runs on the internet. Ollama cloud models count as online services. |

Below the list the box says either **Everything stays on this computer.** or **Some data leaves this computer.** — and, where it helps, what you can do about it (for example, turning on *Remove personal info*). The same one-line "runs on …" note appears next to the service pickers in **Settings**.

Before a run that sends something away from your computer, the app asks you to confirm, listing the steps and whether names are replaced. "Replaced where detected" is deliberate wording: the replacement is automatic and can miss a name (see the warning under *Protecting privacy*). You can tick **Don't ask again for these settings**; the question comes back as soon as the setup changes (another service, or names no longer replaced). To bring every question back, use **Advanced Settings → Ask Before Sending Data Away Again**.

---

## AI proofreading

When **Enable proofreading** is turned on, the text from each document is sent to an AI for review and correction. The corrected text appears alongside the original in the results list.

### Choosing an AI service

Select a service in the **Proofreading service** dropdown. Ollama (AI running locally on your computer) requires no account or access code. Online services require an access code — provided by the service — which you enter in **Advanced Settings → Connections & API Keys…**.

**Recommended proofreading models:**

| Service | Model | Notes |
|---|---|---|
| Ollama | `gpt-oss:20b` | Best quality for local proofreading |
| Ollama | `gemma3:12b` | Strong quality, lighter than the 20b model |
| Ollama | `llama3.1:8b` | Fast, lightweight option |
| OpenAI | `gpt-4o` | High quality online option |
| Anthropic | `claude-3-5-sonnet-latest` | High quality online option |
| Google | `gemini-2.0-flash` | Fast online option |

To use a local Ollama proofreading model, download it first in **Advanced Settings → Downloads…** → **Ollama Models** tab.

---

## Content review

The content review feature uses an AI to read your document text and write a short report — checking whether the content is complete and consistent, noting any gaps or unclear passages, and flagging anything that may need a closer look. It focuses on the meaning and completeness of the content itself, not on spelling or grammar. In **automatic** mode it runs right after proofreading. In **manual** mode, select rows in the results list and trigger it from the right-click menu (**Run Content Review**).

Lighter AI models work well for content review:

| Service | Model |
|---|---|
| Ollama | `gemma3:12b` |
| Ollama | `llama3.1:8b` |
| OpenAI | `gpt-4o-mini` |
| Anthropic | `claude-3-haiku-20240307` |
| Google | `gemini-2.0-flash` |

---

## Your results

The **Processing** tab shows one row per document with its current status — `done`, `error`, `skipped` (already up to date), `processing`, or `correcting` while running — and a short preview of the extracted and corrected text. Double-click a row to open the full text in the **Preview** tab.

Right-click a row for more options:

- **Re-run Text Recognition** — re-extract the text for the selected rows.
- **Re-run Proofreading** — run the proofreading step again for the selected rows.
- **Run Content Review** — generate a content review report for the selected rows without reprocessing anything else.
- **Export as Word Document…** — save a Word file for the selected rows; a single row prompts you to choose a file name, multiple rows prompt for a destination folder.
- **Personal Information Preview…** — compare the original extracted text and the anonymized version side by side.

---

## Stats tab

The **Stats** tab shows:

- a **timing chart** — how long each file took for text recognition and proofreading, and
- a **system monitor** — live usage of your computer's processing power and memory, plus (when a supported graphics card is present) graphics card load, video memory, and temperature.

Graphics card monitoring requires a small extra add-on. The app will offer to install it automatically if a supported card is detected.

---

## Installing extra features

Extra features can be installed from inside the app — no external downloads or technical steps are needed. Each installer shows a live progress log.

| Feature | Where to start | What it enables |
|---|---|---|
| **Privacy add-on** | *Install Privacy Add-on* button (shown when the feature is missing) | Removes personal information before proofreading |
| **Language model for anonymization** | **Advanced Settings → Anonymizer Settings…** → *Download Model* | Choose your document language; the multilingual model is a safe default |
| **PaddleOCR** | **Settings → Text Recognition…** when PaddleOCR is selected | An alternative recognition method (~400 MB download) |
| **Graphics card monitoring** | Offered automatically when a compatible card is found (Stats tab) | NVIDIA or AMD/Linux graphics card stats |

---

## Tips

- **Word documents are opt-in** — a run saves results internally and fills the list by default. Turn on Word output in **Output Settings…** to write documents automatically during a run, or export on demand from the results list.
- **Sub-folders** — enable **Recursive** in Output Settings to include files inside sub-folders in a single run.
- **Output formats** — the **table** format puts the original image, extracted text, and corrected text in three columns; the **comments** format attaches the corrections as Word comments next to the original text.
- **Check anonymization on a real file** — select a row in the results list and choose **Personal Information Preview…** to see exactly what the app will hide before sending text to the AI.
