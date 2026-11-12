# Using Teacher's Teammate

## The app window

The app is divided into two main areas, plus a separate **Settings** window:

- **Workflow panel (left side)** — everything that changes from batch to batch, in numbered steps: your documents, their language, whether to remove personal information, proofread or review, and where the results go (**Step 6: Results**). At the top, a box called **Where your documents are processed** shows, for each step, whether it runs on this computer (green dot), on another computer (orange) or on an online service (red) — see *Where your documents are processed* below. Below the settings you will find coloured status indicators (one per feature the current setup uses) and the action buttons — **Run Selected**, **Stop**, **Export** — with a progress bar.

- **Work area (right side)** — three tabs:
  - **Processing** — a list of your files (one row per document) with status indicators and a live activity log below it.
  - **Preview** — the original document image next to the extracted and corrected text for the selected file.
  - **Stats** — a chart showing how long each file took to process, plus live system resource usage (CPU, memory, and GPU if available).

- **Menu bar** at the top:
  - **File** — Open Folder…, Open Files…, Export ▸ (the same choices as the **Export** button), Quit
  - **Settings** — AI models…, Scan quality…, Privacy…, Add-ons…, and **All settings…** (`Ctrl+,`). Every entry opens the same **Settings window** on that page.
  - **Help** — User Guide…, Third-Party Licenses…, About…

### The Settings window

What you set up once lives in the Settings window, grouped by topic. Pick a page in the list on the left:

| Page | What it holds |
|---|---|
| **AI models** | Which method reads the handwriting (always on this computer), and which AI service and model proofreads and reviews. One model serves both steps unless you tick **Use a different model for content review**. Every service is listed, grouped by where it runs, and marked *key needed* while it has no key; choose an online service and its **key row appears right below it**: paste the key and the model list loads, which also checks that the key works. If text then goes to an online service with names in it, the page says **"Remove personal info" will be turned on** when you press OK (you can keep it off). The context window setting appears here only when it changes something |
| **This computer** | Shown under AI models in the sidebar: what the AI models need from this computer. First how powerful it is (**My computer**; the model suggestions follow it) and its graphics card, then the address of the local AI server (usually this computer, or your school's own server) |
| **Online services** | Also under AI models: the access keys (API keys) of the online AI services. The same keys you enter under the service on AI models, all in one list where you can remove one or add one for another service. Not needed while everything runs on this computer. Keys are kept in your system's keychain (macOS Keychain, Windows Credential Manager, or the Linux desktop keyring); where there is none, the page says that the key is kept in the app's settings file, not encrypted |
| **Scan quality** | How scans and photos are cleaned up before they are read: image preparation and its **Preview…**, corrections and enhancements; under **Expert settings** the PDF resolution and the PDF text layer |
| **Privacy** | What may leave this computer (the answers you chose not to be asked about again), and how names and other personal information are found and replaced |
| **Add-ons** | Downloads of AI models for this computer and of optional parts: handwriting engines, online AI services, name detection models, performance monitoring |

Changes on the pages are saved when you press **OK** and dropped when you press **Cancel**. A key you enter is used right away, so the model list can load, and saved on OK; Cancel forgets it. The exception is on **Add-ons**, where a download or installation starts right away and is not undone by Cancel. **Restore defaults…** at the bottom resets either the page you are on or all settings (the second choice asks again; it also forgets the answers you asked not to be asked about again, and keeps downloaded models and saved API keys). Which instructions the AI gets and how a step behaves stay in the main window, under each step's **Instructions…** button.

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

The app recognises the text in your documents using the method you choose in **Settings → AI models…**.

### Ollama (recommended)

Ollama is a free program that runs AI reading models on your own computer. Teacher's Teammate sends each prepared image to Ollama, which reads the text and returns it. This gives the best results for handwriting and difficult scans.

**Ollama must be running** before you click Run. Download it from [ollama.com/download](https://ollama.com/download) and install it like any other program. The default connection is set to your own computer. If you have Ollama running on a different computer on your home network, enter its address in **Settings → This computer…**.

### Choosing AI models

Teacher's Teammate needs two AI models, and you do not need to know anything about "VRAM" or "model sizes" to pick good ones:

- a **reading model** turns your scans into text,
- a **writing model** proofreads and reviews the text.

The app looks at your computer — the **My computer** setting shows what it found: your graphics card and its memory, and the memory of the computer itself — and suggests models that work well on it. It is usually right; if it could not check your computer it says so there (the details are in the log) instead of staying silent; you can change the choice yourself under **Settings → This computer…**, and every page then uses your choice. If Ollama runs on a different computer, pick that computer's type in the list.

The best models per computer type:

| Your computer | Best reading model | Best writing model |
|---|---|---|
| Basic computer or small graphics card (under 8 GB) | `glm-ocr:q8_0` | `qwen3:4b` |
| Graphics card with about 8 GB memory | `hf.co/prithivMLmods/chandra-ocr-2-GGUF:Q8_0` | `gemma4:e4b` |
| Graphics card with 16 GB memory or more | `hf.co/prithivMLmods/chandra-ocr-2-GGUF:Q8_0` | `gemma4:12b` |

To get a working setup in one step, open **Settings → Add-ons…**, stay on the **AI models for this computer** tab (it opens first), and click **Select the best models for my computer** — the app ticks the two models it recommends for your computer. Then click **Install / Download Selected**. On the very first start the app also pre-selects the best models for your computer, and the "model missing" message before a run takes you straight to **Settings → Add-ons…** with the missing models already ticked.

The app rates each model for your computer. Models marked **Slower** or **Very slow** are bigger than your graphics card holds but fit with the help of your computer's memory: part of the model runs on the processor, so they work, only more slowly (a page can take minutes when it is very slow). Greyed-out models (**Won't fit**) do not fit your computer: the memory left after the system's share (the larger of 8 GB and a fifth of your memory) cannot hold the rest, so the model would have to use the disk as memory and the computer would become unusable. The app tells you so and asks before downloading one. If the memory that is free at that moment is not enough it warns you before the run and offers **Run anyway** — the computer would then have to use its disk as memory and become extremely slow, so the safe answer is **Cancel** and closing other programs first. Models you add yourself stay in the "Other models" group, where each one shows whether it reads scans and writes or only writes. Reading models are meant for turning scans into text; the app warns you when you pick one for proofreading or content review.

### Other recognition methods

Additional recognition methods are available for specific situations — for example, working without internet access or on computers with limited resources. For details, see the {doc}`Advanced User Guide <advanced_user_guide>`.

### Recognition always stays on computers you control

Reading a page needs the whole page — the handwriting, the student's name at the top, the grade in the margin. None of that can be hidden beforehand, because names can only be found once the text has been read. For this reason Teacher's Teammate does **not** offer online services (such as ChatGPT, Claude or Gemini) for text recognition, and it also refuses Ollama *cloud* models (names ending in `-cloud` or `:cloud`), which run on Ollama's servers rather than on your computer. If you try to start a run with one of these, the app explains why and does not start.

If your saved settings from an older version used an online service for text recognition, the app tells you at start-up that it now uses Ollama on your computer instead.

Ollama on another computer that you or your school run (see **Settings → This computer…**) is allowed; the box described below then shows it as "On another computer" with its name.

---

## Image preparation

Before reading the text, the app can adjust each image to improve accuracy. **Grayscale** is the recommended starting point when using Ollama and works well in most situations.

To compare settings before processing a large folder, open **Settings → Scan quality…** and use the **Preview…** — it shows the original and prepared image side by side. Use Ctrl + mouse wheel to zoom.

For a full description of all image preparation options and when to use each one, see the {doc}`Advanced User Guide <advanced_user_guide>`.

---

## Protecting privacy (anonymization)

When **Remove personal information** is turned on, the app finds names, email addresses, phone numbers, and bank account numbers in the text and replaces them with stand-ins — for example `[PERSON_1]` or `[EMAIL_1]`. This happens straight after text recognition, so it also works with **recognition only**: the run log notes how many items were hidden, and nothing leaves the computer to make it happen. Once the corrected text comes back from proofreading, the stand-ins are replaced with the original values, so the final Word document and text file contain the real text. The same protection applies to **content review**. Switching it on or off makes the app proofread and review already processed documents again, because their text is now sent in a different form.

The anonymizer runs once per text and settings per session: what a run hid is reused by **What is sent for this file…**, by **Anonymized Text File…** and by the privacy view setting, so the app does not analyse the same text twice. Only the anonymized text file and that view keep the placeholders; Word documents and plain text files always hold the original names.

You do not need this while everything runs on your own computer — it matters when proofreading or content review uses an online service.

This feature requires the **Privacy add-on** to be installed. You can install it from inside the app — see *Installing extra features* below.

:::{warning} Anonymization does not guarantee full privacy protection
The anonymizer helps reduce exposure of personal information but is not a certified privacy tool. AI models can miss names they were not trained on, and reading errors may cause some personal information to go undetected. Always review the anonymized text before sending sensitive documents to an online service. You remain responsible for compliance with applicable privacy laws.
:::

To see how this will affect a specific document, select its row in the results list and choose **What is sent for this file…** — the **Personal information** tab there shows the original and anonymized text side by side.

For detailed settings and configuration options for the anonymizer, see the {doc}`Advanced User Guide <advanced_user_guide>`.

---

## Where your documents are processed

The box at the top of the settings panel lists every step of the current setup and where it runs:

| Dot | Meaning |
|---|---|
| Green — *On this computer* | Nothing leaves your computer for this step. |
| Orange — *On another computer (name)* | The step runs on an Ollama server that is not this computer, for example a school server. |
| Yellow — *Online service (name) · EU · names hidden* | Anonymized text goes to a service in the European Union. |
| Orange — *Online service (name) · outside the EU · names hidden* | Anonymized text goes to a service outside the EU, or to one that does not say where it processes data. |
| Red — *Online service (name) · EU or outside the EU · names as written* | The text is sent with the names in it. |

**Personal info removal** is listed as its own line, after text recognition. It always stays
on this computer and says *On (spaCy en + de)* — the detection models in use — or *Off*.

**Where is my data processed?** Each service says where it processes documents: **EU** for a
member state of the European Union, **outside the EU** for everything else — including the
United Kingdom, Switzerland, Norway and the other EEA countries, and any provider that does
not declare its region. Hover over a line to see the vendor's home country.

The last line of the box names the least private thing that happens, in green, yellow,
orange or red — *Everything stays on this computer.*, *Anonymized text is sent to a service
in the EU.*, *Anonymized text is sent to a service outside the EU.*, *Text with names is
sent to a service in the EU.*, *Text with names is sent to a service outside the EU.* or
*Data goes to another computer you control.* Below it come the suggestions that help, for
example turning on *Remove personal info* or choosing a service in the EU. The same one-line
"runs on …" note appears next to the service pickers in **Settings**.

Before a run that sends something away from your computer, the app asks you to confirm with
the same wording: the overall level, then one line per step with its place and whether names
are replaced. "Replaced where detected" is deliberate wording: the replacement is automatic
and can miss a name (see the warning under *Protecting privacy*). You can tick **Don't ask
again for these settings**; the question comes back as soon as the setup changes (another
service, another region, or names no longer replaced). Open **Settings → Privacy…** to see everything you chose not to be asked about, in words (for example
*Proofreading: Online service (Mistral (EU)) · EU — names sent as written*). There you can
take back single answers or **Ask again for all**; the changes apply when you press OK. The
same window holds the personal-info settings (detection and patterns). Answers saved by an
earlier version are still shown and honoured, but they ask once more for setups that now
involve a region.

---

## What is sent for this file

The box tells you where your documents go. To see what actually goes there, select a row in
the results list and choose **What is sent for this file…**. The app takes a moment to hide
the personal information the same way a run would, then opens a window with one section per
step that leaves your computer — proofreading first, then content review. Steps that stay on
this computer are not listed; if nothing leaves, the window simply says *Everything stays on
this computer.*

Each section names its destination and what the service gets to read:

- the service and its host, with **EU** or **outside the EU** — the same wording as in the
  box, so *Mistral (EU)* and *Mistral* are no longer easy to confuse;
- **names hidden** or **names as written**. If *Remove personal info* is off, the section
  warns you that the names are sent as written;
- the text exactly as it will be sent, including the stand-ins the anonymizer produced.
  Hidden items are highlighted, so you can see at a glance what the service cannot read.

The instructions the app sends along with the text — your proofreading instructions, or the
content-review question — are folded away under **Show the instructions sent**. They are part
of what leaves the computer, so you can read them too.

The **Personal information** tab holds the original extracted text next to the anonymized
version, for a line-by-line comparison — this is where the former *Personal Information
Preview* went. It is there whenever *Remove personal information* is on and the file has
extracted text.

The rest of the time, hover over a **Proofreading** or **Content Review** cell in the results
list to read where that file's text went and whether its names were hidden — for example
*Sent to Mistral (EU), names hidden* or *Sent to OpenAI, outside the EU, names written*.

---

## Hiding names while you read

Hand the screen to a colleague, or show a proofread text to the class, without the names of
the children in it: **Hide personal information in the texts shown in this app** replaces every
name the anonymizer knows with its stand-in — `[PERSON_1]`, `[EMAIL_1]` and so on — in the
**Extracted Text**, **Proofread Text**, **Changes**, **Content Review** and the review notes
at once. The same stand-ins appear in every tab, so a note that talks about `[PERSON_1]` is
about the same child as in the text. Turn it on under **Settings → Privacy… → Personal info: detection → Showing texts → Hide
personal information in the texts shown in this app**; the eye button in the header above the texts does the same and follows
that checkbox.

This works like *Remove personal information* (see *Protecting privacy* above): the extracted
text is anonymized with the current detection settings and models the first time you switch
the setting on, in the background — a short *Hiding personal information…* note stands in
meanwhile. Everything already hidden in this session is reused, so switching the setting back
and forth costs nothing. The setting is **on by default**, so a fresh installation
never shows names on screen until you ask for them; your choice is remembered for the next
start.

While it is on, the **Extracted Text** and **Proofread Text** tabs cannot be edited — an edit
would be saved with the stand-ins instead of the names. The note above the texts says
**Personal information is hidden, so the texts can't be edited. Click here to show it and
edit.** Clicking it switches the setting off (like the eye button), so you can
work on the text; switch it on again afterwards. The choice is remembered, so the names stay
visible at the next start until you hide them again. Word documents, text files and the anonymized text file are not affected: only the
preview is masked, and the original names are never shown in a tooltip.

Hiding needs the **Privacy add-on** and a detection model for the document language. Without
them the checkbox stays greyed out; its tooltip points at **Settings → Add-ons**.

One limitation: a name the proofreader spelled differently — *Meier* corrected to *Maier* —
is not found in the proofread text or the changes, because the stand-ins are matched against
the spelling of the extracted text. The extracted text, the changes that do not touch a name,
and everything the anonymizer did recognize are hidden as expected.

---

## AI proofreading

When **Enable proofreading** is turned on, the text from each document is sent to an AI for review and correction. The corrected text appears alongside the original in the results list.

### Choosing an AI service

Select a service in the **Proofreading service** dropdown. Ollama (AI running locally on your computer) requires no account or access code. Online services require an access code — provided by the service — which you enter in **Settings → Online services…**: pick the service, press **Add key**, paste the code (**Show** lets you check it). Services that share one code, such as Mistral and Mistral (EU), have one row.

When you pick a service, the app asks it for its current list of models. The line under the model box says whether that worked (*5 models*), whether the list is the app's own built-in list because the service cannot be asked, or why asking failed (no key, the key was refused, no connection) with **Try again**. If the service's support is not installed yet, an **Install…** button switches to **Add-ons** with the right package ticked.

**Recommended proofreading models:**

| Service | Model | Notes |
|---|---|---|
| Ollama | `gemma4:e4b` | Default. Fast, explains every change, fits an 8 GB graphics card |
| Ollama | `gemma4:12b` | Best on a 16 GB graphics card; explains every change |
| Ollama | `qwen3.5:9b` | A model from another family; explains every change and also reads scans |
| OpenAI | `gpt-4o-mini` | High quality online option |
| Anthropic | `claude-haiku-4-5` | Fast online option |
| Google | `gemini-2.0-flash` | Fast online option |

To use a local Ollama proofreading model, download it first in **Settings → Add-ons…** → **AI models for this computer** tab. Models the app has checked for explanations show a small `explanations ✓` note in the model list.

### How we chose the proofreading models

Most research on AI proofreading uses essays written by language learners, which is
close to what students write. The main findings from 2025 and 2026:

- **Models that run on your own computer can proofread German and English student
  texts usefully**, but they still miss more errors and make more wrong changes than
  specially trained correction systems or the largest online services.
- **Their typical mistake is changing sentences that were already correct.** That is
  why Teacher's Teammate tells the model to keep the student's wording and shows every
  change, so you decide what stays.
- **For French there is no public proofreading test yet.** For French we rely on our
  own checks.
- **No study has yet checked how good these models' explanations are.** Read an
  explanation before you pass it on to a student.

**How we check a model.** Before we recommend a writing model, we test it ourselves on
German, English and French texts and on a full page. It must explain its changes in the
format the app needs (shown as `explanations ✓`) and must not lose any of the text. We
then compare the models that pass on texts written by language learners. We recommend at most two
writing models per computer type, so you do not have to compare them yourself. Last checked: November 2026.

Sources: [Wisniewski et al., 2025](https://arxiv.org/abs/2505.06004) (17 models up to 9B,
German, English, Italian, Swedish), [Goto et al., 2026](https://arxiv.org/abs/2605.13624)
(current small models on German and English learner texts),
[MultiGEC-2025](https://aclanthology.org/2025.nlp4call-1.1.pdf) (learner essays in
12 languages), [Song et al., 2024](https://aclanthology.org/2024.findings-naacl.49.pdf)
(explaining grammar errors).

### Long texts

A model can only read a limited amount of text at once (about 10,000 characters with the default local model, roughly 1,600 German words or three to four handwritten pages). Longer texts are proofread in parts: the app splits them at paragraph and sentence boundaries, proofreads each part and joins the answers back together. You get one review as usual. The one thing it cannot do is change something across a part boundary — a sentence is never split, but a correction that only makes sense together with an earlier part is not possible. If a part comes back without explanations, the app says so and keeps the explanations it has.

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
correct though simple wording alone. **Explanation language**, in the proofreading
step's **Instructions…** dialog, sets the language of the explanations and of the note tables;
*Same as the text* keeps them in the language of the document. The same dialog holds
**Corrections only (faster)**.

If you switch on **Corrections only (faster)**, proofreading returns the corrected text
without explanations and without the unclear list — faster, and it works with every model.
Every model is asked for explanations unless you tick **Corrections only**; models the app
has checked show `explanations ✓`. When a model fails to answer in the explanation format
(models without the `explanations ✓` badge are more likely to), that single file falls back to
corrections only automatically; a short banner above the text then says so. With an older Ollama
(before version 0.5) the app also uses corrections only and tells you that updating Ollama
gives you the explanations.

### Your own instructions

The **Instructions…** button of a step shows what the AI is told. The **Instructions** box always holds the
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
  - **Anonymized Text File…** — the extracted text with names and other personal data replaced by placeholders such as `[PERSON_1]` (`<name>_anonymized.txt`). It uses your **Settings → Privacy…** rules and runs entirely on this computer. The list of replaced names is not saved, so the original names cannot be recovered from the file. This needs the privacy add-on and a language model for your document language; if one is missing you are told once.

  A text file is never written over any of the documents in your list, so an existing `essay.txt` you are processing stays untouched. When you export several files into one folder, files from sub-folders of your input folder keep those sub-folders (`class1/essay.png` → `<folder>/class1/essay.docx`), so documents with the same name do not replace each other. You are asked before existing files there are replaced, and a document whose name is already taken by another file of the same export is skipped and reported. If you work with a whole folder, you are also asked first before saving into it, because the next run would read the new file as another document.
- **What is sent for this file…** — show, step by step, exactly which text leaves your computer and what the service is told to do with it (see *What is sent for this file* above).

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
| **Language model for anonymization** | **Settings → Add-ons…** → *Name detection* | Choose your document language; the multilingual model is a safe default |
| **PaddleOCR** | **Settings → AI models…** when PaddleOCR is selected | An alternative recognition method (~400 MB download) |
| **Graphics card monitoring** | Offered automatically when a compatible card is found (Stats tab) | NVIDIA or AMD/Linux graphics card stats |

---

## Tips

- **Output files are opt-in** — a run saves results internally and fills the list by default. In **Step 6: Results** choose which files a run writes automatically: **Word documents**, **text files** (`<name>.txt`) and **anonymized text files** (`<name>_anonymized.txt`, same rules as the export above). Or export on demand from the results list. Text files need an output folder outside the input folder; otherwise the next run would read them as new documents, so a run with text files going there does not start. (The Export menu only asks first, because it is a one-off save you may want in that folder.) If two documents would write the same output file — for example `essay.png` and `essay.pdf` in one folder — the second one is reported as failed instead of replacing the first.
- **Sub-folders** — files inside sub-folders of the input folder are always included, and their output files go into the same sub-folders below the output folder (`class1/essay.png` → `<output>/class1/essay.docx`).
- **Output formats** — the **table** format puts the original image, extracted text, and corrected text in three columns; the **comments** format attaches the corrections as Word comments next to the original text.
- **Check what a service sees** — select a row in the results list and choose **What is sent for this file…** to read the exact text and instructions each service receives, with the hidden names marked.
