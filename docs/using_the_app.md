# Using Teacher's Teammate

## The app window

The app is divided into two main areas:

- **Settings panel (left side)** — everything you need to set up a run: which folders to use, which reading method to apply, whether to use AI proofreading, and so on. At the top, a box called **Where your documents are processed** shows, for each step, whether it runs on this computer (green dot), on another computer (orange) or on an online service (red) — see *Where your documents are processed* below. Below the settings you will find coloured status indicators (one per feature the current setup uses) and the action buttons — **Run Selected**, **Stop**, **Export**, **Restore Defaults** — with a progress bar.

- **Work area (right side)** — three tabs:
  - **Processing** — a list of your files (one row per document) with status indicators and a live activity log below it.
  - **Preview** — the original document image next to the extracted and corrected text for the selected file.
  - **Stats** — a chart showing how long each file took to process, plus live system resource usage (CPU, memory, and GPU if available).

- **Menu bar** at the top — two settings menus plus Help:
  - **Settings** — Text Recognition…, Proofreading Settings…, Content Review Settings…, Output Settings…
  - **Advanced Settings** — Connections & API Keys…, Downloads…, Privacy Settings…
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

The app looks at your computer — the **My computer** setting shows what it found: your graphics card and its memory, and the memory of the computer itself — and suggests models that work well on it. It is usually right; if it could not check your computer it says so there (the details are in the log) instead of staying silent; you can change the choice yourself under **Advanced Settings → Connections & API Keys…** or in **Downloads**, and every window then uses your choice. If Ollama runs on a different computer, pick that computer's type in the list.

The best models per computer type:

| Your computer | Best reading model | Best writing model |
|---|---|---|
| Basic computer or small graphics card (under 8 GB) | `glm-ocr:q8_0` | `qwen3:4b` |
| Graphics card with about 8 GB memory | `hf.co/prithivMLmods/chandra-ocr-2-GGUF:Q8_0` | `qwen3.5:9b` |
| Graphics card with 16 GB memory or more | `hf.co/prithivMLmods/chandra-ocr-2-GGUF:Q8_0` | `qwen3.5:9b` |

To get a working setup in one step, open **Advanced Settings → Downloads…**, stay on the **Ollama Models** tab (it opens first), and click **Select the best models for my computer** — the app ticks the two models it recommends for your computer. Then click **Install / Download Selected**. On the very first start the app also pre-selects the best models for your computer, and the "model missing" message before a run takes you straight to the Downloads dialog with the missing models already ticked.

Models marked **Slower** are bigger than your graphics card holds but fit with the help of your computer's memory: part of the model runs on the processor, so they work, only slower. Greyed-out models are too large for your computer. They still work — but reading or proofreading can become very slow, so the app asks before downloading one. Models you add yourself stay in the "Other models" group, where each one shows whether it reads scans and writes or only writes. Reading models are meant for turning scans into text; the app warns you when you pick one for proofreading or content review.

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

Before a run that sends something away from your computer, the app asks you to confirm, listing the steps and whether names are replaced. "Replaced where detected" is deliberate wording: the replacement is automatic and can miss a name (see the warning under *Protecting privacy*). You can tick **Don't ask again for these settings**; the question comes back as soon as the setup changes (another service, or names no longer replaced). Open **Advanced Settings → Privacy Settings…** to see everything you chose not to be asked about, in words (for example *Proofreading: Online service (Mistral) — receives text, names sent as written*). There you can take back single answers or **Ask again for all**; the changes apply when you press OK. The same window holds the personal-info settings (detection and patterns).

---

## AI proofreading

When **Enable proofreading** is turned on, the text from each document is sent to an AI for review and correction. The corrected text appears alongside the original in the results list.

### Choosing an AI service

Select a service in the **Proofreading service** dropdown. Ollama (AI running locally on your computer) requires no account or access code. Online services require an access code — provided by the service — which you enter in **Advanced Settings → Connections & API Keys…**: pick the service, press **Add key**, paste the code (**Show** lets you check it). Services that share one code, such as Mistral and Mistral (EU), have one row.

When you pick a service, the app asks it for its current list of models. The line under the model box says whether that worked (*5 models*), whether the list is the app's own built-in list because the service cannot be asked, or why asking failed (no key, the key was refused, no connection) with **Try again**. If the service's support is not installed yet, an **Install…** button opens **Downloads** on the right tab.

**Recommended proofreading models:**

