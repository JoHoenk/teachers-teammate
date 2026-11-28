# Error handling and logging

Two audiences, one rule each: the teacher sees what they can act on, and the log file holds
everything.

## The two audiences

- **The teacher** sees a failure where they look: the row of the file, the status line, the
  dialog of the action they took. Short, and only what they can do about it.
- **The log file** (`<data root>/logs/teachers-teammate.log`, rotated, four files) is the full
  record: what failed, where, and with which kind of error. It leaves the computer — a teacher
  sends it to whoever helps — so no line holds a document's text, a prompt or a key
  (`tests/unit/test_log_lines_carry_no_text.py`). Help → Open Log Folder… opens the folder it
  sits in.

## Log once, where it is handled

The code that handles an error logs it: it catches it and does not re-raise, or it hands it to a
caller that shows it. Code that raises, or that passes an error on, logs nothing.

`messages.log_failure(logger, what, error)` is that one call, and it tells the two kinds apart:

| Kind | What makes it one | Line |
|---|---|---|
| Expected | carries a `Message` (`error.args[0]`) | WARNING, the message, no traceback |
| Bug | anything else | ERROR, the type and the frames, never the message |

An exception's own words can quote the text it choked on. A bug's line names the type — and,
for an `OSError`, its `errno` and `strerror` — and nothing else. An expected error that wraps a
foreign one (`detail`) logs the bare key, not the foreign words. Either line names the kinds
of the errors it was raised from, so the root cause stays visible.

## Nothing is lost

`start_logging()` (`infrastructure/logging_setup.py`) installs the nets of the process, once:

- `sys.excepthook`, `threading.excepthook` and `sys.unraisablehook` write a bug's line and then
  hand the exception to the entry point's `on_unexpected` — for a Qt slot, a `QThread.run` and a
  thread of its own. An exception Python could not raise at all (a destructor's) is logged but
  not handed on. The interpreter's own hook still runs after the line, so a console shows the
  full error; stderr leaves the net's line to the file. Ctrl+C is the teacher's doing and
  only gets the interpreter's report.
- `faulthandler` writes the stack of a native crash (a segfault in a reader or in Qt) to
  `logs/crash.log`; the handle stays open for the life of the process. Each start writes a
  header line (time, version, pid), so a stack belongs to the session above it; a file without
  a stack holds no crash. It replaces a `PYTHONFAULTHANDLER` target, and on Windows it also
  reports exceptions native code handled itself.
- Qt's own messages go to the logger `qt` (`infrastructure/qt_log.py`), installed before the
  first `QApplication`: warnings and above are logged with their category, one warning once per
  text; the rest is a debug detail.

The frozen entry (`frozen_entry.py`) starts both before the GUI is imported, so a failed import
or a platform plugin that does not load reaches the file.

Inside the GUI, `gui/_background.py` logs every failure it hands to `on_done`, named by the
task, and `OCRWorker` and `LongJobThread` log a crashed run before they report it. A catch-all
that shows the teacher a failure (an export, the settings file, a preview) logs it as well. A
caller therefore decides only what the teacher is shown.

## One surface in the window

`gui/_errors.py` is the only module that shows a failure, and it shows it through
`gui/_dialogs.py`:

- `show_failure(parent, notice, error=None)` writes *error* down and shows *notice*. The line goes
  through `messages.log_once`, so a failure a catch point already wrote (a background task, a run's
  per-file net) is not written twice.
- `show_unexpected(parent, error)` shows `dlg.unexpected_error()`: a plain sentence about a bug and
  the choice "Open Log Folder". The error's kind and frames are in the file already, and its own
  words stay out of the window, since they can quote the document that choked it. One such notice
  at a time, and the same kind of bug not again within ten seconds — a bug in a slot that fires with
  every key press must not bury the teacher in boxes.
- `show_unexpected_anywhere(error)` is what `start_logging` calls as `on_unexpected`: it queues the
  bug from whichever thread it ended on to the GUI thread, through the bridge `install_notices()`
  makes.
- `show_notice(parent, notice)` carries a sentence that reports no failure.

A failed file is never a dialog. The row says why (`_results_table._warning_text`), and the
end-of-run line and the tray count the failures. A run with every file failed leaves the teacher
one line, not thirty boxes.

An installer keeps a pane of its own, but it shows progress — pip's output. When pip fails, the
last twenty lines of it go to the log at WARNING (`addon_pip.PIP_FAILURE_LINES`), since the
pane closes with the dialog. That is the child process's output, not the error: the handler
still logs the error once.

## Where an event goes

| Kind | Example | Teacher sees | Log |
|---|---|---|---|
| Expected, one file | a stage failed, a file was skipped | the row, the end-of-run line | the `Message` |
| Expected, one action | save, export or settings load failed | one notice | WARNING, the `Message` |
| Environment state | Ollama unreachable, model missing | the readiness line | WARNING once per change |
| Bug | any other exception | one notice, with "Open Log Folder" | ERROR, type and frames |
| Native crash | a segfault in a reader or in Qt | — | `logs/crash.log` |
| Success, progress | DOCX saved, `[3/10] Processing` | status bar, progress bar, row | INFO |

## How much the file holds

"Log detail" on the General page (`log_detail`, default Normal) sets the level of our package and
of the libraries beside it:

| Log detail | `teachers_teammate` | other libraries |
|---|---|---|
| Errors only | WARNING | WARNING |
| Normal (default) | INFO | WARNING |
| Detailed | DEBUG | INFO |

The other libraries stay at WARNING until Detailed because a busy one says nothing useful at INFO:
`httpx` writes every request, and the readiness check polls every ten seconds. Our own INFO lines
are the run's narrative — what started, which file, what was saved.

A start can decide the detail instead of the setting, which is what a support case needs: the flag
`--log-level errors|normal|detailed`, the environment variable `TEACHERS_TEAMMATE_LOG_LEVEL`, and
`--diagnose`, which means Detailed. The flag wins over the environment, the environment over the
setting, and an override holds for the whole session. The General page offers Normal and Detailed;
`errors` is reachable by the flag and the environment.

The window keeps no pane of log lines: a run's story is kept in the file alone (logger
`RUN_LOGGER`), where a `Reporter` line is status at INFO, warning at WARNING and error at ERROR.
What the teacher watches of a run is the progress bar, the rows and the status bar, which shows
the last success line — a document saved, a stored result expired.

The first line of a start places the lines below it: version, operating system, whether this is the
frozen build, the data root, and the detail in force. A path under the home folder is written as
`~/…` by one filter on the file handler, so a file can be sent on as it is.

`logs/diagnostics.log` is not this file: it holds start-up timings and GUI-thread stalls, written
only with `--diagnose`.

On the command line the file is always written, and the lines also go to the console only with
`-v` (`--verbose`).
