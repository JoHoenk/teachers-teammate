"""One home for everything a teacher reads.

Every sentence the app shows a teacher is defined in this package and used by reference
elsewhere: a widget, a dialog or a CLI line names a constant or calls a function, it never
builds the sentence itself.  That keeps the wording reviewable in one pass and lets a test
stop new literals from creeping back into the adapters.

Which module owns what:

===================  ==================================================================
``_types.py``        the shapes: :class:`Notice`, :class:`Question`, :class:`FilePrompt`
``addons.py``        the reading engines, their downloads and the Add-ons page's installs
``app.py``           the app's name, the names of its steps, the update banner, Help and About
``benchmark.py``     the Benchmark window
``check.py``         what the Check tab's widgets say around its text
``cli.py``           the command line's help and the lines it prints while it works
``credentials.py``   the Settings list of access keys (API keys) and the note under each
``dialogs.py``       every message box, question and file picker
``fixes.py``         the readiness line's fixes: the line for what is missing and its button
``issues.py``        one sentence per unmet requirement of a step or a run
``main_window.py``   the main window's rows, steps, panel, results table, export and stats
``menus.py``         the main window's menu bar and what each entry offers
``messages.py``      the English wording of every key in the root ``messages.py``
``models.py``        this computer, the model fields and their status line, the download warnings
``privacy.py``       what is sent for a file, the data-flow wording, a remembered answer
``report.py``        the banner, the progress lines and the closing block of a run's log
``review.py``        the review, the proofreading warnings, the prompt dialogs, the queue, the
                     speed test, sent results
``run.py``           what a run says: the battery warning, a stopped file, a file or a stage that
                     failed, where the output goes
``servers.py``       an Ollama server: its card, its models and what it answered
``settings.py``      the Settings window's page names and the "where to change it" phrases
``setup.py``         every sentence of the Setup Assistant's pages
===================  ==================================================================

Rules that keep it one home:

* **Dependencies point inward.**  This package may import ``pkg`` and ``domain``;
  ``domain`` and ``application`` never import it.  They return ids and data, the adapter
  asks this package for the sentence.  ``infrastructure`` raises a failure as a ``Message``
  (``teachers_teammate.messages``, worded by ``messages.py`` here) and asks this package for the
  sentences it puts into a result or prints.
* **Dynamic parts are functions** — ``no_files_found(folder)``, never ``"…" % x`` at the
  call site.
* **Dialogs go through one helper**, ``gui/_dialogs.py``: ``notify``, ``ask``, ``choose``,
  ``pick_folder`` and friends.  A call site never touches ``QMessageBox`` or
  ``QFileDialog``, so a test patches one place instead of every window.
* **Shared constants stay single-sourced.**  A sentence that points at a page names it
  through ``settings.py``, so renaming the page renames the sentence.

Out of scope on purpose: the LLM prompts (``domain/correction_prompts.py`` — versioned and
hashed into the cache key), the developer log, developer exceptions that never reach a teacher, and
the Word file's label tables, which follow the *document* language rather than the app's.

Imported, this package installs English for :class:`~teachers_teammate.messages.Message`,
so ``str(message)`` gives the sentence.  A second language would be installed here instead.
"""

from .messages import install as install

install()
