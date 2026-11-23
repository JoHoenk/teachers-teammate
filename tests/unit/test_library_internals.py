"""The two library internals the app reaches into, pinned against a silent loss.

"Stop now" closes the connections of a waiting Ollama request, and a comment range is anchored on
the XML element of the runs it covers. Both sit below the public API a reader would find, so a
library bump that moves them fails here instead of quietly losing the feature.
"""

from docx import Document
import ollama


def test_an_ollama_client_closes_its_own_connections() -> None:
    """
    Given  an Ollama client
    When   it is asked for the close the app calls to cut a request off
    Then   the client itself has one, so no private attribute is needed
    """
    client = ollama.Client(host="http://127.0.0.1:11434")

    try:
        assert callable(getattr(client, "close", None)), (
            "ollama.Client lost close(): cutting a request off would need `_client` again"
        )
    finally:
        client.close()


def test_a_docx_run_keeps_its_xml_element_reachable() -> None:
    """
    Given  a run of a python-docx paragraph
    When   it is asked for the element under it
    Then   ``_r`` is that run's own ``w:r``, where a comment range is anchored before and after
    """
    run = Document().add_paragraph().add_run("Text")

    element = getattr(run, "_r", None)

    assert element is not None, "python-docx no longer exposes the XML element of a run as `_r`"
    assert element.tag.endswith("}r"), element.tag
    assert callable(getattr(element, "addprevious", None))
    assert callable(getattr(element, "addnext", None))
