# Using Teacher's Teammate

## The app window

The window has two tabs:

- **Documents** — the workflow panel on the left, the results list on the right.
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
on a page), **Tools** (Setup Assistant, System Monitor) and **Help**.

A line at the bottom of the window says what last happened: a document saved, results expired,
models downloaded. It keeps the last line only; the log file (*What the app writes down*) keeps
them all.

---

## Setting up

The first time you start the app, the **Setup Assistant** opens. It asks:

- the language of your class, and whether students hand in handwritten pages or typed texts;
- where Ollama (the program that runs AI models) runs: on this computer or on another PC in your
  network — **Get Ollama…**, **Start Ollama** and **Find…** help;
- whether proofreading runs on this computer (the recommended model, or one you pick) or at an
  online service (with the service's key);
- what happens to names: removed before text is sent away, or hidden on screen when everything
  stays on this computer.

It then lists what it will set and download, with a box per download you can untick, and the
space needed. Nothing starts until you press **Set up**; the downloads then run in the background.

Run the assistant again at any time from **Tools → Setup Assistant…**. It starts from your saved
answers and keeps a model that already works. **Cancel** changes nothing. After an app update it
opens once by itself, so you can check the setup against what is new.

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

- **What was read** — the transcription. Fix reading mistakes here. With names hidden it shows
  the text as it was sent to the proofreading, or as it will be sent, and a line above it says
  which. A word you select can be hidden from now on (**Hide this**), and a stand-in can be sent
  as the name it stands for (**Don't hide this**): right-click the selection. The choice applies
  to the next proofreading and is listed under **Settings → Privacy…**.
- **With corrections** — the student's words struck through, the corrections marked. Read-only.
- **Final text** — the corrected text alone. This is what is exported; change a correction here.

A document without a proofreading has only **What was read**, with a strip above it that leads to
**Proofread this document**. While a run works on the document, a strip shows its progress and
**Stop**.

**The scan** opens with a whole page in view; **Fit page**, **Fit width**, **+** and **−** (or Ctrl +
mouse wheel) change the zoom, and zooming in shows the scanned pixels. **Prepared · Original**
switches between the pages as fixed for reading and as scanned (shown only when the preparation
changed something).

**The list of corrections** on the right numbers what the text marks. Pick a row and the text
scrolls to it; click a mark in the text and its row is picked. A row you have not looked at yet
carries a **•** and is drawn bold; a row you have looked at is grey and has no dot. **Next ›** goes
to the next correction you have not looked at yet; a document opens at the first one, so coming back
to an essay continues where you stopped.

**The filter row** above the list narrows it: **Not looked at** and one chip per kind of change with
its count (**Grammar 4**). Nothing ticked shows every row. **Next ›** stays inside the filter, while
the counts above the list keep covering the whole document.

The card above the list explains the chosen item. Drag the handle under it to give a long
explanation more room, and the handle beside the list to widen the panel; both are remembered.

- A **correction** shows what was replaced (`have → has`), the sentence it stands in with the
  student's words highlighted, its kind as a chip, and one sentence saying why.
  **Take out** restores the student's words; **Replace with** writes your own version in their
  place, without leaving the view you are reading.
- Each **spelling and punctuation** correction is a row of its own; its card offers **Take out all
  4**, which restores them all at once.
- An **unclear expression** shows the words, why they are unclear, and a recommended version.
  When the meaning cannot be told from the text it is marked **Suggestion**; **Use suggestion**
  writes it into the text.
- A correction you took out stays where it is, marked `taken out`, and its card offers **Put back**;
  a change you typed yourself appears as `your change`.
- A passage the AI did not proofread is listed as `not proofread`: it stands as the student wrote
  it, so read it yourself.

**What the marks mean** — the info button beside **Next ›** shows the whole legend:

- green a correction by the AI, blue your own change, amber something unclear or not proofread to
  read yourself, grey a correction you took out, with the student's words back.
- paler than the rest: a spelling or punctuation change.
- a solid underline is a change, a dotted one something unclear or not proofread, a dashed one what
  you took out.
- under **With corrections**, red struck-through words are what the AI removed, grey struck-through
  words what the student crossed out.

**Content** beside **Corrections** shows the content review of the document, where there is one;
*What was read* opens it.

Read an explanation before you pass it on to a student: the AI can be wrong.

If the AI leaves part of the text out, the app puts your student's words back, says so, and lists
each such passage as `not proofread` to read with the rest.

**Typing** is saved automatically. **Ctrl+Z** undoes one action at a time wherever the focus is on
the page (Ctrl+Shift+Z or Ctrl+Y redoes). While a run works on a document, that document is
read-only.

**Fixing what was read** asks once (**Edit anyway**). In a text without formatting, a change away
from every correction keeps the proofreading: its corrections and your decisions about them stay,
and the final text takes the fix. The document is no longer marked as checked. Any other change, in
or next to a correction or in a text with formatting, removes the proofreading, because it was made
from the old text; the strip then says why, and the page offers **Proofread this document**.

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

The recommended models for your computer are listed in the *Advanced User Guide*; the default is
fast and fits an 8 GB graphics card.

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

Models on your own computer proofread German and English usefully but miss more errors and change
more correct sentences than the largest online services, so the app shows every change and you
decide what stays.

---

## Comparing models

To see which writing model suits your documents, open a document you have read, go to **Check** and
press **Compare models…** beside **Proofread again** — or use **Tools → Compare proofreading
models…** and pick the document in the window. Tick the models you want to compare and press **Run selected**.

- **Changes** lists every place a model changed the text: the student's words, what each model wrote
  there, and how many models agree. Choose a row to read what each model said about it.
- **Side by side** shows the student's sentences beside each model's, with the sentences a model
  changed marked in colour.
- **Overview** has one column per model: how long the answer took, how many places it changed, how
  many of those the others changed too, how many only it changed, how many it explained, and what
  went wrong.
- **Twice, for chance.** *Run again* asks the chosen model a second time and keeps both answers.
  **Overview** then says how many of its changes came in both runs and how many in one only, and
  **Changes** writes *only once* beside a wording that one answer alone produced — a change that
  may not happen again.
- **Right or wrong.** In **Changes**, choose a row and say whether the change is right (the student
  was wrong there) or wrong (the student's own words are). A verdict counts for every model that
  wrote the same wording, and clicking it again takes it back. The **Overview** then counts what
  each model got right, what it got wrong and the right changes it missed, and gives a score once
  every change of that model is marked.

Each model is asked with your settings — level, explanation language, corrections only, notes,
hiding names — and nothing else changes, so an answer is what a run with that model would give. A
model that already answered this text costs no request. One model is asked at a time, then removed
from memory, so every answer starts cold and its time includes loading the model. **Stop** ends the
comparison and interrupts a local model that is still answering; nothing half done is kept.
Closing the window, or quitting the app, stops it too, after asking.

Nothing leaves the computer before you press **Run selected**. An online model is marked `online`
in its row; before the text goes to one, you are asked the same question as before a run, and names
are hidden first when **Remove personal info** is on. While models are asked, no run starts and the
document of the window stays the same.

One text shows what these models do differently, not which of them is better — ask the same text
twice and a model may answer differently. Answers are kept in the cache and go away with **Delete
stored results…** or after the days set there; your verdicts stay in your data folder. An answer
made with other settings than today's stays stored but is not shown, because the columns must
differ in the model and in nothing else.

**Use … for proofreading** makes the model you prefer the one that proofreads from now on
(**Settings → AI models…**). The proofreading the open document has stays as it is.

---

## Content review

The content review asks an AI to read the corrected text and write a short report on its content.
The question it gets is in the **Instructions** box of **Options…** next to **Review content**.
The built-in instructions ask what the text is about, whether it is complete and consistent,
which passages are unclear or contradict each other, and for one suggestion for you. Change them
to ask what you want to know, for example whether the essay answers the task.

**Review content** can be ticked together with **Proofread**; it then runs after the
proofreading. To review single documents, right-click their rows → **Run Content Review**. The
report appears under **Content** beside **Corrections** in the **Check** tab. It uses the
proofreading model unless you tick **Use a different model for content review** under
**Settings → AI models…**.

---

## Privacy

### Where your documents are processed

The dot in front of each step says where it runs:

| Dot | Meaning |
|---|---|
| Green — *On this computer* | Nothing leaves your computer. |
| Orange — *On another computer* | Ollama on another computer in your own network. |
| Yellow — *Online service · EU · names hidden* | Anonymized text goes to a service in the EU. |
| Orange — *Online service · outside the EU · names hidden* | Anonymized text goes to a service outside the EU, or one that does not say where. |
| Red — *Online service · names as written* | The text is sent with the names in it. |

"Outside the EU" includes the United Kingdom, Switzerland and Norway. The box at the top names the
least private step and suggests what helps, for example turning on **Remove personal info**.

An Ollama address that is neither this computer nor in your own network (a public address or a
name the app cannot tell is private; see the Advanced guide) counts as an online service, not as
another computer, and as one outside the EU, since the app cannot know where it runs. Plain
`http://` to another computer is not encrypted; the app says so next to the engine or service you
pick.

Before a run that sends text away, the app asks you to confirm. If you tick **Don't ask again for
these settings**, the question returns when the setup changes. **Settings → Privacy…** lists these
answers and lets you take them back.

**Text recognition always stays on computers you control.** A page has to be read whole — with
the student's name and the grade — before any name can be found, so online services are not
offered for reading, Ollama *cloud* models are refused, and so is an Ollama server outside your
own network.

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

### What the app writes down

Problems are written to a log file in the app's data folder (**Help → Open Log Folder**), so you
can send it to whoever helps you. It holds file names, step names and error messages — never a
document's text or an access key. Nothing is sent anywhere.

### What the app keeps for earlier runs

So that a document is read only once, the app keeps what it recognized and proofread, inside its
own folder. Deleting these stored results leaves your documents and the files a run wrote alone.

**Settings → Privacy → Stored results** says how much is kept. **Delete stored results…** empties
it, and the next run reads and proofreads the pages again (an online service is paid for again).
The box **Also delete my corrections, marks and edited texts** takes your own work as well — it
cannot be brought back. **Delete stored results older than** a number of days drops what was last
processed more than that many days ago, at the next start; left off, nothing expires by itself.

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

- **Ollama (AI model)** (recommended) — an AI reading model on your computer; best for handwriting and
  difficult scans. Ollama must be running before you start a run. If it runs on another computer,
  enter its address under **Settings → This computer**, or use **Find…** there.
- **Built-in reader (PP-OCR)** — runs inside the app, without a server; good for printed pages,
  weaker on handwriting. Choose it under **Settings → AI models…**.

### Choosing AI models

The app needs a **reading model** (scans → text) and a **writing model** (proofreading and content
review). It detects your computer (**My computer** under **Settings → This computer**) and suggests
models that fit; the table is in the *Advanced User Guide*. To install them, open **Settings →
Add-ons…**, click **Select the best models for my computer**, then **Install / Download Selected**.
Models marked **Slower** or **Very slow** run partly on the processor; greyed-out models (**Won't
fit**) would make the computer unusable, and the app asks before downloading one. If too little
memory is free before a run, the app warns you; closing other programs first is the safe choice.

> **For schools:** the reading model `chandra-ocr-2` is free for personal use. A school that uses
> it as an institution should check its [license](https://github.com/datalab-to/chandra#commercial-usage).

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
| **Privacy** | The "don't ask again" answers, how personal information is found and shown, and what the app keeps for earlier runs |
| **Add-ons** | Everything optional: AI models for Ollama, support for online AI services, name detection (spaCy, needed to remove personal information), the built-in reader and page models (both come with the app). A download starts only when you ask |

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
- **Compare proofreading models…** — see *Comparing models*.
