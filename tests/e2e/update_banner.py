"""The update banner in the built app: the real HTTPS ask, and what the window shows.

The app runs this file with ``--_run_script <this.py> <result.json> [--tag <tag>]``
(:func:`~teachers_teammate.frozen_modes.run_script`), so it needs no test build, and it is never
inside the bundle.  What it proves is the chain no unit test covers: the bundle's own ``urllib``
finds CA certificates, the request goes out, the answer comes back to the window's thread, and the
banner a teacher would see is there.  A break in any link looks exactly like "no update", so
nothing a teacher reports would show it.

It claims an old version of itself first (``OLD_VERSION``), so a published release is newer than
this build.  ``application.composition`` reads the version when the services are built, so the
override only has to come before that.

A refused answer is not the same as no answer.  GitHub answers ``403`` or ``429`` with
``x-ratelimit-remaining: 0`` when its hourly limit for a shared address is used up, which says TLS
and the request work and only the banner cannot be checked; that passes and is written down.  Every
other failure fails, and so does a window that shows no banner.  The script asks the feed directly
before it builds a window, so a limited runner is recognised as one.

The result is one JSON file, written on failure too, because a windowed build on Windows has no
console.  The exit code is 0 only when the check held.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Iterator
from contextlib import contextmanager
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time
from typing import Any

from PySide6.QtWidgets import QApplication, QFrame, QLabel, QPushButton

from teachers_teammate.domain.releases import RELEASE_URL_PREFIX

#: The version the script claims to be: below every published release, so one is always offered.
OLD_VERSION = "0.0.1"
#: The banner and the label the window builds it from.
BANNER = "UpdateBanner"
LABEL = "UpdateBannerLabel"
#: What a rate-limited runner records: the ask worked, the banner stays unchecked.
RATE_LIMITED = "rate limited"
RATE_LIMITED_DETAIL = "TLS and the request work, banner not checked"
#: How long the banner may take.  The check runs off the GUI thread, and a slow answer is no bug.
BANNER_S = 20.0
#: How long a window with the check switched off is watched for a request it must not send.
QUIET_S = 10.0
#: The pause between two turns of the event loop.
PUMP_S = 0.05
#: How long the app's background tasks may need after a window closed.
BACKGROUND_MS = 30_000
#: The settings a window is built on.  Every key is one the schema knows: a notice at load is a box,
#: and a box is not what this script is about.  ``ollama_url`` points nowhere, so the readiness
#: check finds a stopped server instead of contacting whatever runs on this computer.
SETTINGS = """\
check_for_updates = {check}
ollama_url = "http://127.0.0.1:9"
ocr_engine = "ppocr"
language = "English"
correction_enabled = false
evaluation_enabled = false
anonymization_enabled = false
docx_enabled = true
text_enabled = false
text_anonymized_enabled = false
output = "{output}"
"""


class _Failed(Exception):
    """One condition of the check did not hold; the message says which."""


def _count_requests() -> list[str]:
    """Count every request the app sends to the release feed, and let each one through.

    The feed reads the network through this module-level name, so wrapping it counts the app's
    own asks — the window's included — and changes nothing about what they do.
    """
    from teachers_teammate.infrastructure import release_feed  # noqa: PLC0415

    calls: list[str] = []
    real_urlopen = release_feed.urlopen

    def counting(request: Any, *args: Any, **kwargs: Any) -> Any:
        calls.append(str(getattr(request, "full_url", request)))
        return real_urlopen(request, *args, **kwargs)

    release_feed.urlopen = counting  # ty: ignore[invalid-assignment]
    return calls


def _ask_feed() -> tuple[str, str]:
    """Return the ``(tag, url)`` the release feed answers with now."""
    from teachers_teammate.infrastructure.release_feed import (  # noqa: PLC0415
        GitHubReleaseFeed,
    )

    return GitHubReleaseFeed().latest()


def _old_version() -> None:
    """Make this process claim to be an old build, before anything is composed."""
    import teachers_teammate  # noqa: PLC0415

    teachers_teammate.__version__ = OLD_VERSION  # ty: ignore[invalid-assignment]


def _seen_setup() -> None:
    """Store the app version as seen, so the Setup Assistant is not offered over the window.

    An update offers it once, as a modal window nothing here would close.
    """
    from teachers_teammate.gui import _settings_file  # noqa: PLC0415
    from teachers_teammate.gui._qsettings import app_settings  # noqa: PLC0415

    store = app_settings()
    store.setValue(_settings_file.APP_VERSION_KEY, _settings_file.__version__)
    store.sync()


def _services() -> Any:
    """Return the app's services, built the way the app builds them at start."""
    from teachers_teammate.application.composition import build_app_services  # noqa: PLC0415

    return build_app_services()


