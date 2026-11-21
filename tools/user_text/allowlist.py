"""The literals that still sit at their call site, per file.

Every entry is a ``(path, text)`` pair the ratchet test tolerates.  The list may only
shrink: a change that moves a sentence into ``teachers_teammate/texts`` deletes its entry,
and the test fails on an entry that no longer matches a finding, so nothing is left behind.

Written from the output of ``tools/user_text/inventory.py``.  Entries are keyed by the
literal's text rather than its line, so an unrelated edit above it does not turn the test
red; duplicates within one file collapse to a single entry.
"""

from __future__ import annotations

#: The literals still allowed at their call site, by module.
ALLOWED: dict[str, frozenset[str]] = {
    "teachers_teammate/cli.py": frozenset(
        {
            "  ! Data leaves this computer. Check that this is allowed for your documents.",
            "Also save the extracted text of each file as <name>.txt.",
            "Also save the extracted text with personal data replaced by placeholders as <name>_anonymized.txt (needs spaCy).",
            "Anonymize PII (person names, emails, phone numbers) in OCR text before sending to the correction LLM. Requires spacy and a language model: pip install 'teachers_teammate[privacy]' && python -m spacy download en_core_web_sm",
            "CEFR level the correction aims at (default: %(default)s).",
            "Context window for the content review when it runs on another Ollama model than proofreading (proofreading uses 8192): 'auto' grows it with the size of the graphics card (8192, 16384 or 32768, whichever still fits), a fixed value pins it — useful for a remote server whose card the app cannot see.",
            "Corrected text only: no explanations, no unclear list (one request instead of three).",
            "DOCX output layout: 'table' = three columns (image | OCR | correction); 'comments' = two columns (image | OCR) with diff corrections as Word comments.",
            "Disable PII anonymization before correction (default).",
            "Do not keep preprocessing temp files after the run.",
            "Do not read scans: use only text that already exists (TXT files, PDFs with a text layer) and skip images and scanned PDFs. No OCR engine or model is needed.",
            "Do not scan subfolders recursively.",
            "Enable DOCX generation (disabled by default; only cache state is written otherwise).",
            "Enable the LLM correction step (default).",
            "Enable the evaluation step.",
            "Evaluation model name; provider default is used when omitted.",
            'Explain the changes and list unclear expressions (default; overrides a config file that sets correction_output = "text").',
            "Image preparation: auto (default; looks at each page and straightens, turns and evens out photographed pages, clean scans pass unchanged) or none (the original page, with the photo's rotation tag applied).",
            "Input folder containing PDF, JPG, JPEG, PNG, or TXT files.",
            "Keep preprocessed temp files after the run (useful for inspecting OCR inputs).",
            "Keep the computer from going to sleep during the run (default).",
            "Language of the change explanations and the unclear table (preset name such as 'german'). Default: the language of the text.",
            "Language passed to the OCR vision model.",
            "Let the computer go to sleep during the run (by default it is kept awake).",
            "OCR engine: ollama (AI vision model through Ollama, default) or ppocr (the built-in reader: no server, best on printed pages; download it first with --download-reader). Online services are not offered: reading a page needs the whole page, so personal information cannot be hidden first.",
            "Ollama OCR vision model.",
            "Ollama base URL.",
            "Output folder for generated .docx files (cache state is stored separately).",
            "Path to a TOML config file. Defaults to ocr.toml in the app's settings folder (the file the app window saves) when it exists.  CLI arguments override file values.",
            "Per-image OCR timeout in seconds (Ollama only).",
            "Preprocess all input files and save the resulting images to the temp cache folder, then exit. No OCR, correction, or DOCX generation is performed.",
            "Read scans (images, PDFs without a text layer) with the OCR engine (default).",
            "Resolution for rendering PDF pages to images before OCR. 72 = screen quality, 150 = draft OCR, 300 = recommended (default), 600 = high quality. Has no effect on image inputs (JPG, PNG).",
            "Run even when the models do not fit in the memory that is free now. Without this flag such a run is refused, because the computer would have to use its disk as memory and become extremely slow.",
            "Scan subfolders recursively.",
            "Skip DOCX generation; only cache state is written.",
            "Skip the LLM correction step (DOCX is still created without column 2).",
            "Skip the evaluation step (default).",
            "System prompt used for evaluating corrected text.",
            "Teacher's Teammate — batch OCR pipeline that extracts text from PDFs/images and builds DOCX documents with grammar/spelling correction proposals.",
            "Use --ignore-memory-check to run anyway.",
            "Use a PDF's embedded text instead of OCR. auto = only when every page is clean, text-only (no scans, no handwriting, annotations or form fields) (default); always = whenever every page has any text; never = always OCR. Has no effect on image or TXT inputs.",
            "Where your documents are processed:",
            "f\"'{value}' is not an integer\"",
            "f\"Correction LLM model name. Auto-selected per provider when omitted: {default_model_for_task('ollama', 'correction')} (ollama), {default_model_for_task('openai', 'correction')} (openai), {default_model_for_task('anthropic', 'correction')} (anthropic).\"",
            'f"Download the built-in reader\'s model files once ({READER_SIZE_LABEL}, from {HUGGING_FACE_HOST}) and exit."',
            "f'  => {_LEVEL_TEXT[flow.level]}'",
            "f'  {marker} {stage.title}: {stage.badge}'",
            "f'DPI must be between {_PDF_DPI_MIN} and {_PDF_DPI_MAX}, got {dpi}'",
            "f'Download the page models of the automatic image preparation once ({PAGE_MODELS_SIZE_LABEL}, from {HUGGING_FACE_HOST}) and exit. They turn sideways pages and flatten curved ones; without them those two steps are skipped.'",
            "f'ERROR: {exc}'",
            "f'ERROR: {refusal}'",
            "f'ERROR: {warning}'",
            "f'Notes for the AI about this class or this text, sent with every proofreading request (at most {NOTES_LIMIT} characters). The instructions themselves are fixed by the app.'",
            "f'WARNING: {warning}'",
            "f'\\nERROR: {exc}'",
            "f'\\n{ready}'",
            "f'\\rDownloading {name}: {done // 1000000} of {total // 1000000} MB'",
        }
    ),
    "teachers_teammate/gui/__init__.py": frozenset(
        {
            "The window needs PySide6-Essentials, which is part of the base install. Reinstall with: pip install --force-reinstall teachers-teammate",
        }
    ),
    "teachers_teammate/gui/_addon_installer_dialog.py": frozenset(
        {
            "",
            '<span style="color:#27ae60;">✓ Model downloaded successfully.</span>',
            '<span style="color:#c0392b;">Please enter a model name.</span>',
            "Cancel",
            "Close",
            "Done ✓",
            "Download",
            "Download log will appear here…",
            "Download spaCy Model",
            "Downloaded ✓",
            "Downloading…",
            "Enter an Ollama model tag to download from the registry.<br>Examples: <tt>llama3.2:latest</tt>, <tt>mistral:7b</tt>, <tt>tinyllama</tt>",
            "Install",
            "Installation log will appear here…",
            "Installed ✓",
            "Installing…",
            "Pull Model",
            "Pull Ollama Model",
            "Pulling…",
            "Retry",
            "Select a spaCy NER model to download. The model will be used for anonymizing person names in OCR output.<br><br>The <i>Multilingual</i> model works as a secondary pass across all supported languages and is a good default choice.",
            "Starting download…",
            "e.g. llama3.2:latest",
            "f'<span style=\"color:#27ae60;\">{txt.READER_DOWNLOADED}</span>'",
            "f'<span style=\"color:#c0392b;\"><b>Error:</b> {msg}</span>'",
            "f'<span style=\"color:#c0392b;\"><b>Error:</b> {text}</span>'",
            "f'{done / 1000000:.1f} / {total / 1000000:.1f} MB'",
            "f'{self._INTRO}<br><tt>{self._service.reader.models_dir()}</tt>'",
            "f'{status} — {mb_done:.1f} / {mb_total:.1f} MB'",
        }
    ),
    "teachers_teammate/gui/_anonymizer_config_widget.py": frozenset(
        {
            "",
            "<b>Extra detection model (optional):</b>",
            "<b>Note:</b> Anonymization is a detection aid, not a guarantee. NER models and regex patterns may miss PII — always review the output before uploading documents to a cloud provider. You remain responsible for compliance with data-protection regulations.",
            "<b>Preview</b> — paste sample OCR text to test all active patterns:",
            "<b>Primary detection model:</b>",
            "Add row",
            "An extra spaCy model run after the primary one to catch names it misses (e.g. German names in English text). <i>Auto</i> uses the model for the language this computer is set to, and nothing when that is the same model as the primary one. Choose <i>(none)</i> to skip the second pass entirely.",
            "Anonymized output:",
            "Detection",
            "Paste text here, e.g.:\nAlice sent an invoice to bob@example.com (STU-123456).",
            "Patterns applied after NER (clear all rows to disable regex anonymization):",
            "Primary spaCy NER model for person-name detection.  <i>Auto</i> picks the language-specific model automatically; choose a specific model to override (e.g. force the multilingual model).",
            "Regex Patterns",
            "Remove selected",
            "Replacement map:",
            "Reset patterns to the built-in defaults (IBAN, email, phone, dates, and German street and postcode formats).",
            "Restore defaults",
            "Run preview",
            "Sample text:",
            "f\"spaCy model '{needed}' is not downloaded.\\nRun: python -m spacy download {needed}\"",
            "f\"{n} pattern{('s' if n > 1 else '')} {('have' if n > 1 else 'has')} errors — fix before saving.\"",
            "f'Could not detect installed spaCy models: {msg}'",
            "f'Invalid regex: {exc}'",
            "f'Preview failed: {exc}'",
        }
    ),
    "teachers_teammate/gui/_config_panel.py": frozenset(
        {
            "Change the explanations (their language, or corrections only) and add notes\nfor the AI. The instructions themselves are fixed; the dialog shows them.",
            "Change the instructions used to generate the content review report.",
            "Generate a Word document for each input file containing the image,\nextracted text, and (if enabled) the proofread text.",
            "Level:",
            "Set which types of personal information to hide and how.",
            "Table — side-by-side columns: Image | Extracted text | Proofread text.\nComments — two columns with tracked changes shown as Word comments.",
            "Text file",
            "Text files need an output folder outside the input folder; otherwise the next run would read them as new documents.",
            "Word document",
            "f\"{('▾' if expanded else '▸')} {txt.PROMPTS_HEADING}\"",
            "f'Use an AI to fix spelling and grammar in the extracted text.\\nWhich AI: see {WHERE_AI_MODELS}.'",
            "f'▸ {txt.PROMPTS_HEADING}'",
            "i",
        }
    ),
    "teachers_teammate/gui/_data_flow_summary.py": frozenset(
        {
            '<a href="change">change…</a>',
            "WHERE YOUR DOCUMENTS ARE PROCESSED",
            "Where your documents go with these settings. The dot next to each step under “What should happen” says where that step runs.",
        }
    ),
    "teachers_teammate/gui/_dependency_guard_dialog.py": frozenset(
        {
            "f'<b>{title}:</b>'",
            "f'• {issue.message}'",
        }
    ),
    "teachers_teammate/gui/_export_controller.py": frozenset(
        {
            "Export",
            "Save results as Word documents or text files, optionally anonymized (also available via right-click)",
        }
    ),
    "teachers_teammate/gui/_export_menu.py": frozenset(
        {
            "Export",
        }
    ),
    "teachers_teammate/gui/_run_controller.py": frozenset(
        {
            "",
            "Run Selected",
        }
    ),
    "teachers_teammate/gui/_hardware_profile_selector.py": frozenset(
        {
            '<a href="detect">Detect again</a>',
            "My computer:",
        }
    ),
    "teachers_teammate/gui/_help_dialog.py": frozenset(
        {
            "<h2>Teacher's Teammate</h2>",
            '<p style="color:#888; font-size:8pt;">Powered by Ollama &bull; OpenCV &bull; PaddleOCR models &bull; LangChain<br>pypdfium2 &bull; PySide6 &bull; qdarkstyle<br><br>Inspired by <a href="https://github.com/imanoop7/Ollama-OCR">Ollama-OCR</a> by imanoop7.</p>',
            "About Teacher's Teammate",
            "Third-Party Licenses — Teacher's Teammate",
            "User Guide — Teacher's Teammate",
            "f'<p>Version <b>{ver}</b></p><p>Batch OCR pipeline for scanned documents and handwritten notes.<br>Extracts text from PDFs and images, optionally produces<br>proofread DOCX reports via a local language model.</p>'",
        }
    ),
    "teachers_teammate/gui/_menu_bar.py": frozenset(
        {
            "About Teacher\u2019s Teammate…",
            "All settings…",
            "Answer a few questions to set up the AI for this computer.",
            "Benchmark…",
            "File",
            "Help",
            "How busy this computer is, and how long each document took.",
            "Open Files…",
            "Open Folder…",
            "Quit",
            "Settings",
            "Setup Assistant…",
            "System Monitor",
            "Third-Party Licenses…",
            "Tools",
            "Try a document with different text-recognition settings and compare the results.",
            "User Guide…",
            "f'{PAGE_TITLES[page]}…'",
        }
    ),
    "teachers_teammate/gui/_main_window.py": frozenset(
        {
            "Check",
            "Documents",
            "Teacher's Teammate",
            'f\'Version <b>{version}</b> is available — <a href="{url}" style="color:#ffd966;">Download</a> · turn this check off under {WHERE_GENERAL}\'',
            "✕",
        }
    ),
    "teachers_teammate/gui/_model_guidance.py": frozenset(
        {
            "",
            "Download best choice…",
            "Use best choice",
        }
    ),
    "teachers_teammate/gui/_ocr_config_selector.py": frozenset(
        {
            "",
            "Model:",
            "PDF:",
            "Pages are always read on a computer you control. Online services are not offered: they would receive the whole page, including names and grades.",
            "Preview…",
            "Resolution for rendering PDF pages to images before OCR.\n72 = screen quality, 150 = draft, 300 = recommended, 600 = high quality.\nHas no effect on image inputs (JPG, PNG).",
            "Use the text embedded in a PDF instead of recognising it.\n\nauto — only when every page is clean, text-only (no scans, no\n  handwriting, annotations or form fields); otherwise the pages\n  are recognised.\nalways — whenever every page contains any text.\nnever — always recognise the rendered pages.\nHas no effect on image or text files.",
        }
    ),
    "teachers_teammate/gui/_ollama_status_line.py": frozenset(
        {
            "",
        }
    ),
    "teachers_teammate/gui/_preprocess_preview.py": frozenset(
        {
            "Ctrl + scroll wheel to zoom",
            "f'Preprocessing Preview — {ocr_config.preprocess_method}'",
        }
    ),
    "teachers_teammate/gui/_prompt_editor.py": frozenset(
        {
            "",
            "f\"{('▾' if expanded else '▸')} {txt.FIXED_HEADING}\"",
            "f'▸ {txt.FIXED_HEADING}'",
        }
    ),
    "teachers_teammate/gui/_readiness_line.py": frozenset(
        {
            "",
            "Whether a run can start with the current settings. It never blocks Run: the requirements are checked again when you press Run.",
        }
    ),
    "teachers_teammate/gui/_results_table.py": frozenset(
        {
            "",
            "Check",
            "Open",
            "Re-run Proofreading",
            "Re-run Text Recognition",
            "Run Content Review",
            "What is sent for this file…",
            "ⓘ",
            "⚠",
            "✗",
        }
    ),
    "teachers_teammate/gui/_stats_widget.py": frozenset(
        {
            "Install psutil for live system stats.",
            "f'{cpu:.0f} %'",
            "f'{mem.used / 2 ** 30:.1f} / {mem.total / 2 ** 30:.1f} GB'",
            "f'{usage.busy_percent} %'",
            "f'{usage.vram_used_mb / 1024:.1f} / {usage.vram_total_mb / 1024:.1f} GB'",
            "\u2013",
        }
    ),
    "teachers_teammate/gui/_system_monitor.py": frozenset(
        {
            "System Monitor",
        }
    ),
    "teachers_teammate/gui/_what_is_sent_dialog.py": frozenset(
        {
            "Left: the extracted text. Right: what a service sees when names are hidden.",
            "f'{stage.title} — {stage.badge}\\n{PAGES_SENT}'",
        }
    ),
    "teachers_teammate/gui/benchmark/__init__.py": frozenset(
        {
            "The Benchmark window needs PySide6-Essentials, which is part of the base install. Reinstall with: pip install --force-reinstall teachers-teammate",
        }
    ),
    "teachers_teammate/gui/benchmark/_benchmark_window.py": frozenset(
        {
            "Choose document…",
            "Compare",
            "Document",
            "Log",
            "No document selected.",
            "OCR configuration",
            "Run && store",
            "Stop",
            "Teacher's Teammate — OCR Benchmark",
        }
    ),
    "teachers_teammate/gui/benchmark/_compare_view.py": frozenset(
        {
            "Diff (A → B)",
            "Select run A and run B to compare.",
            "f'Similarity: {pct:.1f}%   ·   A: {comparison.stats_a.words} words / {comparison.stats_a.chars} chars   ·   B: {comparison.stats_b.words} words / {comparison.stats_b.chars} chars'",
        }
    ),
    "teachers_teammate/gui/benchmark/_runs_list.py": frozenset(
        {
            "Clear all runs for this document",
            "Delete",
            "Set as A",
            "Set as B",
            "Stored runs",
        }
    ),
    "teachers_teammate/gui/check/_page.py": frozenset(
        {
            "",
            "Copied",
            "Copy",
            "Copy the text of this document, or save it as a text file",
            "Next document in the queue",
            "Previous document in the queue",
            "▶",
            "◀",
        }
    ),
    "teachers_teammate/gui/settings/_ai_service_section.py": frozenset(
        {
            "f'{_listing_status(listing, service)} {_status_note(saved_model_not_offered_text(previous, service))}'",
            "f'{_listing_status(listing, service)} {self._retiring_note(retiring, service)}'",
        }
    ),
    "teachers_teammate/gui/settings/_ai_setup_page.py": frozenset(
        {
            "f'<b>{txt.SPEED_HEADING}</b>'",
        }
    ),
    "teachers_teammate/gui/settings/_computer_box.py": frozenset(
        {
            "",
        }
    ),
    "teachers_teammate/gui/settings/_credentials.py": frozenset(
        {
            "f\"<b>{html.escape(title)}</b> <span style='color:#888;'>({html.escape(self.env_key)})</span>\"",
        }
    ),
    "teachers_teammate/gui/settings/_privacy_page.py": frozenset(
        {
            "f'<b>{LIST_HEADING}</b>'",
        }
    ),
    "teachers_teammate/gui/settings/_server_search_dialog.py": frozenset(
        {
            "Stopping…",
        }
    ),
    "teachers_teammate/gui/settings/_speed_panel.py": frozenset(
        {
            "",
        }
    ),
    "teachers_teammate/gui/settings/addons/_common.py": frozenset(
        {
            "f'{base}  {installed_mark(key, results)}'",
        }
    ),
    "teachers_teammate/gui/settings/addons/_install_status.py": frozenset(
        {
            "",
            "Install / download log will appear here…",
        }
    ),
    "teachers_teammate/gui/settings/addons/_name_detection_tab.py": frozenset(
        {
            "Name detection",
            "Name detection models",
            "spaCy  (spacy)",
            "spaCy is the library that finds names and other personal information. Install it first, then download a name detection model below.",
        }
    ),
    "teachers_teammate/gui/settings/addons/_ollama_models_tab.py": frozenset(
        {
            "",
            "AI models for this computer (Ollama)",
            "Add",
            "Add a model by name:",
            "Select the best models for my computer",
            "e.g. llama3.2:latest",
        }
    ),
    "teachers_teammate/gui/settings/addons/_providers_tab.py": frozenset(
        {
            "Online AI services",
        }
    ),
    "teachers_teammate/gui/settings/addons/_reading_models_tab.py": frozenset(
        {
            "f'Built-in reader (PP-OCR) — {READER_SIZE_LABEL}'",
            "f'Saved in: {self._svc.reader.models_dir()}'",
            "f\"The built-in reader reads pages on this computer, without a server. {txt.OCR_ENGINE_HINTS['ppocr']} Its model files, and the page models of the automatic image preparation, come from PaddlePaddle (Apache-2.0).\"",
        }
    ),
    "teachers_teammate/gui/settings/addons/_update_banner.py": frozenset(
        {
            "f'<b>{txt.ADDONS_OUTDATED_LINE}.</b> {txt.ADDONS_OUTDATED_NOTE}'",
        }
    ),
}
