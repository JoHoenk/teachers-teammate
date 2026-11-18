# Using Teacher's Teammate

## The app window

The window has two tabs:

- **Documents** — the workflow panel on the left, the results list and the log on the right.
- **Check** — one document at a time: the scan on the left, the text in the middle, the
  corrections on the right. Double-click a row in the results list (or press Enter) to open it;
  **Esc** or **Ctrl+1** goes back to **Documents**, **Ctrl+2** returns to the open document.

The **workflow panel** holds everything that changes from batch to batch, in four steps:

1. **Documents** — the files to process. You can also drag a folder onto the window.
2. **Language** — the language of the texts, and the **Level** of the students' writing.
3. **What should happen** — tick **Recognize text**, **Remove personal info**, **Proofread** and
   **Review content**. A grey line under each step names the AI model it uses and where it runs;
   **change…** opens the setting. **Options…** next to a step holds its instructions.
4. **Results** — which files a run writes and where.

At the top, the box **Where your documents are processed** says what leaves your computer (see
*Privacy* below). Above the **Run Selected**, **Stop** and **Export** buttons, one line says
whether a run can start; if something is missing, it names it and offers a button that fixes it
(for example **Download…**, **Enter key…**, **Get Ollama…** or **Start Ollama**). The line turns
green by itself once the problem is solved.

**Menus:** **File** (open a folder or files, export, quit), **Settings** (opens the Settings window
on a page), **Tools** (Setup Assistant, System Monitor, Benchmark) and **Help**.

---

## Setting up

The first time you start the app, the **Setup Assistant** opens. It asks:

- the language of your class, and whether students hand in handwritten pages or typed texts;
- whether Ollama (the program that runs AI models on your computer) is installed and running —
  **Get Ollama…** opens its download page, **Start Ollama** starts it;