| Service | Model | Notes |
|---|---|---|
| Ollama | `qwen3.5:9b` | Default. Explains every change and also reads scans |
| Ollama | `mistral-nemo:12b` | Good with long texts, explains changes |
| Ollama | `gpt-oss:20b` | Strongest writer, but no explanations — corrections only |
| OpenAI | `gpt-4o-mini` | High quality online option |
| Anthropic | `claude-haiku-4-5` | Fast online option |
| Google | `gemini-2.0-flash` | Fast online option |

To use a local Ollama proofreading model, download it first in **Advanced Settings → Downloads…** → **Ollama Models** tab. Models the app has checked for explanations show a small `explanations ✓` note in the model list.

### Reading the proofreading notes

Proofreading does not just return a corrected text: it explains every change and lists the
passages that it could not make sense of, even when they are grammatically correct.

Open a document and switch to the **Changes** tab. Its title carries the counts —
`Changes (7 · 2 ?)` means 7 corrections in the list and 2 unclear passages. The same
counts appear in the status line below the text, in the filter buttons, and on the command
line. A correction of several words (`his friend → her friends`) can be marked in more than
one place; it has one explanation, shown at each of them.

The left half is the text with the corrections marked, as before. The marked words are
links: hovering over a change shows the reason, and clicking it marks the change in strong
yellow and marks the matching note on the right. Clicking the number of a note on the right
does the same the other way round and scrolls the text to the change. Passages highlighted in
orange are unclear expressions; they link to their note the same way. A difference in line
breaks or spaces alone is not counted as a change.

The right half lists the notes. Use the filter at the top to see **All**, only the
**Changes**, or only the **Unclear** items:

- A **change** shows what was replaced (`have → has`), its kind (*Grammar*,
  *Word choice*, *Word order*), and one sentence saying why. **Spelling and punctuation
  corrections** explain themselves, so they are not listed one by one: the text still marks
  them, and one line at the end of the list (`▸ 5 spelling and punctuation corrections`)
  opens them. They are not counted in the title either. If you edit the
  text yourself in the **Proofread Text** tab, your own edits appear as `your change`, and a
  correction you undo moves to a section **Not in the text** at the end of the list (click
  its title to open it). A correction the AI made without giving a reason reads
  `no explanation`.
- An **unclear expression** has three parts: the original wording, why it is unclear, and a
  recommended version. The **Unclear** filter shows them as a three-column table. When the
  intended meaning cannot be told from the text, the recommendation is labelled
  **Suggestion** — treat it as one option, not as a correction.

The **Proofread Text** tab stays clean: it holds only the corrected text, no markers, so you
can keep editing it and export it. The results list shows a small `?2` badge next to files
that contain unclear expressions.

Two settings belong to the review. The **Level** dropdown in the proofreading step (A1–C2,
default B2) tells the AI how strictly to correct: at B2 it fixes real errors and leaves a
correct though simple wording alone. **Explanation language**, in **Settings →
Proofreading Settings…**, sets the language of the explanations and of the note tables;
*Same as the text* keeps them in the language of the document. The same dialog holds
**Corrections only (faster)**.

If you switch on **Corrections only (faster)**, proofreading returns the corrected text
without explanations and without the unclear list — faster, and it works with every model.
Every model is asked for explanations unless you tick **Corrections only**; models the app
has checked show `explanations ✓`. When a model fails to answer in the explanation format
(`gpt-oss:20b`, for example, is not checked yet), that single file falls back to corrections
only automatically; a short banner above the text then says so. With an older Ollama
(before version 0.5) the app also uses corrections only and tells you that updating Ollama
gives you the explanations.

### Your own instructions

**Settings → Proofreading Settings… → Proofreading Instructions** (and the panel's
**Instructions…** button) shows what the AI is told. The **Instructions** box always holds the
text that is really used: the built-in instructions of the document language, matched to the
**Level** setting (`{level}` is replaced by it). You can change them right in the box.
**Reset to default** puts the built-in text back. As long as the box holds the built-in text,
it keeps following the document language; once you change it, your instructions are used for
every language, and a one-line note under **Instructions…** in the panel says so. If your own
text sets an output format itself ("return only …", "JSON", "[unclear]"), a hint warns you
that it may confuse the model, because the app sets the format.

