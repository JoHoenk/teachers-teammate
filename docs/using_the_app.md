# Using Teacher's Teammate

## The app window

The app has two tabs under the menu bar, plus a separate **Settings** window:

- **Documents** — the work: the workflow panel on the left, the results list and the log on the right.
- **Check** — one document at a time, full width: the scan on the left, one text in the middle, the corrections in a list on the right, with the file name, its position in the queue and the arrows to move through the documents at the top. It is greyed out until at least one document has a result. Double-click a row in the results list (or press Enter on it) to open it here; **Esc** or **Ctrl+1** goes back to **Documents**, **Ctrl+2** returns to the document you had open. The **Copy** menu in the header puts the text of the open document on the clipboard — what was read, or the corrected text, each also with names hidden when the Privacy add-on is installed — and its two **Save** entries write what was read as a text file (also with names hidden). The **⋯** menu holds **Show Markdown source** (the stored text of the view in front, read-only) and **Proofread again** for this document. See *Reading a document in the Check tab* below.

The **Documents** tab holds:

- **Workflow panel (left side)** — everything that changes from batch to batch, in four steps: **1 Documents**, **2 Language** (with the **Level** of the students' writing), **3 What should happen** (recognize text, remove personal info, proofread, review content — each one a box you can tick) and **4 Results** (where the results go). Under each part of step 3 a grey line says which AI model does it and where it runs (for example *mistral-small-latest · Mistral (EU) · EU*); **change…** opens the place in Settings where you choose it. A step you have not ticked keeps its line in grey, so you see what ticking it would use. At the top, a box called **Where your documents are processed** gives the verdict for all steps at once — green: everything stays on this computer; yellow, orange or red: something is sent elsewhere — see *Where your documents are processed* below. Directly above the action buttons — **Run Selected**, **Stop**, **Export** — one line says whether a run can start. If something is missing it says what, with a button that opens the place that fixes it (**Download…**, **Install…**, **Enter key…**, **Choose…**, **Find server…**, **Turn on** or **Check again**); otherwise it shows *Ready* and how many documents will run. Ollama gets two lines of its own: **Ollama wasn't found on this computer** with **Get Ollama…**, and **Ollama isn't running** with **Start Ollama** (on Linux, where the app may not start it, the button's tooltip names the service command instead). Both check again every few seconds, so the line turns green by itself once the server answers. The line never blocks **Run**: the requirements are checked again when you press it.

- **Results list (right side)** — a list of your files (one row per document) with status indicators and a live activity log below it.

- **Menu bar** at the top:
  - **File** — Open Folder…, Open Files…, Export ▸ (the same choices as the **Export** button), Quit
  - **Settings** — General…, AI models…, Scan quality…, Privacy…, Add-ons…, and **All settings…** (`Ctrl+,`). Every entry opens the same **Settings window** on that page.
  - **Help** — User Guide…, Third-Party Licenses…, About…

### The Settings window

What you set up once lives in the Settings window, grouped by topic. Pick a page in the list on the left:

| Page | What it holds |
|---|---|
| **General** | How the app behaves while it works, and what it does at start: **Keep the computer awake**, on by default (see [Keeping the computer awake](#keeping-the-computer-awake)), and **Check for updates when the app starts**, also on by default (see [The update check](#the-update-check)) |
| **AI models** | Which method reads the handwriting (always on this computer), and which AI service and model proofreads and reviews. One model serves both steps unless you tick **Use a different model for content review**. Every service is listed, grouped by where it runs, and marked *key needed* while it has no key; choose an online service and its **key row appears right below it**: paste the key and the model list loads, which also checks that the key works. If text then goes to an online service with names in it, the page says **"Remove personal info" will be turned on** when you press OK (you can keep it off). The context window setting appears here only when it changes something |
| **This computer** | Shown under AI models in the sidebar (not in the Settings menu): what the AI models need from this computer. One box holds how powerful it is (**My computer**; the model suggestions follow it), its graphics card and the speed test; below it the address of the local AI server (usually this computer) |
| **Online services** | Also under AI models: the access keys (API keys) of the online AI services. The same keys you enter under the service on AI models, all in one list where you can remove one or add one for another service. Not needed while everything runs on this computer. Keys are kept in your system's keychain (macOS Keychain, Windows Credential Manager, or the Linux desktop keyring); where there is none, the page says that the key is kept in the app's settings file, not encrypted |
| **Scan quality** | How scans and photos are prepared before they are read: **Automatic** or **Original page**, with a **Preview…**, and a **Download…** line for the page models; under **Expert settings** the PDF resolution and the PDF text layer |
| **Privacy** | What may leave this computer (the answers you chose not to be asked about again), and how names and other personal information are found and replaced |
| **Add-ons** | Downloads of AI models for this computer and of optional parts: handwriting engines, online AI services, name detection models, performance monitoring |

Changes on the pages are saved when you press **OK** and dropped when you press **Cancel**. A key you enter is used right away, so the model list can load, and saved on OK; Cancel forgets it. The exception is on **Add-ons**, where a download or installation starts right away and is not undone by Cancel. **Restore defaults…** at the bottom resets either the page you are on or all settings (the second choice asks again; it also forgets the answers you asked not to be asked about again, and keeps downloaded models and saved API keys). Which instructions the AI gets and how a step behaves stay in the main window, under each step's **Options…** button.

You can also **drag a folder directly onto the window** to set it as the input folder.

Status indicators turn green once each required service is up and running.

---

## Setting up

The first time you start the app, the **Setup Assistant** opens by itself. It asks only what it cannot work out on its own, and it never changes anything until you press **Set up**.

| Page | What it asks |
|---|---|
| **Welcome** | What the app does and what the assistant will ask |
| **Your classes** | The language of your class, and whether the students hand in handwritten pages or typed texts |
| **The AI on this computer** | Whether Ollama is installed and running, and how powerful your computer is. If Ollama is missing, **Get Ollama…** opens its download page; if it is installed but stopped, **Start Ollama** starts it. The line turns green by itself once the server answers |
| **Proofreading** | Whether the AI proofreads on this computer or at an online service. Choosing an online service shows its key row right below it, and **Install now** fetches the part the app needs for it |
| **Names and personal information** | Whether names and other personal information are replaced before any text goes online. It is ticked in advance and can only ever be turned on here, never off |
| **Ready to set up** | What the assistant will set and download, one line per step, with a ticked box per download. Untick anything you do not want. Under the rows it says what the downloads add up to and how much space is free |
| **All set** | How far each download has come, and the speed test for the models that just arrived. **Select documents…** closes the window and asks you for the folder with your students' work |

The pages you see depend on your answers: with typed texts there is no reading model to choose, and with proofreading off there is nothing to ask about privacy.

**Nothing starts by itself.** The downloads begin when you press **Set up**, a search for a server elsewhere on your network starts only from its own button, and the speed test runs only when you press **Test speed**.

You can run the assistant again at any time from **Tools → Setup Assistant…**. It starts from your saved answers, keeps a model that already works, and keeps a content review that has its own model, so running it a second time changes nothing you did not ask for. **Cancel** changes nothing at all.

---

## How processing works

### Automatic processing

When you click **Run Selected**, the app works through your files one by one. You can watch the progress in the **Processing** tab — each file shows whether it is waiting, being read, being proofread, or finished. The app handles everything by itself. You do not need to stay on the screen. You can switch to another program and come back when it is done.

Each file goes through all the steps you have switched on — text recognition, removing personal information, and proofreading — in that order. Results appear in the list as soon as each file is finished.

### Keeping the computer awake

While documents are processed, the app keeps your computer from going to sleep on its own, so a long run is not interrupted. A short note under the progress bar says so. The screen may still turn off, which saves power. If your computer runs on battery, the app also reminds you to plug in the charger.

What the app cannot prevent: closing the lid (on many laptops, in particular without an external screen), a nearly empty battery, and choosing **Sleep** from the system menu (on Linux the system refuses that choice, or asks for an administrator password, until the run ends). Keep the lid open during a long run. If you would rather not have this, switch off **Keep the computer awake** under **Settings → General**. Where the system does not allow it, or where the app has no way to ask (for example under WSL), the note says that the computer may go to sleep.

### The update check

Once per start the app asks GitHub whether a newer version exists. If one does, a note appears at
the top of the window with a **Download** link; it stays until you close it. Nothing from your
documents is sent — only the question itself, which GitHub sees like any web request. This is the only thing the app sends without
being asked.

To turn it off, open **Settings → General** and clear **Check for updates when the app starts**.
The change takes effect the next time you start the app; a check that is already running is not
interrupted. With the box cleared, the app never contacts GitHub. The
{doc}`code signing policy <code_signing_policy>` lists everything the app sends and when.

### Stopping and continuing

**Stop** works in two steps:

1. **Click Stop once** to finish the document that is being processed, then stop. The button then reads **Stop now**, and the progress text says the run stops after this document.
2. **Click Stop now** to stop at once. The step in progress is cut short (an AI request on this computer is cancelled within a moment; a request to an online service cannot be taken back, so it finishes first). What was already done for that document — for example the text that was read — is kept.

A stopped run is not an error. The document that was cut short shows **stopped** in its step; documents that never started stay as they were. At the end the progress bar says, for example, *Stopped — 3 of 10 done, 1 unfinished, 6 not started*. Everything that finished is saved. When you click **Run Selected** again, the app picks up where it left off — finished work is reused automatically.

**Closing the window during a run** asks first. If you choose **Stop and close**, the run stops at once and the window closes right away; it does not wait for the run to finish. Finished documents are kept, and the document that was being processed is picked up again next time.

### Reading a document in the Check tab

After processing, double-click any row in the results list to open it in the **Check** tab. The
arrows in the header move to the previous and next document that has a result, in the order of
the list, so you can work through a whole class set without going back to **Documents**.

**One text, three views.** The switch above the text chooses what you read:

- **What was read** — the transcription, as the reader produced it. This is where you fix a
  reading mistake.
- **With corrections** — the same text with the proofreading in it: the student's words struck
  through in grey, the corrected ones marked. Read-only; it is the view for reading, not for
  typing.
- **Final text** — the corrected text alone, with no marks. This is what is exported and what
  the content review reads, and it is where you change a correction.

A document without a proofreading has only **What was read**; the other two views are greyed
out. A document that was only recognised (see *Switching text recognition off*) shows no switch
at all.

**The scan** on the left shows every stored page of the document, one below the other, fitted to
the width of the pane. Ctrl + mouse wheel zooms, and a double-click fits the page to the window
again. **Original** swaps every page for the page as it was scanned, before the image
preparation, and says *Showing the original pages*; a page whose original was not kept stays as
it is. In a narrow window the scan folds away to a strip with **Show scan**; the text and the
list keep their room.

**The list** on the right numbers the same things the text marks, in the order they appear. Pick
a row and the text scrolls to it and marks it; click a mark in the text and its row is picked.
The card above the list says what the chosen item is and offers what you can do with it — see
*Reading the proofreading notes* below. **Next ›** on the card goes to the next correction you
have not looked at yet, in the order of the list and from the top when you reach the end; when
there is none left it reads *All looked at*. The line under the list counts the items: *7
corrections · 2 unclear*.

**Typing.** Your changes are saved automatically after a short pause, and you do not need to
re-run the file. **Ctrl+Z** undoes them, one action at a time — a typed word, or a whole
**Take out** from the card — and it works wherever the focus is on the page: in the list, on the
card, or in any of the three texts. Ctrl+Shift+Z or Ctrl+Y redoes. The undo history ends when the
corrected text is filled again: when you open another document, switch **Hide names**, fix what
was read, or the document is proofread again. The text size is set with Ctrl + mouse wheel and is
remembered for the next document and the next start.

**While a run works on a document** it is read-only: the banner says *Waiting for this run*
until the run reaches it, then *Being proofread — you can edit it when it is done*. Documents
the run does not touch, and documents it has already finished, stay editable.

**Fixing what was read.** A proofreading is made from the transcription, so changing the
transcription throws the corrections away. The first time you type in **What was read** while
the document has a proofreading, a banner says so and offers **Edit anyway**; it asks once per
document. After the first stored change the proofreading is gone, the tab falls back to **What
was read**, and the header offers **Proofread this document** to make a new one from the text
you fixed.

**Marking a document as checked.** The button in the header of the **Check** tab says **Mark as
checked**; click it when you are done with the document and it turns into **✓ Checked** — or
**✓ Text checked** when the document has no proofreading, because then it is the transcription
you checked. Click it again to take the mark back. It is not part of **Ctrl+Z**: it is a
decision, not a change to the text, and typing after it does not uncheck the document. It does
not move you to the next document either — the arrows in the header do that.

The results list has a **Checked** column for it: *Checked*, *Text checked*, or empty.
Right-click → **Select checked** selects every row with either mark, which is how you export
only what you have checked: **Select checked**, then **Export**.

**What you have looked at.** The list of items on the right marks the ones you have already
seen: ● for an item you have looked at, ○ for one you have not, and the line under the list
counts them — *8 of 12 looked at*. An item counts as looked at as soon as its card is shown,
whether you change anything or not. Your own changes and the *minor* group row count once each.
A document opens at the first correction you have not looked at, so coming back to an essay
continues where you stopped, and **Next ›** walks the ones that are left. Looking at something is
never undone and never stops a run from proofreading the document again.

### Re-running a specific step

If you want to redo just one part — for example, run proofreading again with a different setting — right-click a row in the results list and choose the step you want to repeat: **Re-run Text Recognition**, **Re-run Proofreading**, or **Run Content Review**. Only the chosen step is repeated (and any steps that depend on it).

A re-run of the reading or the proofreading throws away what you did to that document, so the app
asks first when there is something to lose. **Re-run Proofreading** on a document whose
corrections you changed or checked says so and offers **Proofread again** or **Cancel**; **Re-run
Text Recognition** on a document whose transcription you fixed or checked, or whose corrections
you changed or checked, says that what you did is replaced by the new reading and that the
proofreading made from the old one is removed. **Cancel** is the default in both, and one dialog names every document of the selection
that it applies to. **Run Content Review** asks nothing: it is made from your corrected text and
changes none of it.

### How the app remembers your results

The app stores the result for every file it has processed. If you change only the proofreading settings and click **Run Selected** again, the app reuses the text it already read — it does not read your documents a second time. If you change the reading method or the image preparation setting, any affected files are re-read from scratch automatically.

Next to the text, the app keeps every prepared page of a document as an image, not only the first one, and — when the image preparation changed the page — the page as it was scanned. That takes roughly one to three megabytes per prepared page and three to eight per original page of a scan in the app's cache folder, so a class set of four-page essays can take a few hundred megabytes. Deleting the cache costs nothing but a re-run, which makes the images again. The **Check** tab shows all of them; documents that were read before this version keep only the first page until they are read again, and a page file that has gone missing is not made again by itself.

**Your own corrections are kept apart from the results.** What you type, the corrections you take
out or put back, the documents you mark as checked and the items you have looked at are stored in
a folder of their own, one small file per essay, keyed by the essay's content — so an essay keeps
its corrections wherever its results go and whatever the file is called. A run never writes over
them. Emptying the results cache therefore costs you the machine's texts and the prepared pages,
not your work: your corrected transcription is still there and the next proofreading is made from
it. Your corrections to the proofreading are kept too, as long as the proofreading they were made
on is still stored — a new proofreading is a new answer, and corrections written on the old one
would not fit it. The app tells you before that happens (below).

**A run with changed settings keeps what you worked on.** If you change the proofreading settings
and run again, a document whose corrections you changed or checked keeps its proofreading: the
results list shows it as done with the note *kept*, and hovering over it says that it was made
with other settings and how to get a new one. Before such a run starts, the app asks once and
lists the documents it applies to: **Keep my changes** (the default) keeps them and does not ask
again for these settings, **Proofread again** makes new corrections and removes your changes to
them, **Cancel** starts nothing. The same dialog names the documents whose corrections belong to
a proofreading that is not stored here any more — after an emptied cache, for example — because a
new proofreading will not contain them; whatever you answer there (except **Cancel**), those
changes are let go and the question does not come back.

Changing the settings of the **reading** does not read a page again for a document you worked on:
one whose transcription you fixed or checked, or whose corrections you changed or checked, keeps
the text it has. Only **Re-run Text Recognition** reads it again, after asking.

---

## Switching text recognition off

**Recognize text** is ticked by default: photos and scanned pages are read by the AI. Text files (`.txt`) and PDFs that already contain their text are used as they are either way, and a document whose text was recognised before is not read again.

A scan whose text you fixed or checked counts as text that already exists, even when the results
were emptied since: its pages are prepared for the scan, but not read again.

Untick **Recognize text** when you only want to work with text that already exists — for example to proofread a folder of typed essays without setting up a recognition model. Then:

- text files, PDFs with a text layer and already-recognised documents run as usual;
- **scans are skipped**: images and PDFs that have to be read from page images stay in the queue marked *skipped* (hover for the reason), get no Word document or text file, and are counted separately when the run ends — they are neither a success nor an error;
- an orange note under the box says before you press **Run** how many scans will be skipped, and the box *Where your documents are processed* no longer lists text recognition, since no page is read;
- if *every* queued document is a scan, the line above **Run** says *Nothing to read* and offers **Turn on**.

The right-click action *Re-run Text Recognition* on a row still reads that page, whatever the box says. On the command line the same switch is `--no-recognition` (setting `recognition_enabled = false`).

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
| Basic computer or small graphics card (under 8 GB) | `glm-ocr:q8_0` | `gemma4:e4b` (`qwen3:4b` on a computer with less than about 13 GB of main memory) |
| Graphics card with about 8 GB memory | `hf.co/prithivMLmods/chandra-ocr-2-GGUF:Q8_0` | `gemma4:e4b` |
| Graphics card with 16 GB memory or more | `hf.co/prithivMLmods/chandra-ocr-2-GGUF:Q8_0` | `gemma4:12b` |

To get a working setup in one step, open **Settings → Add-ons…**, stay on the **AI models for this computer** tab (it opens first), and click **Select the best models for my computer** — the app ticks the two models it recommends for your computer. Then click **Install / Download Selected**. On the very first start the app also pre-selects the best models for your computer, and the "model missing" message before a run takes you straight to **Settings → Add-ons…** with the missing models already ticked.

The app rates each model for your computer. Models marked **Slower** or **Very slow** are bigger than your graphics card holds but fit with the help of your computer's memory: part of the model runs on the processor, so they work, only more slowly (a page can take minutes when it is very slow). Greyed-out models (**Won't fit**) do not fit your computer: the memory left after the system's share (the larger of 8 GB and a fifth of your memory) cannot hold the rest, so the model would have to use the disk as memory and the computer would become unusable. The app tells you so and asks before downloading one. If the memory that is free at that moment is not enough it warns you before the run and offers **Run anyway** — the computer would then have to use its disk as memory and become extremely slow, so the safe answer is **Cancel** and closing other programs first. Models you add yourself stay in the "Other models" group, where each one shows whether it reads scans and writes or only writes. Reading models are meant for turning scans into text; the app warns you when you pick one for proofreading or content review.

### Other recognition methods

The **Built-in reader (PP-OCR)** reads pages on any computer without an AI model or a server — a good choice for printed pages, and for computers that cannot run a vision model. It reads handwriting less well. Choose it under *Recognition method* in **Settings → AI models…**; its files come with the app, and the pages never leave your computer. For details, see the {doc}`Advanced User Guide <advanced_user_guide>`.

### Recognition always stays on computers you control

Reading a page needs the whole page — the handwriting, the student's name at the top, the grade in the margin. None of that can be hidden beforehand, because names can only be found once the text has been read. For this reason Teacher's Teammate does **not** offer online services (such as ChatGPT, Claude or Gemini) for text recognition, and it also refuses Ollama *cloud* models (names ending in `-cloud` or `:cloud`), which run on Ollama's servers rather than on your computer. If you try to start a run with one of these, the app explains why and does not start.

If your saved settings from an older version used an online service for text recognition, the app tells you at start-up that it now uses Ollama on your computer instead.

Ollama on another computer that you use (see **Settings → This computer…**) is allowed; the box described below then shows it as "On another computer" with its name.

---

## Image preparation

Before reading the text, the app prepares each image to improve accuracy. **Automatic (recommended)** looks at every page and does only what it needs: it straightens and turns photographed pages, flattens curved ones, evens out shadows and corrects a tilt. Clean scans pass unchanged. **Original page** sends each page as it is, to check what the preparation changes.

Turning sideways pages and flattening curved ones need two small page models (39 MB), which come with the app. *Automatic* still works without them, it skips those two steps.

To compare settings before processing a large folder, open **Settings → Scan quality…** and use the **Preview…** — it shows the original and prepared image side by side, and says what was done ("Prepared: page turned, shadows evened out"). Use Ctrl + mouse wheel to zoom.

For a full description of all image preparation options and when to use each one, see the {doc}`Advanced User Guide <advanced_user_guide>`.

---

## Protecting privacy (anonymization)

When **Remove personal information** is turned on, the app finds names, email addresses, phone numbers, and bank account numbers in the text and replaces them with stand-ins — for example `[PERSON_1]` or `[EMAIL_1]`. This happens straight after text recognition, so it also works with **recognition only**: the run log notes how many items were hidden, and nothing leaves the computer to make it happen. Once the corrected text comes back from proofreading, the stand-ins are replaced with the original values, so the final Word document and text file contain the real text. The same protection applies to **content review**. Switching it on or off does not send anything again by itself: a document that was already proofread keeps its result, and the app remembers what was sent with it. Hover over its **Proofreading** or **Content Review** cell, or open **What is sent for this file…**, to see that the result was made with other privacy settings — proofread again if you want it sent with the current ones.

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

The box at the top of the workflow panel gives the verdict for the current setup, and the dot in front of each line under **What should happen** says where that step runs:

| Dot | Meaning |
|---|---|
| Green — *On this computer* | Nothing leaves your computer for this step. |
| Orange — *On another computer (name)* | The step runs on an Ollama server that is not this computer, for example another computer at home. |
| Yellow — *Online service (name) · EU · names hidden* | Anonymized text goes to a service in the European Union. |
| Orange — *Online service (name) · outside the EU · names hidden* | Anonymized text goes to a service outside the EU, or to one that does not say where it processes data. |
| Red — *Online service (name) · EU or outside the EU · names as written* | The text is sent with the names in it. |

**Remove personal info** has its own line under its checkbox. It always stays
on this computer and says *On (spaCy en + de)* — the detection models in use — or *Off*.

**Where is my data processed?** Each service says where it processes documents: **EU** for a
member state of the European Union, **outside the EU** for everything else — including the
United Kingdom, Switzerland, Norway and the other EEA countries, and any provider that does
not declare its region. Hover over a step's line to see the vendor's home country.

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

The instructions the app sends along with the text are folded away under **Show the
instructions sent**: the fixed prompts of the proofreading requests that run (one heading per
request, so **Corrections only** shows one), your **Notes for the AI** when you wrote any, and
the content-review question. They are part of what leaves the computer, so you can read them
too.

The **Personal information** tab holds the original extracted text next to the anonymized
version, for a line-by-line comparison — this is where the former *Personal Information
Preview* went. It is there whenever *Remove personal information* is on and the file has
extracted text.

The rest of the time, hover over a **Proofreading** or **Content Review** cell in the results
list to read where that file's text went and whether its names were hidden — for example
*Sent to Mistral (EU), names hidden* or *Sent to OpenAI, outside the EU, names written*.
Below that line the cell says how the result that is stored was really sent — *Sent to
Mistral (EU) with 2 names hidden*, or *Made on this computer (Ollama)* — and, when the
privacy settings have changed since, that it was made with other privacy settings.

The **What is sent** tab starts with the same lines for the results that are already stored,
one per step, so you can compare what went out with what the next run would send.

---

## Hiding names while you read

Hand the screen to a colleague, or show a proofread text to the class, without the names of
the children in it: **Hide personal information in the texts shown in this app** replaces every
name the anonymizer knows with its stand-in — `[PERSON_1]`, `[EMAIL_1]` and so on — in all
three views of the **Check** tab and in the list of corrections at once. The same stand-ins
appear everywhere, so a note that talks about `[PERSON_1]` is about the same child as in the
text. Turn it on under **Settings → Privacy… → Personal info: detection → Showing texts → Hide
personal information in the texts shown in this app**; the **Hide names** box in the header of
the **Check** tab does the same and follows that checkbox.

This works like *Remove personal information* (see *Protecting privacy* above): the extracted
text is anonymized with the current detection settings and models the first time you switch
the setting on, in the background — a short *Hiding personal information…* note stands in
meanwhile. Everything already hidden in this session is reused, so switching the setting back
and forth costs nothing. The setting is **on by default**, so a fresh installation
never shows names on screen until you ask for them; your choice is remembered for the next
start.

While it is on, the texts cannot be edited and the card's buttons are greyed out — an edit
would be saved with the stand-ins instead of the names. The note above the text says
**Personal information is hidden, so the texts can't be edited. Click here to show it and
edit.** Clicking it switches the setting off (like the **Hide names** box), so you can
work on the text; switch it on again afterwards. The choice is remembered, so the names stay
visible at the next start until you hide them again. Word documents, text files and the anonymized text file are not affected: only the
texts on screen are masked, and the original names are never shown in a tooltip.

Hiding needs the **Privacy add-on** and a detection model for the document language. Without
them the checkbox stays greyed out; its tooltip points at **Settings → Add-ons**.

One limitation: a name the proofreader spelled differently — *Meier* corrected to *Maier* —
is not found in the corrected text or the list, because the stand-ins are matched against
the spelling of the extracted text. The extracted text, the corrections that do not touch a
name, and everything the anonymizer did recognize are hidden as expected.

---

## AI proofreading

When **Proofread** is ticked, the text from each document is sent to an AI for review and correction. The corrected text appears alongside the original in the results list.

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

Open a document and switch to **With corrections**. The list on the right numbers the same
things the text marks, in the order they appear, and the line under it counts them: *7
corrections · 2 unclear*. The command line and the log count the same way, one line per
document.

Pick a row and the text scrolls to it and marks it in strong yellow; click a mark in the text
and its row is picked. A correction of several words (`his friend → her friends`) can be
marked in more than one place; it is one row with one explanation. A difference in line breaks
or spaces alone is not counted as a change.

The card above the list says what the chosen item is and offers what you can do with it:

- A **correction** shows what was replaced (`have → has`), its kind (*Grammar*, *Word
  choice*, *Word order*) and one sentence saying why. **Take out** puts the student's words
  back and takes the correction out of the text; **Change** goes to **Final text** and selects
  the words, so you can type your own version. A correction the AI made without giving a
  reason says so and offers the same two buttons.
- **Spelling and punctuation corrections** explain themselves, so they are not listed one by
  one: one row says *9 spelling, punctuation* and opens into its items. **Take out all** puts
  the student's words back for every one of them.
- An **unclear expression** shows the words, why they are unclear, and a recommended version.
  When the intended meaning cannot be told from the text, the recommendation is labelled
  **Suggestion** — treat it as one option, not as a correction — and **Use suggestion** writes
  it into the text.
- A correction you took out moves to the end of the list as **taken out**, with **Put back**.
- A change you made yourself in **Final text** appears as `your change`, with **Take out**.
- A correction you edited by hand keeps its explanation and says *you changed this*.

Every one of these is one edit of the text, so **Ctrl+Z** undoes it and the list follows.
**Next ›** on the card goes on to the next correction you have not looked at yet, so you can
work through a document without going back to the list.

The **Final text** view stays clean: it holds only the corrected text, no marks, so you can
keep editing it and export it. The results list shows a small `?2` badge next to files that
contain unclear expressions.

A **Content review** section at the end of the list shows the report for this document, if one
was made. It is read-only; **Run Content Review** in the results list's right-click menu makes
one.

Two settings belong to the review. The **Level** dropdown in step 2, next to the language (A1–C2,
default B2) tells the AI how strictly to correct: at B2 it fixes real errors and leaves a
correct though simple wording alone. **Explanation language**, in the
**Options…** dialog of **Proofread**, sets the language of the explanations and of the note tables;
*Same as the text* keeps them in the language of the document. The same dialog holds
**Corrections only (faster)**.

If you switch on **Corrections only (faster)**, proofreading returns the corrected text
without explanations and without the unclear list — faster, and it works with every model.
A full proofreading asks the model three times: once for the corrected text, once for the
explanations and once for the unclear expressions. **Corrections only** sends the first request
alone, so it is about three times faster. Every model is asked for explanations unless you tick
**Corrections only**; models the app has checked show `explanations ✓`. When a model cannot
answer the explanation or the unclear request (models without the `explanations ✓` badge are
more likely to), the corrected text is kept and a short banner above it says which part is
missing — the file is never lost. With an older Ollama (before version 0.5) the app also uses
corrections only and tells you that updating Ollama gives you the explanations.

### Notes for the AI

The **Options…** button of **Proofread** has a **Notes for the AI (optional)** box: what the
model should know about this class or this text, for example "Year 9, second foreign language;
ignore missing capital letters in headings". The notes go with every request of the
proofreading, so they apply to the corrected text, the explanations and the unclear list alike.
At most 500 characters are sent; the counter under the box shows how many you have used.

The instructions themselves are fixed: they are written and tested with the app, and a change
would silently change what every model is asked. Below the notes, **What the AI is asked** shows
them, one heading per request — with **Corrections only** only the first one is sent. The same
texts appear in **What is sent for this file…**.

The **Options…** button of **Review content** still has an **Instructions** box you can edit:
that prompt is yours, and the app reads the reply as plain text.

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

The results list on the **Documents** tab shows one row per document with its current status — `done`, `error`, `skipped` (already up to date), `processing`, or `correcting` while running — and a short preview of the extracted and corrected text. Double-click a row to open the full text in the **Check** tab.

**Export after checking.** The Word file and the text files a run writes are a snapshot of the
texts the document had when the run reached it. Correcting a document afterwards does not change
them — no edit and no *Checked* writes a file. When you are done, export: **Export ▸** builds the
files from the texts the document has now, so your corrections are in them. That is also why
**Select checked** followed by **Export** is the way to hand in only what you have checked.

Right-click a row for more options:

- **Re-run Text Recognition** — re-extract the text for the selected rows.
- **Re-run Proofreading** — run the proofreading step again for the selected rows.
- **Run Content Review** — generate a content review report for the selected rows without reprocessing anything else.
- **Select checked** — select every row you marked as checked in the **Check** tab, whether it is *Checked* or *Text checked*.
- **Export ▸** — save the selected rows (the **Export** button below the settings offers the same choices, for the selected rows or all rows when none is selected). A single row prompts you to choose a file name, multiple rows prompt for a destination folder.
  - **Word Document…** — a Word file with the extracted and proofread text.
  - **Text File…** — the extracted text as a plain text file (`<name>.txt`), without any Markdown markup.
  - **Anonymized Text File…** — the extracted text with names and other personal data replaced by placeholders such as `[PERSON_1]` (`<name>_anonymized.txt`). It uses your **Settings → Privacy…** rules and runs entirely on this computer. The list of replaced names is not saved, so the original names cannot be recovered from the file. This needs the privacy add-on and a language model for your document language; if one is missing you are told once.

  A text file is never written over any of the documents in your list, so an existing `essay.txt` you are processing stays untouched. When you export several files into one folder, files from sub-folders of your input folder keep those sub-folders (`class1/essay.png` → `<folder>/class1/essay.docx`), so documents with the same name do not replace each other. You are asked before existing files there are replaced, and a document whose name is already taken by another file of the same export is skipped and reported. If you work with a whole folder, you are also asked first before saving into it, because the next run would read the new file as another document.
- **What is sent for this file…** — show, step by step, exactly which text leaves your computer and what the service is told to do with it (see *What is sent for this file* above).

---

## Tools menu

The **Tools** menu holds what is not part of a batch.

**Setup Assistant…** opens the assistant described under *Setting up* above. It starts from your saved answers, so it is the quickest way to change the language, the reading method or where the proofreading runs in one go.

**System Monitor** opens its own window, which can stay open next to the main window while a batch runs (closing the main window closes it too), with

- a **timing chart** — how long each file took for text recognition and proofreading (it collects the data of every run, also while the window is closed), and
- a **live monitor** — usage of your computer's processing power and memory, plus (when a supported graphics card is present) graphics card load and video memory. It only updates while the window is open.

Graphics card monitoring requires a small extra add-on. The app will offer to install it automatically if a supported card is detected.

**Benchmark…** opens the Benchmark app in its own window, to try a document with different text-recognition settings and compare the results side by side (see the advanced guide). It takes the AI server and computer profile from your saved settings when it opens. A benchmark and a batch can run at the same time, but they then share the graphics card, so both are slower. Closing the main window closes the Benchmark window too.

---

## Installing extra features

Extra features can be installed from inside the app — no external downloads or technical steps are needed. Each installer shows a live progress log.

| Feature | Where to start | What it enables |
|---|---|---|
| **Privacy add-on** | *Install Privacy Add-on* button (shown when the feature is missing) | Removes personal information before proofreading |
| **Language model for anonymization** | **Settings → Add-ons…** → *Name detection* | Choose your document language; the multilingual model is a safe default |
| **Built-in reader** | **Settings → AI models…** when *Built-in reader (PP-OCR)* is selected, or **Settings → Add-ons… → Reading models** | A recognition method that needs no server and runs on any computer; best for printed pages (31 MB, included with the app) |
| **Page models** | **Settings → Scan quality…** under *Automatic*, or **Settings → Add-ons… → Reading models** | Two small models (39 MB, included with the app) that let *Automatic* image preparation turn sideways pages and flatten curved ones; it works without them, skipping those two steps |
| **Graphics card monitoring** | Offered automatically when a compatible card is found (**Tools → System Monitor**) | NVIDIA or AMD/Linux graphics card stats |

---

## Tips

- **Output files are opt-in** — a run saves results internally and fills the list by default. In step **4 Results** choose which files a run writes automatically: **Word documents**, **text files** (`<name>.txt`) and **anonymized text files** (`<name>_anonymized.txt`, same rules as the export above). Or export on demand from the results list. Text files need an output folder outside the input folder; otherwise the next run would read them as new documents, so a run with text files going there does not start. (The Export menu only asks first, because it is a one-off save you may want in that folder.) If two documents would write the same output file — for example `essay.png` and `essay.pdf` in one folder — the second one is reported as failed instead of replacing the first.
- **Sub-folders** — files inside sub-folders of the input folder are always included, and their output files go into the same sub-folders below the output folder (`class1/essay.png` → `<output>/class1/essay.docx`).
- **Output formats** — the **table** format puts the original image, extracted text, and corrected text in three columns; the **comments** format shows the extracted text without its formatting (bold, italics and struck-out words are written as plain words) and attaches every correction and every unclear passage as a Word comment on exactly the words it is about.
- **Check what a service sees** — select a row in the results list and choose **What is sent for this file…** to read the exact text and instructions each service receives, with the hidden names marked.
