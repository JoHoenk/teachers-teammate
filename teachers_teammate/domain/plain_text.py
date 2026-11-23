"""Plain text: what a text file holds, and what a Word document can take.

Pure: no I/O, no library.  Text arrives from a PDF's text layer, a text file, a model's answer
or the teacher's own edits, and any of them can carry a byte sequence that is not text or a
character a document cannot hold.  Kept as the transcription, such a character makes the DOCX
writer reject the whole document ("All strings must be XML compatible"), so every string that is
written goes through :func:`xml_safe_text` (or, for a PDF's text layer, :func:`printable_text`).
"""

from __future__ import annotations

import codecs
import unicodedata

#: Control, format, surrogate, private-use and unassigned characters.
_UNPRINTABLE_CATEGORIES = frozenset({"Cc", "Cf", "Cs", "Co", "Cn"})

#: Control and surrogate characters: XML 1.0 has no place for them.
_CONTROL_CATEGORIES = frozenset({"Cc", "Cs"})

#: The replacement character and the two non-characters XML refuses.
_NOT_XML = frozenset({"\ufffd", "\ufffe", "\uffff"})

#: Byte-order marks, longest first, with the codec each one names.
_BOM_CODECS = (
    (codecs.BOM_UTF32_LE, "utf-32"),
    (codecs.BOM_UTF32_BE, "utf-32"),
    (codecs.BOM_UTF8, "utf-8-sig"),
    (codecs.BOM_UTF16_LE, "utf-16"),
    (codecs.BOM_UTF16_BE, "utf-16"),
)


def is_printable(char: str) -> bool:
    """Return False for control, unassigned, private-use and replacement characters."""
    return char != "\ufffd" and unicodedata.category(char) not in _UNPRINTABLE_CATEGORIES


def printable_text(text: str) -> str:
    """Return *text* without unprintable characters, keeping line breaks and tabs."""
    return "".join(char for char in text if char in "\n\t" or is_printable(char))


def xml_safe_text(text: str) -> str:
    """Return *text* without what a Word document cannot hold, and nothing else.

    Control codes (but line breaks and tabs), surrogates, the replacement character and the two
    non-characters go.  Format characters stay: the joiners of an emoji or of Persian text, the
    direction marks of right-to-left text and the soft hyphen are the writer's own.
    """
    return "".join(
        char
        for char in text
        if char in "\n\t"
        or (char not in _NOT_XML and unicodedata.category(char) not in _CONTROL_CATEGORIES)
    )


def decode_text(data: bytes) -> str:
    """Return *data* as text: the codec its byte-order mark names, else UTF-8, else Windows-1252.

    A Windows text file written by Word carries the quotes, the dash and the euro sign in
    cp1252, which is not Latin-1: read as Latin-1 they become control codes.  A byte cp1252
    does not define becomes the replacement character, which :func:`xml_safe_text` drops.
    Every line ending (CR LF, a lone CR) is a ``"\\n"``, as a text file read in text mode.
    """
    return _universal_newlines(_decoded(data))


def _universal_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _decoded(data: bytes) -> str:
    for bom, codec in _BOM_CODECS:
        if data.startswith(bom):
            return data.decode(codec, errors="replace")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace")