**Answer format (advanced)**, collapsed below, is the part the app adds: it tells the model
how to fill in its reply — corrected sentences with an explanation each, a list of unclear
expressions, never markers in the text — and that corrections stay in the language of the
document while explanations use the explanation language. There is one format for **With
explanations** and one for **Corrections only**; both can be edited, each with its own
**Reset to default**. The app reads the reply in this format, so change it only if you know
what you are doing: a hint warns you when an edited format no longer names a field the app
needs, and when the model does not follow it, the file falls back to corrections only. A format
you edited is tied to the version of the app it was written for; after an update that changes
the format, your edit is not used and the built-in one applies again. For Markdown texts a
read-only note about crossed-out and inserted words is added as well.

---

## Content review

The content review feature uses an AI to read your document text and write a short report — checking whether the content is complete and consistent, noting any gaps or unclear passages, and flagging anything that may need a closer look. It focuses on the meaning and completeness of the content itself, not on spelling or grammar. In **automatic** mode it runs right after proofreading. In **manual** mode, select rows in the results list and trigger it from the right-click menu (**Run Content Review**).

Lighter AI models work well for content review:

| Service | Model |
|---|---|
| Ollama | `gemma3:12b` |
| Ollama | `llama3.1:8b` |
| OpenAI | `gpt-4o-mini` |
| Anthropic | `claude-haiku-4-5` |
| Google | `gemini-2.0-flash` |

---

## Your results

The **Processing** tab shows one row per document with its current status — `done`, `error`, `skipped` (already up to date), `processing`, or `correcting` while running — and a short preview of the extracted and corrected text. Double-click a row to open the full text in the **Preview** tab.

Right-click a row for more options:

- **Re-run Text Recognition** — re-extract the text for the selected rows.
- **Re-run Proofreading** — run the proofreading step again for the selected rows.
- **Run Content Review** — generate a content review report for the selected rows without reprocessing anything else.
- **Export ▸** — save the selected rows (the **Export** button below the settings offers the same choices, for the selected rows or all rows when none is selected). A single row prompts you to choose a file name, multiple rows prompt for a destination folder.
  - **Word Document…** — a Word file with the extracted and proofread text.
  - **Text File…** — the extracted text as a plain text file (`<name>.txt`), without any Markdown markup.
  - **Anonymized Text File…** — the extracted text with names and other personal data replaced by placeholders such as `[PERSON_1]` (`<name>_anonymized.txt`). It uses your **Privacy Settings…** and runs entirely on this computer. The list of replaced names is not saved, so the original names cannot be recovered from the file. This needs the privacy add-on and a language model for your document language; if one is missing you are told once.

  A text file is never written over any of the documents in your list, so an existing `essay.txt` you are processing stays untouched. When you export several files into one folder, files from sub-folders of your input folder keep those sub-folders (`class1/essay.png` → `<folder>/class1/essay.docx`), so documents with the same name do not replace each other. You are asked before existing files there are replaced, and a document whose name is already taken by another file of the same export is skipped and reported. If you work with a whole folder, you are also asked first before saving into it, because the next run would read the new file as another document.
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
| **Language model for anonymization** | **Advanced Settings → Privacy Settings…** → *Personal info: detection* → *Download Model* | Choose your document language; the multilingual model is a safe default |
| **PaddleOCR** | **Settings → Text Recognition…** when PaddleOCR is selected | An alternative recognition method (~400 MB download) |
| **Graphics card monitoring** | Offered automatically when a compatible card is found (Stats tab) | NVIDIA or AMD/Linux graphics card stats |

---

## Tips

- **Output files are opt-in** — a run saves results internally and fills the list by default. In **Output Settings…** choose which files a run writes automatically: **Word documents**, **text files** (`<name>.txt`) and **anonymized text files** (`<name>_anonymized.txt`, same rules as the export above). Or export on demand from the results list. Text files need an output folder outside the input folder; otherwise the next run would read them as new documents, so a run with text files going there does not start. (The Export menu only asks first, because it is a one-off save you may want in that folder.) If two documents would write the same output file — for example `essay.png` and `essay.pdf` in one folder — the second one is reported as failed instead of replacing the first.
- **Sub-folders** — files inside sub-folders of the input folder are always included, and their output files go into the same sub-folders below the output folder (`class1/essay.png` → `<output>/class1/essay.docx`).
- **Output formats** — the **table** format puts the original image, extracted text, and corrected text in three columns; the **comments** format attaches the corrections as Word comments next to the original text.
- **Check anonymization on a real file** — select a row in the results list and choose **Personal Information Preview…** to see exactly what the app will hide before sending text to the AI.
