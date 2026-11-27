# The windows

How the main window, the Check page, the Settings window and the Setup Assistant work. For
developers; what teachers see is in {doc}`using_the_app`.

## The main window and its controllers

`MainWindow` (`gui/_main_window.py`) builds the widgets and hands each topic to one controller. A
controller gets what it needs from the window as a hooks object and holds its topic's state.

| Controller | Owns |
|---|---|
| `RunStartController` (`_run_start.py`) | from Run to a started run: requirement check, the three questions, the worker; re-running one stage |
| `RunController`, `RunSession` (`_run_controller.py`, `_run_session.py`) | the worker, progress bar and time left, the two-step Stop (`StopStage`), what a finished file or run does to the window |
| `CheckController` (`_check_controller.py`) | the Check tab: opening a document, storing an edit, the lock, the tab labels |
| `ReadinessController` (`_readiness_controller.py`) | the line above Run |
| `StageGuard` (`_stage_guard.py`) | the requirement check after a step is ticked |
| `SettingsLauncher`, `SetupLauncher` | opening the Settings window and the Setup Assistant, applying their result |
| `PrivacyViewController` (`_privacy_view_controller.py`) | hiding names in the Check tab; the *What is sent* report |
| `ExportController` (`_export_controller.py`) | Export and Copy |
| `MenuBar` (`_menu_bar.py`) | the menus |

Only a controller or the teacher switches tabs. A controller never reaches into another's widgets;
it uses the hooks.

## Settings window

One modal window (`gui/settings/_window.py:SettingsWindow`) holds everything set up once; the main
window holds what changes per batch.

- **Pages** (`SettingsPage`): General (`keep_awake`, `check_for_updates`), AI models, This computer
  (computer profile, local server) and Online services (API keys) — the last two indented under AI
  models (`SUB_PAGES`) — then Scan quality, Privacy and Add-ons.
- **Entry point:** `SettingsLauncher.open(page, focus=..., preselect=...)`.
- **OK** applies the result once (`SettingsLauncher.apply`): update the panel, the anonymizer rules
  and the hide-names setting, save, check the services, refresh the queue.
- **Restore defaults › All settings** resets and saves, including the hide-names view and the
  remembered "don't ask again" answers.

### Page protocol

`gui/settings/_page.py:SettingsPageBase`. A page shows a set of settings and never talks to another
page.

| Method | Meaning |
|---|---|
| `load(values)` / `values()` | show / return **only the keys the page owns** |
| `owned_keys()` | those keys; "Restore defaults › This page" resets exactly them (`restore_defaults()`) |
| `apply()` | on OK: save what lives outside the settings dict (API keys, remembered answers) |
| `is_valid()` / `validity_changed` | OK is disabled while a page cannot be saved (e.g. an invalid regex) |
| `focus(focus, preselect)` | deep link: bring a part into view (`FOCUS_*`), tick entries |
| `on_shown()` / `on_packages_changed()` | refresh when shown / after an install |
| `set_hardware_setting()` / `set_ollama_url()` | follow what *This computer* changed |
| `close_blocker()` / `shutdown()` | why the window cannot close now / stop background work |

**A new setting** goes on the page that shows it: the widget, and its key in `load()`, `values()`
and `owned_keys()`. A test checks that no key is owned twice. Main-window keys (languages, steps,
Results) stay in `ConfigPanel`.

### How the window holds together

- **Lazy pages.** A page starts background work (model lists, hardware, installed packages), so it
  is built when first shown. `SettingsWindow.values()` merges only built pages; an unvisited page
  changes nothing. Values other pages change meanwhile (profile, server address) live in the
  window's working copy, so a later page starts from them.
- **Shared state is wired by the window**, not the pages: *This computer*'s profile feeds the
  read-only profile lines of AI models and Add-ons; its server address (on `editingFinished`) feeds
  the model lists; a finished install calls `on_packages_changed()` on every built page and is
  re-emitted as `SettingsWindow.packages_changed`; choosing a recognition method applies its
  default image preparation.
- **Deep links** between pages ("Install…", "Download a model…", "change…") are
  `navigate_requested` signals that the window turns into `show_page(page, focus, preselect)`.

### API keys

- One `ApiKeyDrafts` (`gui/settings/_credentials.py`), owned by the window and passed as
  `SettingsContext.key_drafts`. The key row under the AI service and the *Online services* list are
  both views of it (`ApiKeyRow`).
- A key is held by the app as soon as it is committed (on `editingFinished` and shortly after the
  last keystroke), because providers ask `ApiKeys` for it when listing models — that list is the
  check that the key works.
- It is stored only on OK (`apply()`, which reports keys the keychain refused). Every other exit
  (Cancel, Esc, close, Restore › All settings) calls `discard()`, after the pages' `shutdown()`.
- `ApiKeys` (`application/api_keys.py`) is the only place a key is kept. The environment is only
  read: an exported key counts, and the app writes none.
