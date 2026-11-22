"""The access keys (API keys) of the online AI services: their list, rows and notes."""

from __future__ import annotations

from .settings import PAGE_AI_MODELS

SHOW_KEY = "Show"
HIDE_KEY = "Hide"
KEY_PLACEHOLDER = "Paste your API key here"
REMOVE_KEY = "Remove"
ADD_KEY = "Add key"
GROUP_TITLE = "Access keys (API keys)"
#: Opens both notes: the key list shows the keys that are also entered under the service.
_GROUP_NOTE_SAME_KEYS = (
    "A key is what an online service gives you to use its AI. You can also enter it under "
    f"the service on {PAGE_AI_MODELS}; here you see all of them, can remove one, or add "
    "one for another service. "
)
GROUP_NOTE_KEYCHAIN = (
    _GROUP_NOTE_SAME_KEYS + "Keys are kept in your system's secret store (keychain) and loaded "
    "automatically when the app starts. A key set in the environment before launching takes "
    "priority. A key you enter is used right away and saved when you press OK."
)
GROUP_NOTE_FILE = (
    _GROUP_NOTE_SAME_KEYS + "No system keychain was found, so keys are kept in this app's "
    "settings file on this computer, readable by anyone who can open your user account's files "
    "(they are not encrypted), and loaded automatically when the app starts. A key set in the "
    "environment before launching takes priority. A key you enter is used right away and saved "
    "when you press OK."
)
KEY_LINE_OUTSIDE = (
    "Set outside the app (environment variable). It is used instead of a key saved here."
)
KEY_LINE_KEYCHAIN = "Kept in your system's keychain."
KEY_LINE_FILE = "Kept in this app's settings file, not encrypted (no keychain found)."
KEY_LINE_GET_KEY = "Get a key at {link}."


def key_line_get_key(link: str) -> str:
    """Return the sentence that says where to get a key; *link* is already markup."""
    return KEY_LINE_GET_KEY.format(link=link)