- whether proofreading runs on this computer or at an online service (with the service's key);
- whether names are replaced before any text goes online.

It then lists what it will set and download, with a box per download you can untick, and the
space needed. Nothing starts until you press **Set up**; the downloads then run in the background.

Run the assistant again at any time from **Tools → Setup Assistant…**. It starts from your saved
answers and keeps a model that already works. **Cancel** changes nothing.

---

## Running a batch

Select the files you want (or none, to process all) and click **Run Selected**. Each file goes
through the steps you ticked. The results list shows each step of a file as waiting (`-`), running
(`▶`), done (`✓`) or failed (`✗`); *skipped* marks a scan left out because **Recognize text** is off.
You can switch to another program meanwhile.

**Stopping.** Click **Stop** once to finish the current document and then stop; the button then
reads **Stop now**, which stops at once. A request to an online service cannot be taken back, so it
finishes first. A stopped run is not an error: everything that finished is kept, and the next
**Run Selected** picks up where it left off. Closing the window during a run asks first.

**Keeping the computer awake.** During a run the app keeps the computer from going to sleep on its
own (the screen may still turn off). It cannot prevent sleep when you close a laptop's lid or the
battery runs empty. Switch this off under **Settings → General**.

**Results are remembered.** If you change only the proofreading settings and run again, the text
already read is reused. Changing the reading method or the image preparation reads the affected
files again — except documents you have already worked on (see below).

**Your own work is kept apart.** What you type, the corrections you take out, and the documents
you mark as checked are stored separately, one small file per essay. A run never writes over them.
If you change the proofreading settings and run again, the app asks first about documents whose
corrections you changed or checked: **Keep my changes** (the default), **Proofread again** (new
corrections; your changes to the old ones are removed) or **Cancel**.

**Re-running one step.** Right-click a row and choose **Re-run Text Recognition**, **Re-run
Proofreading** or **Run Content Review**. When this would replace work you did on the document, the
app asks first.

---

## Checking a document

The arrows in the header of the **Check** tab move to the previous and next document.

**Three views of the text:**

- **What was read** — the transcription. Fix reading mistakes here.
- **With corrections** — the student's words struck through, the corrections marked. Read-only.
- **Final text** — the corrected text alone. This is what is exported; change a correction here.

A document without a proofreading has only **What was read**.

**The scan** shows every page. Ctrl + mouse wheel zooms; **Original** shows the pages as they were
scanned, before the image preparation.

**The list of corrections** on the right numbers what the text marks. Pick a row and the text
scrolls to it; click a mark in the text and its row is picked. ● marks what you have already looked
at, ○ what you have not. **Next ›** on the card goes to the next correction you have not looked at
yet; a document opens at the first one, so coming back to an essay continues where you stopped. The
card above the list explains the chosen item:

- A **correction** shows what was replaced (`have → has`), its kind and one sentence saying why.
  **Take out** restores the student's words; **Change** lets you type your own version.
- **Spelling and punctuation** corrections are grouped in one row; **Take out all** restores them.
- An **unclear expression** shows the words, why they are unclear, and a recommended version.
  When the meaning cannot be told from the text it is marked **Suggestion**; **Use suggestion**
  writes it into the text.
- A correction you took out moves to the end of the list with **Put back**; a change you typed
  yourself appears as `your change`.

Read an explanation before you pass it on to a student: the AI can be wrong.

**Typing** is saved automatically. **Ctrl+Z** undoes one action at a time wherever the focus is on
the page (Ctrl+Shift+Z or Ctrl+Y redoes). While a run works on a document, that document is
read-only.

**Fixing what was read** removes the proofreading, because it was made from the old text. The app
asks once (**Edit anyway**) and then offers **Proofread this document**.

**Mark as checked** in the header records that you are done with a document (**✓ Checked**, or
**✓ Text checked** without a proofreading). Click it again to take the mark back. Right-click in the
results list → **Select checked**, then **Export**, exports only what you have checked.

The **⋯** menu offers **Proofread again**, and **Copy** puts the text on the clipboard.

---

## Proofreading

When **Proofread** is ticked, an AI corrects each text, explains every change, and lists passages
it could not make sense of.

**Choosing the service.** Choose the service and model under **Settings → AI models…**. Ollama runs
on your computer and needs no account. An online service needs an access key from the service:
choose it, and paste the key in the row that appears below it.

| Service | Model | Notes |
|---|---|---|
| Ollama | `gemma4:e4b` | Default. Fast; fits an 8 GB graphics card |
| Ollama | `gemma4:12b` | Best on a 16 GB graphics card |
| Ollama | `qwen3.5:9b` | Another model family; also reads scans |
| Mistral (EU) | `mistral-small-latest` | Online; processes data in the EU |

Models the app has tested for explanations show `explanations ✓` in the model list.

**Settings for the proofreading:**

- **Level** (step 2, A1–C2, default B2) — how strictly to correct. At B2 the AI fixes real errors
  and leaves correct, simple wording alone.
- **Options…** of **Proofread**:
  - **Explanation language** — *Same as the text*, or another language.
  - **Corrections only (faster)** — the corrected text without explanations and without the
    unclear list; about three times faster, and it works with every model.
  - **Notes for the AI** — what the model should know about the class or text, for example
    "Year 9, second foreign language" (at most 500 characters). The app's own instructions are
    fixed; **What the AI is asked** shows them.

**Long texts** are proofread in parts, split at paragraphs and sentences, and joined into one
review. A correction that needs to see across two parts cannot be made.

If a model cannot give the explanations or the unclear list, the corrected text is kept and a
banner says what is missing.

**How the models are chosen.** Before a writing model is recommended, it is tested on German,
English and French texts: it must explain its changes in the app's format and must not lose any
text, and the models that pass are compared on texts written by language learners. Research on
learner essays shows that small models running on your own computer proofread German and English
usefully, but miss more errors and change more correct sentences than the largest online services
— which is why the app shows every change, so you decide what stays.

---

## Content review

The content review asks an AI to read the corrected text and write a short report on its content.
The question it gets is in the **Instructions** box of **Options…** next to **Review content**.
The built-in instructions ask what the text is about, whether it is complete and consistent,
which passages are unclear or contradict each other, and for one suggestion for you. Change them
to ask what you want to know, for example whether the essay answers the task.

**Review content** can be ticked together with **Proofread**; it then runs after the
proofreading. To review single documents, right-click their rows → **Run Content Review**. The
report appears at the end of the list in the **Check** tab. It uses the proofreading model unless
you tick **Use a different model for content review** under **Settings → AI models…**.

---

## Privacy

### Where your documents are processed

The dot in front of each step says where it runs:

| Dot | Meaning |
|---|---|
| Green — *On this computer* | Nothing leaves your computer. |
| Orange — *On another computer* | Ollama on another computer you use. |
| Yellow — *Online service · EU · names hidden* | Anonymized text goes to a service in the EU. |
| Orange — *Online service · outside the EU · names hidden* | Anonymized text goes to a service outside the EU, or one that does not say where. |
| Red — *Online service · names as written* | The text is sent with the names in it. |

"Outside the EU" includes the United Kingdom, Switzerland and Norway. The box at the top names the
least private step and suggests what helps, for example turning on **Remove personal info**.

Before a run that sends text away, the app asks you to confirm. If you tick **Don't ask again for
these settings**, the question returns when the setup changes. **Settings → Privacy…** lists these
answers and lets you take them back.

**Text recognition always stays on computers you control.** A page has to be read whole — with
the student's name and the grade — before any name can be found, so online services are not
offered for reading, and Ollama *cloud* models are refused.

### Removing personal information

With **Remove personal info** ticked, the app replaces names, email addresses, phone numbers,
bank account numbers, street addresses and dates with stand-ins such as `[PERSON_1]` before any
text goes to the proofreading or the content review. When the answer comes back, the stand-ins
are replaced with the real values, so Word documents and text files contain the original text.
This runs on your computer. You only need it when an online service is used.

It needs spaCy and a name detection model: install them under **Settings → Add-ons… → Name
detection**.

> **Anonymization does not guarantee full privacy protection.** The anonymizer can miss names it
> was not trained on, and reading errors can hide personal information from it. Check what is
> sent before you send sensitive documents to an online service. You remain responsible for
> complying with privacy law.

### What is sent for this file

Right-click a row → **What is sent for this file…** shows, per step that leaves your computer, the
service, whether names are hidden, and the exact text that is sent, with the stand-ins
highlighted. **Show the instructions sent** shows the instructions that go with it. The
**Personal information** tab compares the original and the anonymized text. Hovering over a
**Proofreading** or **Content review** cell in the results list says where that result was sent.

### Hiding names on screen

**Hide personal information in the texts shown in this app** (under **Settings → Privacy…**, or
the **Hide names** box in the **Check** tab) shows stand-ins instead of names on screen, so you can
show a text to a colleague or the class. It is on by default. While it is on, the texts cannot be
edited; click the note above the text to show the names and edit. Exported files are not affected.
A name the proofreading spelled differently (*Meier* → *Maier*) is not hidden in the corrected text.

---

## Exporting results

A run saves its results inside the app. Under **4 Results** you can also have each run write
**Word documents**, **text files** (`<name>.txt`) or **anonymized text files**
(`<name>_anonymized.txt`) into an output folder outside the input folder.

These files show the texts at the time of the run; later corrections do not change them. When you
have finished checking, use **Export** (button, File menu or right-click) to write the files from
the current texts:

- **Word Document…** — the extracted and the proofread text.
- **Text File…** — the extracted text as plain text.
- **Anonymized Text File…** — the extracted text with stand-ins; the original names cannot be
  recovered from it.

The Word **table** layout puts the page image, the extracted text and the corrected text in three
columns; the **comments** layout shows the text with each correction and unclear passage as a Word
comment. Files from sub-folders of the input folder keep their sub-folders, and the app asks
before it replaces an existing file.

---

## Text recognition

### Methods

- **Ollama (recommended)** — an AI reading model on your computer; best for handwriting and
  difficult scans. Ollama must be running before you start a run. If it runs on another computer,
  enter its address under **Settings → This computer**, or use **Find…** there.
- **Built-in reader (PP-OCR)** — runs inside the app, without a server; good for printed pages,
  weaker on handwriting. Choose it under **Settings → AI models…**.

### Choosing AI models

The app needs a **reading model** (scans → text) and a **writing model** (proofreading and content
review). It detects your computer (**My computer** under **Settings → This computer**) and suggests
models that fit:

| Your computer | Best reading model | Best writing model |
|---|---|---|
| Basic computer or small graphics card (under 8 GB) | `glm-ocr:q8_0` | `gemma4:e4b` |
| Graphics card with about 8 GB memory | `hf.co/prithivMLmods/chandra-ocr-2-GGUF:Q8_0` | `gemma4:e4b` |
| Graphics card with 16 GB memory or more | `hf.co/prithivMLmods/chandra-ocr-2-GGUF:Q8_0` | `gemma4:12b` |

> **For schools:** the reading model `chandra-ocr-2` is free for personal use. A school that uses
> it as an institution should check its [license](https://github.com/datalab-to/chandra#commercial-usage).

To install them, open **Settings → Add-ons…**, click **Select the best models for my computer**,
then **Install / Download Selected**. Models marked **Slower** or **Very slow** run partly on the
processor; greyed-out models (**Won't fit**) would make the computer unusable, and the app asks
before downloading one. If too little memory is free before a run, the app warns you; closing
other programs first is the safe choice.

### Switching text recognition off

Untick **Recognize text** to work only with text that already exists, for example to proofread
typed essays. Text files, PDFs that contain their text and documents read before still run; scans
are marked *skipped*, get no output, and count neither as success nor as error.

### Image preparation

**Automatic** (recommended) prepares each page before it is read: it cuts out and straightens
photographed pages, turns sideways pages upright, flattens curved ones, evens out shadows and
corrects a tilt. Clean scans pass unchanged. **Original page** sends pages as they are.
**Settings → Scan quality… → Preview…** shows a page before and after.

---

## The Settings window

| Page | What it holds |
|---|---|
| **General** | **Keep the computer awake** and **Check for updates when the app starts** (both on) |
| **AI models** | The reading method, and the service and model for proofreading and content review, with the key of an online service |
| **This computer** | How powerful the computer is (the model suggestions follow it), the speed test, and the address of the Ollama server |
| **Online services** | All access keys in one list. Keys are kept in your system's keychain where there is one |
| **Scan quality** | **Automatic** or **Original page**, with **Preview…**; under **Expert settings** the PDF resolution and how a PDF's own text is used |
| **Privacy** | The "don't ask again" answers, and how personal information is found and shown |
| **Add-ons** | Downloads: AI models for this computer, online AI services, name detection, the built-in reader and page models |

**OK** saves, **Cancel** discards — except downloads on **Add-ons**, which start at once.
**Restore defaults…** resets the page or all settings; downloaded models and keys are kept.

**The update check.** Once per start the app asks GitHub whether a newer version exists and shows
a note if one does. Nothing about you or your documents is sent. Turn it off under
**Settings → General**.

---

## Tools menu

- **Setup Assistant…** — see *Setting up*.
- **System Monitor** — how long each file took, and live use of the processor, memory and (if
  present) the graphics card.
- **Benchmark…** — try one document with different text-recognition settings and compare the
  results side by side (see the *Advanced User Guide*).

---

## Installing extra features

Everything optional is installed from **Settings → Add-ons…**: AI models for Ollama, support for
an online AI service, spaCy and the name detection models (needed to remove personal
information), and the built-in reader and the page models (both come with the installed app).
A download starts only when you ask for it.