- **Storage:** `infrastructure/credential_store.py` uses the system's secret store (Keychain,
  Credential Manager, Secret Service) where there is one, else the plain-text `credentials/` group
  of the settings file. It and `gui/_qsettings.py` both locate the file via
  `config.gui_store_location()`. Built once in `application/composition.py`. Keys load at start
  (`ApiKeys.load_stored`); a run that uses nothing online never touches the keychain. A key in the
  settings file moves to the secret store at the next start (`CredentialStore.migrate`); the
  uninstaller deletes all (`ApiKeys.forget_all`).

### AI models page

- One proofreading/review block. There is no "different review model" setting: the box starts
  ticked when the *effective* pairs differ (`review_has_own_model`; empty model = task default).
  Unticked, `values()` writes the proofreading choice into the review keys. The review section
  loads, and asks a service, only while ticked.
- The context window row (`proofreading_context`) shows only while `context_choice(...).applies`
  and after the computer has been checked.
- Choosing an online service while names would be sent as written ticks "Remove personal info":
  `privacy_line_state` decides the line; a switch from local to online sets
  `SettingsResult.turn_on_anonymization`. The stage guard and the confirmation before sending stay
  the authority.

### Add-ons page

- Fills the window (`SettingsPageBase.fills_window`): the model tree takes the free height and the
  action row stays visible at the bottom.
- One **status line** above it, fed by `InstallRound`'s signals, never by log text: the running
  step with its number, a percentage for a model pull, the elapsed time for pip (which reports no
  progress through a pipe), then the result, a pending restart, or what detection found.
- The log is behind "Show details", opens itself on a failure; "Copy" copies it. Abort appears only
  while it can stop something (`InstallRound.can_abort()`): a download, not a pip install.

### Closing

`done()` calls `shutdown()` on every built page: a model fetch is cancelled; a long job is held by
`LongJobThread` until it ends. A pip install or language-model download blocks closing
(`close_blocker()`) until the package is importable; an Ollama pull belongs to the main window's
`PullManager` and continues. Dialogs opened from the window take it (or its page) as parent, so
they cannot open behind it.

## Main window: steps, readiness line, fix targets, tools

### Steps

`ConfigPanel` has four steps: Documents, Language (with Level), What should happen, Results.

- Under recognition, personal-info removal, proofreading and review sits a `UsesLine`
  (`_data_flow_summary.py`) with the step's model and place. Text from
  `DataFlowService.describe_steps(config)` — every step as if ticked, so an unticked step keeps its
  grey line — and `short_place` / `uses_line`, which share words with the AI models page
  (`PLACE_*`).
- The model is not part of `DataFlow` or its fingerprint, so a "don't ask again" survives a model
  change.
- The data-flow box (`DataFlowSummary`) shows the verdict and advice for the ticked steps of the
  same answer (`ticked_steps`), so places are asked once per refresh.
- The review is a child of proofreading: `ConfigPanel.review_will_run()` (ticked **and**
  proofreading ticked) is what `to_config()` / `lookup_config()` pass as `evaluation_enabled`, so
  nothing counts a review the pipeline would drop (`StageBuilder.build_evaluation`). The tick itself
  is saved.

### Requirements and their fixes

- `check_stage_requirements(config, stage)` (stages `ocr`, `anonymizer`, `correction`,
  `evaluation`) returns `RequirementIssue`s with an `IssueKind`, never only a sentence.
- `check_run_preflight(config, for_run=..., recognition=..., fresh=...)` runs `ocr` first and
  exposes the first blocker as `RunPreflight.blocker -> (stage, issue)`.
  - `recognition=False` skips the engine check for a run that reads no page
    (`needs_text_recognition(files, recognised=...)`: only text files or stored text; a PDF counts
    as reading). Given `files`, the preflight adds documents with stored recognised text
    (`DocumentQueries.recognised_files()`), so the CLI answers like the window.
  - `for_run=False` is the bare readiness question: no memory check, no wait for Ollama, no output
    folder check.
  - `fresh=True` asks Ollama for its models again ("Check again", a returning window).
- Engine probes (`AddonService.is_module_importable`, `ReaderService.reader_installed`) are shared
  with the connection check, so installs from Add-ons are seen without a restart.
- **A new requirement** = an `IssueKind`, its check, and **one row in
  `gui/_fix_targets.py:fix_for`**: the readiness-line text and buttons (`FixAction`: a Settings page
  + focus + preselect, or a `command` such as `retry` / `choose_folder`). `DependencyGuardDialog`
  (the guard when a step is ticked, and the dialog that stops Run) and the readiness line take
  their buttons from that table. Preselect entries are `InstallItem`s (`domain/install_item.py`).

### Readiness line

`gui/_readiness_line.py`. `readiness(...)` is pure: the first matching row wins (blocker, Results
problem, no documents, no files, nothing selected, "Checking…", ready).

- `ReadinessController.update()` is instant (last answer + queue, selection, folder) and runs on
  every selection change.
- The answer comes from `start_check()`: a background `check_run_preflight(..., memory=False)` in
  a `LatestTask`, started by a debounced settings change (`data_flow_changed`), Settings OK /
  restore, a finished install or download, start-up, window activation while blocked, a queue
  refresh (the files decide whether an engine is needed), and periodically while Ollama is
  unreachable. "Check again" and activation pass `fresh=True`.
