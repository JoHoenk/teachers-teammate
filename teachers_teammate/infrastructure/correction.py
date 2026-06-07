"""Text correction implementations.

:class:`LangChainCorrector` — accepts any LangChain ``BaseChatModel``; provider-agnostic.
:class:`NativeOllamaCorrector` — uses :class:`~.ollama_utils.OllamaClient` directly; no LangChain.

Both implement :class:`~teachers_teammate.interfaces.Corrector`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from langchain_core.language_models import BaseChatModel

    from .ollama_utils import OllamaClient

from ..interfaces import Corrector
from ._llm_invoke import invoke_langchain_chain, invoke_ollama_chat

ENGLISH_PROMPT = (
    "You are a proofreading assistant. "
    "Correct grammar and spelling mistakes in the provided text. "
    "Apply the minimum number of changes necessary: "
    "preserve the author's wording, style, tone, formatting, and structure "
    "as closely as possible. "
    "Do not rephrase, restructure, reorder, add, or remove content. "
    "Return only the corrected text, nothing else."
)

GERMAN_PROMPT = (
    "Du bist ein Korrekturdienst. "
    "Korrigiere Grammatik- und Rechtschreibfehler im vorliegenden Text. "
    "Nimm so wenige Änderungen wie möglich vor: "
    "Bewahre Wortwahl, Stil, Ton, Formatierung und Struktur des Autors "
    "so genau wie möglich. "
    "Formuliere nicht um, strukturiere nicht um, füge nichts hinzu "
    "und entferne nichts. "
    "Gib ausschließlich den korrigierten Text zurück, sonst nichts."
)

FRENCH_PROMPT = (
    "Vous êtes un assistant de correction. "
    "Corrigez les fautes de grammaire et d'orthographe dans le texte fourni. "
    "Apportez le minimum de modifications nécessaires : "
    "préservez autant que possible les formulations, le style, le ton, "
    "la mise en forme et la structure de l'auteur. "
    "Ne reformulez pas, ne restructurez pas, ne réordonnez pas, n'ajoutez pas "
    "et ne supprimez pas de contenu. "
    "Renvoyez uniquement le texte corrigé, rien d'autre."
)

SPANISH_PROMPT = (
    "Eres un asistente de corrección. "
    "Corrige los errores gramaticales y ortográficos del texto proporcionado. "
    "Realiza el mínimo número de cambios necesarios: "
    "preserva en la medida de lo posible la redacción, el estilo, el tono, "
    "el formato y la estructura del autor. "
    "No reformules, no reestructures, no reordenes, no añadas ni elimines contenido. "
    "Devuelve únicamente el texto corregido, nada más."
)

ITALIAN_PROMPT = (
    "Sei un assistente di correzione. "
    "Correggi gli errori grammaticali e ortografici nel testo fornito. "
    "Apporta il minimo numero di modifiche necessarie: "
    "preserva per quanto possibile la formulazione, lo stile, il tono, "
    "la formattazione e la struttura dell'autore. "
    "Non riformulare, non ristrutturare, non riordinare, non aggiungere "
    "e non rimuovere contenuto. "
    "Restituisci solo il testo corretto, nient'altro."
)

PORTUGUESE_PROMPT = (
    "Você é um assistente de correção. "
    "Corrija os erros gramaticais e ortográficos no texto fornecido. "
    "Faça o mínimo de alterações necessárias: "
    "preserve ao máximo a redação, o estilo, o tom, a formatação "
    "e a estrutura do autor. "
    "Não reformule, não reestruture, não reordene, não adicione nem remova conteúdo. "
    "Devolva apenas o texto corrigido, nada mais."
)

DUTCH_PROMPT = (
    "Je bent een correctie-assistent. "
    "Verbeter grammatica- en spelfouten in de aangeleverde tekst. "
    "Breng zo min mogelijk wijzigingen aan: "
    "bewaar de woordkeuze, stijl, toon, opmaak en structuur van de auteur "
    "zo nauwkeurig mogelijk. "
    "Herformuleer niet, herstructureer niet, herorden niet, voeg niets toe "
    "en verwijder niets. "
    "Geef alleen de gecorrigeerde tekst terug, niets anders."
)

# Named presets — "" and "english" both resolve to the English prompt.
PRESET_PROMPTS: dict[str, str] = {
    "english": ENGLISH_PROMPT,
    "german": GERMAN_PROMPT,
    "french": FRENCH_PROMPT,
    "spanish": SPANISH_PROMPT,
    "italian": ITALIAN_PROMPT,
    "portuguese": PORTUGUESE_PROMPT,
    "dutch": DUTCH_PROMPT,
}
_PROMPTS: dict[str, str] = {
    "": ENGLISH_PROMPT,
    **PRESET_PROMPTS,
}


def _resolve_prompt(prompt: str, language: str = "") -> str:
    """Return the system prompt: named presets, auto-match by language, or custom string."""
    if prompt:
        return _PROMPTS.get(prompt, prompt)
    # Auto-match to a language-specific preset when no custom prompt is set.
    return _PROMPTS.get(language.lower(), ENGLISH_PROMPT)


class LangChainCorrector(Corrector):
    """LangChain-backed proofreading service (grammar & spelling correction).

    Accepts any :class:`~langchain_core.language_models.BaseChatModel`, making
    the underlying LLM trivially swappable.  Build the model first with
    :func:`~teachers_teammate.infrastructure.llm_factory.build_llm`.
    """

    def __init__(self, llm: BaseChatModel, prompt: str = "") -> None:
        self._llm = llm
        self._prompt = prompt

    def correct(self, raw_text: str, language: str) -> tuple[str, str | None]:
        """Return proofread text and an optional warning. On error, returns raw text with warning."""
        return invoke_langchain_chain(
            self._llm,
            system=_resolve_prompt(self._prompt, language),
            human="Language: {language}\n\nText:\n\n{text}",
            variables={"text": raw_text, "language": language},
            fallback=raw_text,
            failure_prefix="Correction failed",
            failure_suffix="; keeping original text.",
        )


class NativeOllamaCorrector(Corrector):
    """Ollama-backed proofreading using the native HTTP API (no LangChain).

    Accepts an :class:`~.ollama_utils.OllamaClient` injected by the stage
    builder — the client is shared across pipeline stages and must not be
    constructed here.
    """

    def __init__(
        self,
        model: str,
        client: OllamaClient,
        prompt: str = "",
        timeout: float = 120.0,
        temperature: float = 0.7,
    ) -> None:
        self._model = model
        self._client = client
        self._prompt = prompt
        self._timeout = timeout
        self._temperature = temperature

    def correct(self, raw_text: str, language: str) -> tuple[str, str | None]:
        """Return proofread text and an optional warning. On error, returns raw text with warning."""
        system = _resolve_prompt(self._prompt, language)
        return invoke_ollama_chat(
            self._client,
            model=self._model,
            prompt=f"{system}\n\nLanguage: {language}\n\nText:\n\n{raw_text}",
            timeout=self._timeout,
            temperature=self._temperature,
            fallback=raw_text,
            failure_prefix="Correction failed",
            failure_suffix="; keeping original text.",
        )