def _write_settings(root: Path, *, check_for_updates: bool) -> Path:
    """Write the settings file the next window is built on, and return its path."""
    path = root / "ocr.toml"
    path.write_text(
        SETTINGS.format(
            check="true" if check_for_updates else "false",
            output=(root / "Ausgabe").as_posix(),
        ),
        encoding="utf-8",
    )
    return path


@contextmanager
def isolated() -> Iterator[Path]:
    """Run with one temporary folder for the settings, the data root and the cache.

    ``TEACHERS_TEAMMATE_TMPDIR`` is what the app reads (``config.override_root``), and under it the
    app also keeps its GUI settings in a file instead of the platform's registry or plist.
    """
    previous = os.environ.get("TEACHERS_TEAMMATE_TMPDIR")
    # The real path: macOS keeps its temporary folder behind a link (/var -> /private/var).
    folder = str(Path(tempfile.mkdtemp(prefix="teachers-teammate-banner-")).resolve())
    os.environ["TEACHERS_TEAMMATE_TMPDIR"] = folder
    try:
        yield Path(folder)
    finally:
        if previous is None:
            os.environ.pop("TEACHERS_TEAMMATE_TMPDIR", None)
        else:
            os.environ["TEACHERS_TEAMMATE_TMPDIR"] = previous
        shutil.rmtree(folder, onexc=lambda *_exc: None)


def _pump(seconds: float) -> None:
    """Run the event loop for *seconds*, so the app's own threads can answer."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        QApplication.processEvents()
        time.sleep(PUMP_S)


def _settle(condition: Callable[[], bool], what: str, deadline_s: float) -> None:
    """Pump the event loop until *condition* holds, or fail saying what never happened."""
    deadline = time.monotonic() + deadline_s
    while True:
        QApplication.processEvents()
        if condition():
            return
        if time.monotonic() > deadline:
            raise _Failed(f"{what} within {deadline_s:g} s")
        time.sleep(PUMP_S)


def _wait_for_background() -> None:
    """Wait for the app's background tasks, as the app does when its window closes."""
    from teachers_teammate.gui._background import wait_for_background_tasks  # noqa: PLC0415

    wait_for_background_tasks(BACKGROUND_MS)


def _show(services: Any) -> Any:
    """Build the real main window on *services*, show it, and return it."""
    from teachers_teammate.gui._app_bootstrap import create_app  # noqa: PLC0415
    from teachers_teammate.gui.composition import build_main_window  # noqa: PLC0415

    create_app()
    window = build_main_window(app_service=services)
    window.show()
    _settle(window.isVisible, "the window to be shown", BANNER_S)
    return window


def _banner(window: Any) -> QFrame:
    """Return the window's update banner, or fail naming it."""
    banner = window.findChild(QFrame, BANNER)
    if banner is None:
        raise _Failed(f"the window has no {BANNER}")
    return banner


def _banner_text(banner: QFrame) -> str:
    """Return what the banner says, or fail naming the label it should say it in."""
    label = banner.findChild(QLabel, LABEL)
    if label is None:
        raise _Failed(f"the banner has no {LABEL}")
    return label.text()