- `ConfigPanel.lookup_config()` is recursive like `to_config()`, so line and Run agree.
- The line never disables Run; Run checks again (`RunStartController`). A model download takes the
  line over (`show_download`) and hands it back with a new check.

### Tools menu

- **System Monitor** (`gui/_system_monitor.py:SystemMonitorWindow`) is built at start-up and kept
  hidden so the chart collects every run; `SystemStatsWidget` polls only while shown.
- **Benchmark…** builds `build_benchmark_window()` in-process (lazy import; a hidden import in the
  packaged build). It is kept between uses; showing it again calls `reload_settings()`. A user
  close during a run asks first (`event.spontaneous()`); the app quitting does not.
- Both are **parentless** windows (an owned window stays on top on Windows), so they can sit next
  to the main window. `MainWindow.closeEvent` closes them; for Benchmark only `close()` runs the
  `closeEvent` that stops its worker.

## The window tabs and the Check page

Tab 0 is *Documents* (workflow panel, results list), tab 1 *Check*
(`gui/check/_page.py:CheckPage`). *Check* is disabled while no document has a result
(`ResultsTable.checkable_source_ids()`). Labels carry state: *Documents · 12 of 28*,
*Check — Anna_Mueller.pdf*.

### One query feeds the page

`DocumentQueries.check_document(config, source)` (`application/check_document.py`) returns a
`CheckDocument`: both texts and their plain forms, the aligned review, the numbered items, the scan
pages, how results were sent (`sent`) and the preparation (`preprocess`). `CheckController` asks
for it once per document (`load`) and once per stored edit and never assembles that state itself.
Alignment is memoised (`_align_cached`). The queue's *sent* cells come from the same read.

### Showing a document again

- `CheckBody.show_document` refills the scan and texts, also for the open document (a run may have
  read it again).
- Only the store's answer to a typed transcription passes `typed=True`, which keeps the text being
  typed and the scan as they are.
- A stored edit of the final text goes through `update_document`, which never refills a text.
- The list is rebuilt silently: `SidePanel` reports a choice only when the teacher made it.
- A typed edit waiting for its pause is stored **before** a stage run invalidates the cache
  (`RunStartController.start_stage`).
- A zero-width edit (words put back where the model deleted them) is placed with
  `PlainMap.insertion()`, which does not snap over a paragraph break.

### Three views, two documents

`gui/check/_text_views.py`: `ReadView` (*What was read*, editable), `FinalView` (*Final text*,
editable), `MarkedView` (*With corrections*, read-only), in a `QStackedWidget` of `CheckBody`.
*Final text* is loaded whenever a proofreading exists, even when not in front: every card action
edits its `QTextDocument` and goes through the normal commit path; *With corrections* is rebuilt
from the stored result after each commit.

### Marks are overlays

