"""One home for everything a teacher reads.

Every sentence the app shows a teacher is defined in this package and used by reference
elsewhere: a widget, a dialog or a CLI line names a constant or calls a function, it never
builds the sentence itself.  That keeps the wording reviewable in one pass and lets a test
stop new literals from creeping back into the adapters.

Which module owns what:

===================  ==================================================================
``_types.py``        the shapes: :class:`Notice`, :class:`Question`, :class:`FilePrompt`
``addons.py``        the reading engines, their downloads and the Add-ons page's installs
``check.py``         what the Check tab's widgets say around its text
``dialogs.py``       every message box, question and file picker
``issues.py``        one sentence per unmet requirement of a step or a run
``models.py``        this computer, the suitability of a model, the download warnings
``privacy.py``       what is sent for a file, and the data-flow wording
``review.py``        the review, the prompt dialogs, the queue, the speed test, sent results
``run.py``           what a run says: the battery warning, a stopped file
``servers.py``       how a found Ollama server is described (the card)
``settings.py``      the Settings window's page names and the "where to change it" phrases
``setup.py``         every sentence of the Setup Assistant's pages
===================  ==================================================================

Rules that keep it one home:

* **Dependencies point inward.**  This package may import ``pkg`` and ``domain``;
  ``domain`` and ``application`` never import it.  They return ids and data, the adapter
  asks this package for the sentence.
* **Dynamic parts are functions** — ``no_files_found(folder)``, never ``"…" % x`` at the
  call site.
* **Dialogs go through one helper**, ``gui/_dialogs.py``: ``notify``, ``ask``, ``choose``,
  ``pick_folder`` and friends.  A call site never touches ``QMessageBox`` or
  ``QFileDialog``, so a test patches one place instead of every window.
* **Shared constants stay single-sourced.**  A sentence that points at a page names it
  through ``settings.py``, so renaming the page renames the sentence.

Out of scope on purpose: the LLM prompts (``domain/correction_prompts.py`` — versioned and
hashed into the cache key), log lines, developer exceptions that never reach a teacher, and
the Word file's label tables, which follow the *document* language rather than the app's.
"""