def _check_offer(text: str, tag: str, url: str) -> None:
    """Fail unless the banner offers *tag* at *url*, both as the feed named them."""
    if not url.startswith(RELEASE_URL_PREFIX):
        raise _Failed(f"{url} is not a page of this project's releases")
    if f'href="{url}"' not in text:
        raise _Failed(f"the banner does not link to {url}: {text!r}")
    # The link carries the tag itself: what the banner names is what is left around it.
    if tag.lstrip("v") not in text.replace(url, ""):
        raise _Failed(f"the banner does not name {tag}: {text!r}")


def _dismiss(banner: QFrame) -> None:
    """Click the banner's close button, and check that a teacher can put it away."""
    button = banner.findChild(QPushButton)
    if button is None:
        raise _Failed("the banner has no close button")
    button.click()
    _settle(lambda: not banner.isVisible(), "the banner to close", BANNER_S)


def _check(result: dict[str, Any], calls: list[str], ci_tag: str) -> None:
    """Ask the feed, then build the windows and read what they show."""
    from teachers_teammate.exceptions import ReleaseFeedError  # noqa: PLC0415

    try:
        tag, url = _ask_feed()
    except ReleaseFeedError as exc:
        if exc.rate_limited:
            result["state"] = RATE_LIMITED
            result["detail"] = RATE_LIMITED_DETAIL
            return
        raise _Failed(f"the feed gave no release: {exc}") from exc
    result["state"] = "offer"
    result["feed_tag"] = tag
    if ci_tag and ci_tag != tag:
        raise _Failed(f"the feed names {tag}, the CI step read {ci_tag}")
    _old_version()
    with isolated() as root:
        _write_settings(root, check_for_updates=True)
        _seen_setup()
        services = _services()
        window = _show(services)
        banner = _banner(window)
        _settle(banner.isVisible, "the banner to be offered", BANNER_S)
        result["banner"] = text = _banner_text(banner)
        _check_offer(text, tag, url)
        _dismiss(banner)
        window.close()
        _wait_for_background()
        # Written after the window closed: a window saves the settings it holds while it closes.
        sent = len(calls)
        _write_settings(root, check_for_updates=False)
        second = _show(services)
        _pump(QUIET_S)
        if len(calls) != sent:
            raise _Failed(
                f"a window with the check switched off sent {len(calls) - sent} request(s)"
            )
        second.close()
        _wait_for_background()


def _write(path: Path, result: dict[str, Any]) -> None:
    """Write *result* to *path*, creating the folder if it is new."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _summarise(result: dict[str, Any]) -> None:
    """Put the state of the check into the CI job summary, when this runs in one."""
    summary = os.environ.get("GITHUB_STEP_SUMMARY", "")
    if not summary:
        return
    detail = f": {result['detail']}" if result["detail"] else ""
    line = f"- update banner ({result['platform']}): {result['state']}{detail}\n"
    try:
        with open(summary, "a", encoding="utf-8") as sink:
            sink.write(line)
    except OSError:
        pass  # the result file is the record; the step summary is a convenience


def main(argv: list[str]) -> int:
    """Ask the feed, build the windows, write what they showed; 0 when it all held."""
    parser = argparse.ArgumentParser(
        prog="update-banner",
        description="Check that the built app finds a newer release and offers it.",
    )
    parser.add_argument("out", help="the file the result goes into")
    parser.add_argument(
        "--tag", default="", help="the newest published tag, as the CI step read it"
    )
    options = parser.parse_args(argv)
    out = Path(options.out).expanduser().resolve()
    calls = _count_requests()
    result: dict[str, Any] = {
        "ok": False,
        "platform": sys.platform,
        "state": "not asked",
        "detail": "",
        "feed_tag": "",
        "ci_tag": options.tag,
        "banner": "",
        "requests": 0,
        "failed": [],
    }
    try:
        _check(result, calls, options.tag)
    except _Failed as exc:
        result["failed"].append(str(exc))
    except Exception as exc:  # noqa: BLE001 — anything else the check hits is its failure
        result["failed"].append(f"{type(exc).__name__}: {exc}")
    result["requests"] = len(calls)
    result["ok"] = not result["failed"]
    _write(out, result)
    _summarise(result)
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