In the editable views a mark is a `QTextEdit.ExtraSelection`, so `document_from_qt` stores the same
Markdown with or without marks and typing never writes a mark into the text. Only `MarkedView` has
its own formatting (student's words struck through in grey, taken-out words in red), never
converted back. A commit that changes nothing stores nothing.

### Positions come from `PlainMap`

`gui/check/_plain_map.py:PlainMap` walks a `QTextDocument` like `document_from_qt` and records
`(qt_start, qt_end, plain_start)` per text run; `position()`, `offset()` and `cursor()` are
bisects.

- Invariant, tested on every Markdown fixture: `PlainMap.text == TextFormatter.plain_text(stored)`.
- List markers and table ` | ` / `\n` are *virtual*: a plain range without a Qt range; an offset
  inside snaps to the next run.
- Where a document breaks the invariant, the view shows it without marks and says so — a wrong mark
  is worse than none.

### Every action is a text edit in plain offsets

`domain/proofreading/text_edits.py` holds `TextEdit(start, end, text)`; `domain/teacher_work.py`
holds the builders of card actions (`take_out`, `put_back`, `use_suggestion`,
`suggestion_is_wording`); tests use `apply_edits`.
`FinalView.apply_plain_edits()` maps them through `PlainMap` and applies them last to first in one
edit block, so one Ctrl+Z undoes one action. Whitespace is one function (`fit_gap`): a
replacement keeps the separators, an insertion gets one space towards a neighbouring word, a
removal takes one adjacent space, and words never join across a block boundary.

### Undo

- `CheckBody` owns Undo/Redo `QAction`s (`WidgetWithChildrenShortcut`) that call the *Final text*
  document's `undo()` / `redo()` and store at once (`flush`), so they work from the list and the
  card too.
- An editable text view keeps the keys for itself (Qt's shortcut override): Ctrl+Z in *What was
  read* undoes the transcription.
- After a step the panel chooses the item at the cursor (`PlainMap.offset`).
- Disabled while *Final text* is read-only (lock, *Hide names*) or has nothing to undo. The history
  ends when the text is refilled (another document, *Hide names*, a reading fix, a reload after a
  run); a stored edit coming back (`update_document`) keeps it.
- There is no app-level undo stack and no approval per correction: every correction is applied; the
  teacher takes out or changes what is wrong.

### Where an edit goes

`CheckController` calls `edit_transcription` or `edit_corrected_text` on `DocumentCommands`, which
writes the teacher's work and returns the merged view; the queue row is filled from it. An edit
never writes the cache, so the machine result stays what the models produced.

### *Checked*

A checkable button in the Check header (`CheckPage.checked_toggled`, `refresh_checked()`):
*Mark as checked*, *✓ Checked* (with a proofreading), *✓ Text checked* (without).

- It marks what the document has now: the corrections (bound, so a new proofreading starts
  unchecked), else the transcription, stored as a copy.
- Its own undo (not Ctrl+Z); a later edit does not uncheck it; disabled under the lock; allowed with
  *Hide names* on.
- A typed edit is stored first (`flush_pending_edit`); a press that cannot be stored shows a notice
  and resets the button.
- It does not advance — ▶ does. The queue's *Checked* column shows the state; right-click →
  *Select checked* + *Export* is the "checked only" export.

### The side panel

`gui/check/_side_panel.py`: two tabs, a filter row, a card.

- **Tabs:** *Corrections · Content* (`show_corrections()` / `show_content()`, chosen by
  `CheckPage.set_view`). `setTabBarAutoHide(True)` hides the bar when there is no review.
- **Filter row:** a `FlowLayout` (`gui/check/_flow.py`) with one `QCheckBox` per category plus
  *Unclear* and *Not proofread*, each with its count; zero counts are hidden. A taken-out
  correction and a change of the teacher's own answer to no box. Nothing ticked = all
  rows; ticked boxes combine; *Not looked at* narrows the rest. `set_filter()` only re-applies row
  visibility and keeps the chosen row if visible, else chooses the first visible one.
- **Card:** a fixed-height `QScrollArea` plus a fixed button row, so long explanations scroll and
  the buttons stay put. Its HTML (`_body_html()`): the bold `before → after`, the sentence with the
  student's words highlighted (`_context_html()`, masked like the title), the category chip,
  *optional* / *AI not sure* tags (`_tags_of()`), then the explanation.
- **Buttons:** *Take out* / *Put back*, *Use suggestion*, the replacement field and *OK*. A model's
  spelling or punctuation change also carries *Take out all N* (`minor_corrections()`), which
  excludes taken-out and own changes.
- **List rows:** `_ItemDelegate` draws a bar in the kind's accent at the left edge, a fixed dot
  column (`_bullet_of()`, `todo_mark()`), two word columns (`_column_rects()`) and a neutral chip. A
  row not looked at yet is bold with a *•*; a looked-at row is grey and has none; an own change is
  neither. The columns are laid out in the regular font, so a row does not move when its weight
  does. A spelling or punctuation row has the paler accent, as its mark in the text. The chip is the
  kind where the item has none of its own (*unclear*, *not proofread*, *your change*, *taken out*),
  else the category (*other* if there is none).
- **Legend:** the info button between the counts and *Next* — `_legend_html()`, built from
  `MARK_ACCENTS` and `texts/check.py` — is its tooltip and what a click shows.
- **Colours:** `gui/check/_style.py` says what happened to the text, the words say the category:
  `MARK_COLOURS` (a mark's background and text), `MARK_ACCENTS` (its underline, the row's bar, the
  legend's swatch; a minor mark takes `pale()` of it), and one neutral `CHIP_BACKGROUND` /
  `CHIP_TEXT` for every chip. The app always uses the dark theme: dark tints with light text, the chosen item the one bright background.

### Looked at and *Next*

- `SidePanel.item_shown` fires when an item becomes the chosen one (list click, text click, first
  item on open); the window calls `mark_looked_at` once per new key, without a reload.
- Keys: `edit:<id>` (also when taken out), `unclear:<n>`, `own:<start>-<end>` (position in the
  student's text, so other changes do not renumber them). A counting row not looked at carries a
  *•* and is bold; a looked-at row is grey without one, and an own change has none at all. The
  footer ("8 of 12 looked at") comes from `looked_at_progress`. Own changes do not count.
  Looked-at is never undone.
- *Next* (`SidePanel.choose_next`) chooses the next counting row in list order, wrapping; a document
  opens at the first. With a filter it stays inside it. `counts_for_looked_at`, `next_unseen` and
  `first_unseen` (`application/check_items.py`) are pure and keyed, so list and footer agree. A card
  action does not move the choice.

### The strip above the texts

One `_Banner` (`_refresh_banner`) shows the most important reason: the lock, a failed hide, what a
reading fix took (the `ReadingFix` that `edit_transcription` returns on its view; it stays until
the document changes or a proofreading is back), the reading fix, then `doc.warning`. Nothing that toggles with 👁 is in it,
because a strip that comes and goes moves the text under the cursor. *Edit anyway* of the reading fix
is greyed out while names are hidden. A failed hide is the exception: names show while the toggle
says hidden, so the strip says so and offers to show them.

Other strips (`gui/check/_run_strip.py`): `NextStepStrip` over an unproofread transcription
(*Proofread*, or *Set up proofreading…* via `set_proofreading_ready`), and `RunStrip` while the
open document is in a run (`CheckController.refresh_run`). A run started from the Check header
keeps the tab (`RunSession.from_check`).

### The questions

Three, in `texts/dialogs.py`, each one dialog for the whole selection:

- **Run start** (`RunStartController`, after the data-flow question): documents whose proofreading
  is stale and whose corrections were worked on, plus those whose corrections belong to no stored
  proofreading. *Keep my changes* (default) writes `kept_under` and runs; *Proofread again*
  invalidates first; *Cancel* starts nothing. With only the second list: *Continue* / *Cancel*. Any
  answer but *Cancel* drops the unbound corrections (`drop_unbound_corrections`). The lists come
  from `worked_on_documents`, computed in the preflight's background task (files are re-hashed only
  when size, time or identity changed), and only when starting a run (`ask_about_work`).
- **Re-run Proofreading**: worked-on documents of the selection, before the cache is touched.
- **Re-run Text Recognition**: documents with a transcription part or worked-on corrections.

All fail closed: if the question cannot be built, nothing re-runs. A re-run drops the work before
resetting the cache; if the work cannot be dropped, nothing is reset.

A run with changed settings keeps worked-on corrections and says so in the log file and on the
command line; the queue shows that proofreading as done, noted *kept*, with a tooltip.

### The lock

A document the run will redo is read-only from the run's start until its result arrives
(`CheckController.apply_lock`, from `RunSession`), so no edit races the run. The banner shows
*Waiting for this run*, then *Being proofread — you can edit it when it is done*. Finished and
untouched documents stay editable. `set_lock` calls `flush_pending_edit()` first.

### Tabs, selection and keys

- **Only the teacher or a run start switches tabs.** `CheckController.load` never switches;
  `open_document` and `show_check` do. A finished file reloads the open document and leaves the
  tab; `RunController.start` goes to *Documents* because progress lives there. A run that redoes
  the open document locks it and reloads it when done (or when the run ends early).
- **Edits go to the open document**, never the selected row. Opening from the queue
  (`check_requested`) selects its row; clicking the *Check* tab does not.
- **Clicking *Check* with nothing open** opens the current row if it has a result, else the first
  that has one (`show_check`). A rebuilt queue without the open document closes *Check*
  (`sync_with_queue`).
- **Keys:** Esc leaves *Check* unless focus is in a text field, line edit, editable combo or popup
  (`_escape_is_for_the_focus()`). Ctrl+1 / Ctrl+2 are window `QShortcut`s (Ctrl+2 only while
  *Check* is enabled). The header arrows step through `checkable_source_ids()` and select the row.
- **Copy:** `Copy ▾` takes the row's text through `ExportService.text_for_copy()`, the same as
  *Export text*, after `flush_pending_edit()`. The "names hidden" entries follow
  `PrivacyViewController.refresh_availability()`. *Save* entries go through `ExportController`.

### Focus and clicks

Choosing an item (its words or its row) marks it in both. The text uses extra selections
(`CheckTextView._apply_marks`), so a click neither re-renders nor loses the scroll position.
`reveal()` scrolls to the item and puts the caret on its first word without a selection (which
would paint the theme's blue over the mark). A click is resolved with `cursorForPosition()` →
`PlainMap.offset()` → the innermost item containing it. Tests click with `qtbot.mouseClick` at a
position found that way, not by emitting signals.

### Scan pages

- **Prepared pages:** `PreviewImageStore.persist_page_images()` writes page 1 as
  `<stem>_preprocessed.png` (`preview_img`) and pages 2..N as `<stem>_p<N>.png`, listed in
  `DocumentState.page_images`. The stem is `document_stem(file)`, so same-named files in different
  folders keep their own pages. `prune_page_images()` removes pages a shorter document no longer
  has; `cleanup_intermediate_images()` treats them as intermediate.
- **Original pages**, when the preparation changed a page: `persist_original_images()` writes
  `<stem>_original.<ext>` (`original_img`, read by Benchmark and the Word builder) and
  `<stem>_o<N>.<ext>`, listed in `DocumentState.original_images`. `prune_original_images()` matches
  exact names, so a document called `Anna_o2` keeps its files.
- `DocumentState.load_valid()` drops the list when a file is missing: a deleted cache costs a
  re-run, never a broken page.
- **Text-layer PDFs** store every page too: `PdfInputProvider` renders skipped pages into
  `InputPayload.page_images` at `pdf_render_dpi`, and `FileProcessor` persists them like prepared
  pages.
- **ScanPane** (`gui/check/_scan_view.py`) holds the *Prepared · Original* switch (only when a page
  has an original and a step acted on it) and the caption (`prepared_caption`). Opens fitted to the
  page; a double click toggles fit page / fit width.
- **ScanView** draws each page from its own image, scaled down (`setScale`), so zooming in shows the
  scanned pixels, up to `_ZOOM_NATIVE` / `_ZOOM_MAX`. Only pages in view and their neighbours are
  held at full size (`_pages_in_view`); others are thumbnails (`_THUMBNAIL_WIDTH`), swapped by
  `_refresh_full_pages` on scroll or zoom.

## Setup Assistant

`gui/setup/` is a `QWizard` that asks what only the teacher knows and writes the settings once. It
opens on a first start (no settings file), once after an update (`SettingsFile` keeps the
last-run version under `gui/app_version`) and from **Tools → Setup Assistant…**.
`SetupLauncher.open(first_start=...)` builds the `SetupContext` (panel settings, application
service, the main window's `PullManager`, system language on a first start) and applies what
`SetupAssistant.committed` returns.

### Page order

`gui/setup/_flow.py`: `ORDER` lists the seven pages (Welcome, Your classes, The AI on this
computer, Proofreading, Names and personal information, Ready to set up, All set);
`next_page(page, answers)` picks the next, so a page never decides where to go.

- With typed texts the reading model is not asked for.
- The Privacy page is always shown; `SetupAnswers.privacy_case` decides its shape (online or
  another PC: *Remove personal info*; this computer: only *Hide names in this app*, returned as
  `SetupPlan.display_anonymized`, never `False`).
- Anything expensive (Ollama status, profile, online section, model list, plan) is built when its
  page is first shown.
- Every page sits in a scroll area (`SetupPage._content()`), since QWizard gives all pages one
  height; `fit_to_screen()` keeps wizard and Settings on screen. The wizard re-applies
  `ModernStyle` on show (Windows swaps in Aero; the app also sets the Fusion style there, which has
  none).
- A found server is a `ServerCard` (`gui/_server_card.py`), shared with Settings and the search
  dialog. The proofreading model list comes from `plan_with_model_choices()`; an empty
  `SetupAnswers.local_model` means *the recommended one*. `extra_names_model` (the Privacy page's
  extra language, suggested as the computer's own because the students' names are in it whatever
  language the text has) becomes `anonymizer_secondary_model` and joins the downloads.

### The plan is a pure function

`application/setup_plan.py`: `build_setup_plan(answers, current, facts, rate=, best=,
default_model=)` takes `SetupAnswers` (what the pages collected) and `SetupFacts` (what
`SetupService.setup_facts()` measured: profile, memory, card, Ollama, installed tags, free disk,
anonymization state, missing package) and returns a `SetupPlan`: Settings-window keys, main-window
keys, one `PlannedStep` per summary row, `PlannedDownload`s, the review-model choice, a
`DiskVerdict`, and `needed_gb` / `free_disk_gb`.

- **A working choice is kept:** an installed model that fits is not replaced, so running the
  assistant again changes nothing. A chosen built-in reader is kept too; if its files are missing
  `build_setup_plan` adds a `DownloadKind.READER_MODEL` download. The assistant never proposes the reader.
- **Nothing is turned off:** "Remove personal info" is only set to `True`; a review with its own
  model keeps it.
- **Only existing keys:** the assistant writes keys the Settings window or main window already own,
  so `Config`, `DEFAULTS`, the TOML keys and the CLI are untouched; no migration.
- **Page models** (`DOC_ORI` / `UVDOC`) serve *Automatic* preparation for every engine and are
  planned separately: with handwritten pages, `preprocess_method == "auto"` and
  `page_models_installed` false, `build_setup_plan` adds a `DownloadKind.PAGE_MODELS` download. The assistant
  never changes `preprocess_method`.

### Summary and downloads

- `gui/setup/_summary.py:summary_rows(plan, place_of, key_missing=)` turns a plan into
  `SummaryRow`s (classes, then one per step with model, place and downloads), testable without a
  window. `SummaryRows` draws one ticked checkbox per download; only ticked ones reach the round.
- **Set up** calls `SetupAssistant.commit(downloads)`: stores typed keys (`store_key_drafts`), emits
  `committed` with a `SetupResult(settings, panel)` and starts an `InstallRound`
  (`gui/_install_round.py`, shared with Add-ons) with one list per kind; the reader and page models
  run after spaCy. An Ollama tag with `possible == False` (Ollama not running) is left out; the main
  window offers it once the server answers.
- The Done page follows the round and the pull manager, and offers the speed test on the resulting
  settings.

### Applying and closing

- **Nothing starts by itself:** downloads need **Set up**; the network search, **Start Ollama** and
  **Test speed** each need their own click; the server address changes only by the teacher's
  choice.
- The launcher applies Settings keys with `update_settings` and main-window keys with
  `load_from_dict` (which skips the stage guard, since models are still downloading), then saves,
  checks the services and refreshes the queue.
- **Cancel** changes nothing; on a first start it keeps `_apply_first_start_defaults` (system
  language, best models for this computer).
- **Select documents…** closes the wizard, then the main window asks for the folder
  (`documents_wanted()`), so the dialog does not open behind the wizard.
- `done()` refuses while the round is busy; otherwise it calls `shutdown()` on every page. Cancel
  restores the typed keys (`discard_key_drafts`) and forgets model lists fetched with them.
- `teachers_teammate.gui.setup` is a hidden import in the packaged build (lazy import).

### Tests

**The walkthrough** (`tests/gui/test_gui_setup_walkthrough.py`) runs the real `SetupAssistant`,
`SetupService`, `LatestTask` pool, `InstallRound`, `PullManager`, `ollama` client over loopback,
`MainWindow` and `ocr.toml`. Replaced at the narrowest seam:

- Ollama by `tests/fakes/fake_ollama.py:FakeOllama` (one loopback port);
- pip, spaCy, the reader and page models by recording stubs;
- the model store by one under `tmp_path`; the computer by `hardware_profile = "16gb"`, fixed disk
  and memory;
- `SetupAssistant.exec` by a function that shows, scripts and closes the wizard without a nested
  modal loop, so failures are reported.

Expected tags come from `best_model(...)`, never literals. `FakeOllama` is the only Ollama server in
the tests; extend it with parameters. `tests/unit/test_fake_ollama.py` drives it with the real
client and its answers follow Ollama's source (e.g. `modified_at`, `details.format`, a failing pull
that streams `pulling manifest` before the error, all in a 200 stream). No test runs a real
`ollama serve`.

**The packaged build** checks the same on all three systems: the `setup assistant` check in
`frozen_modes.py` clicks through to the summary and, frozen only, asserts the bundled page models
are seen (no `PAGE_MODELS` download). It points at `http://127.0.0.1:9` and uses a temporary
folder.

## Hiding names in the Check tab

`domain/anonymization.py` is pure (no spaCy, Qt or I/O) and holds both directions of the
placeholder map:

- `restore(text, mapping)` — placeholder → original; used on the proofreader's answer.
  `SpacyAnonymizer.restore` delegates to it.
- `mask(text, mapping)` — original → placeholder, for showing other views. Longest original first
  (one regex alternation, so "Anna Müller" is not cut by "Anna"), and only as a whole word
  (`(?<!\w)` / `(?!\w)` only where the value starts or ends with a word character). A name the
  proofreader respelled is not found — a known limitation.

`gui/check/_masking.py:Masking` holds the Check tab's state: mapping, readiness, failure, and the
text they belong to. The page keeps texts *unmasked* in its `CheckDocument` and asks
`Masking.mask(text)` for what it shows, so toggling needs no reload.

- **The 👁 toggle alone** says whether names are hidden; no strip does. Until placeholders arrive
  (`Masking.working`) it reads *👁…* with a "Hiding personal information…" tooltip.
- **Why the text cannot be typed into** is a tooltip at the caret on a keystroke
  (`CheckTextView.keyPressEvent`) and on the card's buttons (`txt.SHOW_NAMES_TO_CHANGE`).
- **A failed hide** gets a strip; its button emits `display_anonymized_changed(False)`, the same
  route as the **Hide names** box.
- **Placeholders** are chips (`gui/_formatted_text.py:chip_placeholders()`, skipping markup).
  Hovering one in *What was read* shows the original from the map; the other views name nothing.
- **Marks** move to the masked text with `Masking.carry(span, text)` via the `OffsetMap` of
  `mask_with_offsets()`, the same map the notes use.
- **Requests:** `Masking.needs_request(raw_text)`, once per text; `PrivacyViewController` answers
  with a background `AnonymizationService.anonymize_text()`, sharing the run's cache. A result for a
  text no longer shown is dropped; `forget_anonymization()` runs when anonymizer settings change.

**The setting** is a GUI preference, not part of `Config`. The checkbox (*Settings → Privacy →
Personal info: detection*) and the eye button both call
`PrivacyViewController.apply_display_anonymized`, which stores `privacy/display_anonymized`
(`gui/_privacy_settings.py`) and calls `CheckPage.set_display_anonymized()`. Default **on**
(`DISPLAY_ANONYMIZED_DEFAULT`). The checkbox is enabled only while
`AnonymizationService.anonymizer_available(language)` (spaCy and the language's model installed),
cached per language and refreshed on `data_flow_changed` and after an install.

### What the box of *What was read* holds

`application/check_document.py:sent_view_of()` is the one rule behind the box, and it answers with a
`SentView`: the text, the placeholders inside it, the map behind them, why (`SentKind`), and whether
the text has words the student crossed out.

| Situation | `SentKind` | The box holds |
|---|---|---|
| proofreading sent with names hidden | `SENT` | `correction_sent_text` as stored, or the transcription when nothing was found |
| that proofreading, with the names shown again | `NAMES_SHOWN` | the transcription |
| proofreading sent as written | `AS_WRITTEN` | the transcription |
| no proofreading, names hidden, answer arrived | `WILL_BE_SENT` | the anonymizer's text of the transcription |
| no proofreading, answer still out or failed | `WORKING` | the transcription |
| no proofreading, names shown | `NAMES_SHOWN` | the transcription |
| no add-on | `UNAVAILABLE` | the transcription |

The sent text is the string the request was made from, so a name the model spelled differently is
hidden there while `mask()` cannot find it in the transcription. *Final text*, *With corrections*
and the side panel keep `mask()`: they show the corrected text, which was restored and has no sent
string.

`CheckBody._sent_caption` words the line above the box from `texts/check.py`, and adds that
crossed-out words stay on this computer when the sent text has them.

A `SENT`, `WILL_BE_SENT` or `WORKING` box is read-only: typing there would store placeholders in
what the teacher writes. `AS_WRITTEN` holds the transcription itself, so it stays editable once the
teacher accepts a reading fix.

## Remembered "don't ask again" answers

`gui/_privacy_settings.py:FlowAcknowledgements` alone reads and writes them (QSettings
`privacy/acknowledged_data_flows`, one fingerprint per line). The pre-run question, the list in
Settings → Privacy and "Ask again" all use it. `texts/privacy.py:describe_fingerprint()` words a
line when it is read, so no wording is stored.

The fingerprint is versioned (`v2|…`, with the data region); another version does not match and
the user is asked again. Parts are separated by `|`, fields by `:`; the host may contain a colon
and is always the second-to-last field. `PrivacyPage` (`gui/settings/_privacy_page.py`) shows them
in its first tab (`SendingDataTab`); changes apply on OK.

## "What is sent for this file"

`application/sent_text.py` answers *which text does the service read?* without spaCy, a model or a
widget:

- `describe_sent_texts(flow, *, raw_text, correction_text, anonymize, preview_extracted,
  correction, evaluation_prompt)` walks `DataFlow.outbound` and returns a `SentText` per step:
  proofreading gets the extracted text, content review the proofread text, each anonymized when
  the step hides names. Without `anonymize`, the text is reported as written.
- An internal memo anonymizes each distinct text once, so all sections share one spaCy pass, and
  the shared anonymization cache answers repeats.
- `preview_extracted` (set from `config.anonymization_enabled`) adds the anonymized extracted text
  for the comparison tab.
- `DocumentQueries.describe_sent_texts()` supplies `anonymize` (`None` when removal is off) and
  fills `correction` / `evaluation_prompt` with the stages' own code (`_sent_correction`,
  `_sent_evaluation_prompt`), so the dialog cannot drift from a run. A `SentText` carries the
  requests as data; `texts/review.py:prompts_block()` words them. `SentTexts` adds `flow.level`,
  the raw and anonymized texts, and `stored` (the file's `DocumentStateView.sent`) to compare with
  what was already sent.

`gui/_what_is_sent_dialog.py` only presents it: one `_SentSection` per step (title, badge, names
note, text with highlighted placeholders, collapsed instructions), a *Personal information* tab
(`DiffWidget`), and for a local run "Everything stays on this computer." `PrivacyViewController`
computes the report in the background behind a small waiting dialog and shows it modally.

## Cell tooltips in the results table

`data_flow_changed` (from `refresh_data_flow()`, which caches the `DataFlow`) and the main window
push `{stage.stage: stage_tooltip(stage)}` into `ResultsTable.set_stage_tooltips()`.
`stage_tooltip()` (`gui/_data_flow_summary.py`) words one line, e.g. *Sent to Mistral (EU), names
hidden*. A cell's own message (count, warning, error) lives in `_ROLE_NOTE` via `_set_note()`;
`_compose_tooltip()` joins the stage line, how the stored result was sent (`sent_lines()`, omitted
while the stage is being redone) and the note. `set_stage_tooltips()` redraws every row.

## Background tasks in the GUI

Two kinds, one module each.

**A query or fetch** (no window waits, newest answer wins): `gui/_background.py`.

```python
self._task = LatestTask(owner=self)            # "latest wins" per widget
self._task.submit(lambda: service.slow_call(), self._on_done)   # on_done(result, error)
run_streaming(fetch, on_item, on_done, owner=self)              # items as they arrive
```

- Runs on a shared `QThreadPool`; results arrive on the GUI thread via a queued signal. Results for
  a destroyed `owner` or a superseded task are dropped. No thread object, so closing a dialog
  mid-task cannot abort the process.
- `network=True` (model lists, update check, server search) uses a second pool: such a task cannot
  be interrupted and must not block local tasks.
- The main window waits a bounded moment on close (`wait_for_background_tasks`). In GUI tests,
  `neutralize_gui_threads` keeps smoke tests offline.

**A long job** (pip install, model download, Ollama pull: reports progress, must outlive its
window, stops only between chunks): subclass `LongJobThread` (`gui/_long_job.py`) and implement
`work()`:

```python
class _PipInstallThread(LongJobThread):
    def work(self) -> None:                     # the result travels with finished_ok
        self._svc.addons.install_packages(self._packages, self.log_line.emit)
```

- The base provides `log_line`, `progress`, `finished_ok`, `finished_err`, a stop event and
  `stop()`. A stopped job raises `ModelDownloadCancelledError` and reports `ABORTED`.
- It holds every started job until it ends (destroying a running `QThread` aborts the process); the
  app waits a bounded moment on close (`wait_for_long_jobs`).

`tests/unit/test_thread_kinds.py` refuses any other `QThread` subclass, except `OCRWorker` and
`BenchmarkWorker`, which run and stop as a whole.
