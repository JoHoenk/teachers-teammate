"""Built-in proofreading policies (one per language) and the migration of saved copies.

Pure text and string comparison: no I/O, no provider imports.  The policy is what a teacher
may change; the answer-format rules are appended by ``infrastructure/correction_schema.py``.
"""

from __future__ import annotations

# ── Policy presets ────────────────────────────────────────────────────────────
# One policy per language: the same rules, translated.  ``{level}`` is filled in by code;
# the output format lives in the contract, not here.

ENGLISH_PROMPT = (
    "You are a proofreading assistant. "
    "Correct the following English student text sentence by sentence at a {level} level. "
    "Correct linguistic and grammatical errors and irregularities while making only "
    "necessary changes. "
    "Keep the student's original choice of words and sentence structure when they are "
    "correct, idiomatic and possible. "
    "Do not rephrase for stylistic reasons or unnecessarily raise the language level.\n\n"
    "Do not assume that a sentence is clear just because it is grammatically correct. "
    "Check whether pronouns and references clearly refer to something, and whether the "
    "meaning of each phrase is understandable. "
    "If wording is unclear or ambiguous, do not silently guess what the student means: "
    "leave the wording as it is and flag it as unclear, even when it is grammatically "
    "correct."
)

GERMAN_PROMPT = (
    "Du bist ein Korrekturassistent. "
    "Korrigiere den folgenden deutschen Schülertext Satz für Satz auf dem Niveau {level}. "
    "Korrigiere sprachliche und grammatische Fehler und Unregelmäßigkeiten und nimm dabei "
    "nur notwendige Änderungen vor. "
    "Behalte die Wortwahl und den Satzbau der Schülerin oder des Schülers bei, wenn sie "
    "korrekt, idiomatisch und möglich sind. "
    "Formuliere nicht aus stilistischen Gründen um und hebe das Sprachniveau nicht "
    "unnötig an.\n\n"
    "Gehe nicht davon aus, dass ein Satz klar ist, nur weil er grammatisch korrekt ist. "
    "Prüfe, ob Pronomen und Verweise eindeutig auf etwas Bezug nehmen und ob die Bedeutung "
    "jeder Formulierung verständlich ist. "
    "Wenn eine Formulierung unklar oder mehrdeutig ist, rate nicht stillschweigend, was "
    "gemeint sein könnte: Lass die Formulierung stehen und kennzeichne sie als unklar, "
    "auch wenn sie grammatisch korrekt ist."
)

FRENCH_PROMPT = (
    "Tu es un assistant de correction. "
    "Corrige le texte d'élève français suivant phrase par phrase au niveau {level}. "
    "Corrige les erreurs et irrégularités linguistiques et grammaticales en n'apportant "
    "que les modifications nécessaires. "
    "Conserve le choix de mots et la structure des phrases de l'élève lorsqu'ils sont "
    "corrects, idiomatiques et possibles. "
    "Ne reformule pas pour des raisons stylistiques et n'élève pas inutilement le niveau "
    "de langue.\n\n"
    "Ne considère pas qu'une phrase est claire simplement parce qu'elle est "
    "grammaticalement correcte. "
    "Vérifie si les pronoms et les références renvoient clairement à quelque chose et si "
    "le sens de chaque formulation est compréhensible. "
    "Si une formulation est peu claire ou ambiguë, ne devine pas silencieusement ce que "
    "l'élève veut dire : laisse la formulation telle quelle et signale-la comme peu "
    "claire, même si elle est grammaticalement correcte."
)

SPANISH_PROMPT = (
    "Eres un asistente de corrección. "
    "Corrige el siguiente texto de estudiante en español frase por frase en un nivel "
    "{level}. "
    "Corrige los errores e irregularidades lingüísticos y gramaticales haciendo solo los "
    "cambios necesarios. "
    "Mantén la elección de palabras y la estructura de las frases del estudiante cuando "
    "sean correctas, idiomáticas y posibles. "
    "No reformules por motivos estilísticos ni eleves el nivel de lengua "
    "innecesariamente.\n\n"
    "No supongas que una frase es clara solo porque es gramaticalmente correcta. "
    "Comprueba si los pronombres y las referencias remiten claramente a algo y si el "
    "significado de cada expresión es comprensible. "
    "Si una expresión es poco clara o ambigua, no adivines en silencio lo que el "
    "estudiante quiere decir: deja la expresión tal como está y márcala como poco clara, "
    "aunque sea gramaticalmente correcta."
)

ITALIAN_PROMPT = (
    "Sei un assistente di correzione. "
    "Correggi il seguente testo di studente in italiano frase per frase a un livello "
    "{level}. "
    "Correggi gli errori e le irregolarità linguistiche e grammaticali apportando solo le "
    "modifiche necessarie. "
    "Mantieni la scelta delle parole e la struttura delle frasi dello studente quando "
    "sono corrette, idiomatiche e possibili. "
    "Non riformulare per motivi stilistici e non alzare inutilmente il livello "
    "linguistico.\n\n"
    "Non dare per scontato che una frase sia chiara solo perché è grammaticalmente "
    "corretta. "
    "Verifica se i pronomi e i riferimenti rimandano chiaramente a qualcosa e se il "
    "significato di ogni espressione è comprensibile. "
    "Se un'espressione è poco chiara o ambigua, non indovinare in silenzio che cosa "
    "intende lo studente: lascia l'espressione com'è e segnalala come poco chiara, anche "
    "se è grammaticalmente corretta."
)

PORTUGUESE_PROMPT = (
    "Você é um assistente de correção. "
    "Corrija o seguinte texto de estudante em português frase por frase no nível {level}. "
    "Corrija os erros e irregularidades linguísticos e gramaticais fazendo apenas as "
    "alterações necessárias. "
    "Mantenha a escolha de palavras e a estrutura das frases do estudante quando "
    "estiverem corretas, idiomáticas e possíveis. "
    "Não reformule por motivos estilísticos nem eleve o nível de língua "
    "desnecessariamente.\n\n"
    "Não presuma que uma frase é clara só porque está gramaticalmente correta. "
    "Verifique se os pronomes e as referências remetem claramente a algo e se o "
    "significado de cada expressão é compreensível. "
    "Se uma expressão for pouco clara ou ambígua, não adivinhe em silêncio o que o "
    "estudante quer dizer: deixe a expressão como está e marque-a como pouco clara, "
    "mesmo que esteja gramaticalmente correta."
)

DUTCH_PROMPT = (
    "Je bent een correctieassistent. "
    "Verbeter de volgende Nederlandse leerlingtekst zin voor zin op niveau {level}. "
    "Verbeter taalkundige en grammaticale fouten en onregelmatigheden en breng daarbij "
    "alleen noodzakelijke wijzigingen aan. "
    "Behoud de woordkeuze en de zinsstructuur van de leerling wanneer die correct, "
    "idiomatisch en mogelijk zijn. "
    "Herformuleer niet om stilistische redenen en verhoog het taalniveau niet onnodig."
    "\n\n"
    "Ga er niet vanuit dat een zin duidelijk is alleen omdat hij grammaticaal correct is. "
    "Controleer of voornaamwoorden en verwijzingen duidelijk naar iets verwijzen en of de "
    "betekenis van elke formulering begrijpelijk is. "
    "Als een formulering onduidelijk of dubbelzinnig is, raad niet stilzwijgend wat de "
    "leerling bedoelt: laat de formulering staan en markeer die als onduidelijk, ook als "
    "die grammaticaal correct is."
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
PROMPTS: dict[str, str] = {
    "": ENGLISH_PROMPT,
    **PRESET_PROMPTS,
}

# ── Saved-prompt migration ───────────────────────────────────────────────────
# Versions before the review window stored the preset *text* in the settings.  The texts
# below are the presets of every released version (they are identical from the initial
# release up to the last release before this branch), so a saved copy of any of them can
# be recognised and replaced by "": "use the built-in prompt for the document language".


def normalize_prompt_text(text: str) -> str:
    """Return *text* with all runs of whitespace collapsed, for preset comparison."""
    return " ".join(text.split())


_LEGACY_ENGLISH_PROMPT = (
    "You are a proofreading assistant. "
    "Correct grammar and spelling mistakes in the provided text. "
    "Apply the minimum number of changes necessary: "
    "preserve the author's wording, style, tone, formatting, and structure "
    "as closely as possible. "
    "Do not rephrase, restructure, reorder, add, or remove content. "
    "Return only the corrected text, nothing else."
)
_LEGACY_GERMAN_PROMPT = (
    "Du bist ein Korrekturdienst. "
    "Korrigiere Grammatik- und Rechtschreibfehler im vorliegenden Text. "
    "Nimm so wenige Änderungen wie möglich vor: "
    "Bewahre Wortwahl, Stil, Ton, Formatierung und Struktur des Autors "
    "so genau wie möglich. "
    "Formuliere nicht um, strukturiere nicht um, füge nichts hinzu "
    "und entferne nichts. "
    "Gib ausschließlich den korrigierten Text zurück, sonst nichts."
)
_LEGACY_FRENCH_PROMPT = (
    "Vous êtes un assistant de correction. "
    "Corrigez les fautes de grammaire et d'orthographe dans le texte fourni. "
    "Apportez le minimum de modifications nécessaires : "
    "préservez autant que possible les formulations, le style, le ton, "
    "la mise en forme et la structure de l'auteur. "
    "Ne reformulez pas, ne restructurez pas, ne réordonnez pas, n'ajoutez pas "
    "et ne supprimez pas de contenu. "
    "Renvoyez uniquement le texte corrigé, rien d'autre."
)
_LEGACY_SPANISH_PROMPT = (
    "Eres un asistente de corrección. "
    "Corrige los errores gramaticales y ortográficos del texto proporcionado. "
    "Realiza el mínimo número de cambios necesarios: "
    "preserva en la medida de lo posible la redacción, el estilo, el tono, "
    "el formato y la estructura del autor. "
    "No reformules, no reestructures, no reordenes, no añadas ni elimines contenido. "
    "Devuelve únicamente el texto corregido, nada más."
)
_LEGACY_ITALIAN_PROMPT = (
    "Sei un assistente di correzione. "
    "Correggi gli errori grammaticali e ortografici nel testo fornito. "
    "Apporta il minimo numero di modifiche necessarie: "
    "preserva per quanto possibile la formulazione, lo stile, il tono, "
    "la formattazione e la struttura dell'autore. "
    "Non riformulare, non ristrutturare, non riordinare, non aggiungere "
    "e non rimuovere contenuto. "
    "Restituisci solo il testo corretto, nient'altro."
)
_LEGACY_PORTUGUESE_PROMPT = (
    "Você é um assistente de correção. "
    "Corrija os erros gramaticais e ortográficos no texto fornecido. "
    "Faça o mínimo de alterações necessárias: "
    "preserve ao máximo a redação, o estilo, o tom, a formatação "
    "e a estrutura do autor. "
    "Não reformule, não reestruture, não reordene, não adicione nem remova conteúdo. "
    "Devolva apenas o texto corrigido, nada mais."
)
_LEGACY_DUTCH_PROMPT = (
    "Je bent een correctie-assistent. "
    "Verbeter grammatica- en spelfouten in de aangeleverde tekst. "
    "Breng zo min mogelijk wijzigingen aan: "
    "bewaar de woordkeuze, stijl, toon, opmaak en structuur van de auteur "
    "zo nauwkeurig mogelijk. "
    "Herformuleer niet, herstructureer niet, herorden niet, voeg niets toe "
    "en verwijder niets. "
    "Geef alleen de gecorrigeerde tekst terug, niets anders."
)

LEGACY_PRESET_PROMPTS: dict[str, str] = {
    "english": _LEGACY_ENGLISH_PROMPT,
    "german": _LEGACY_GERMAN_PROMPT,
    "french": _LEGACY_FRENCH_PROMPT,
    "spanish": _LEGACY_SPANISH_PROMPT,
    "italian": _LEGACY_ITALIAN_PROMPT,
    "portuguese": _LEGACY_PORTUGUESE_PROMPT,
    "dutch": _LEGACY_DUTCH_PROMPT,
}
"""Preset texts of every released version before the review window (``git log -S``)."""

_SUPERSEDED_SUGGESTION_SENTENCES: dict[str, str] = {
    "english": (
        "When you can only propose a version for unclear wording, present it as a labelled suggestion."
    ),
    "german": (
        "Wenn du für eine unklare Formulierung nur einen Vorschlag machen kannst, kennzeichne ihn als Vorschlag."
    ),
    "french": (
        "Si tu ne peux proposer qu'une version pour une formulation peu claire, présente-la comme une suggestion explicite."
    ),
    "spanish": (
        "Si solo puedes proponer una versión para una expresión poco clara, preséntala como una sugerencia claramente indicada."
    ),
    "italian": (
        "Se puoi proporre solo una versione per un'espressione poco chiara, presentala come un suggerimento chiaramente indicato."
    ),
    "portuguese": (
        "Se só puder propor uma versão para uma expressão pouco clara, apresente-a como uma sugestão claramente indicada."
    ),
    "dutch": (
        "Als je voor een onduidelijke formulering alleen een versie kunt voorstellen, presenteer die dan als een duidelijk aangegeven suggestie."
    ),
}
"""Closing sentence the presets had while the review window was unreleased.

Saved copies of ``preset + sentence`` must still count as "never customised", so they are
recognised next to the released texts and reset to ``""``.
"""

SUPERSEDED_PRESET_PROMPTS: dict[str, str] = {
    name: f"{PRESET_PROMPTS[name]} {sentence}"
    for name, sentence in _SUPERSEDED_SUGGESTION_SENTENCES.items()
}
"""Preset texts of the unreleased review-window builds (see above)."""


def is_preset_prompt_text(prompt: str) -> bool:
    """Whether *prompt* is (whitespace-normalised) a built-in preset of any version.

    Saved settings that contain a preset text are treated as a copy, not as custom
    instructions, so they can be reset to ``""`` ("use the built-in prompt").
    """
    normalized = normalize_prompt_text(prompt)
    if not normalized:
        return False
    return normalized in {
        normalize_prompt_text(text)
        for text in (
            *PRESET_PROMPTS.values(),
            *LEGACY_PRESET_PROMPTS.values(),
            *SUPERSEDED_PRESET_PROMPTS.values(),
        )
    }


def migrate_saved_prompt(prompt: str) -> str:
    """Return ``""`` when a saved *prompt* is a preset text of any version, else *prompt*.

    A preset saved as text by an older version must count as "use the built-in prompt
    for the document language" again: it must not be sent as a custom policy next to
    the answer rules, which it would contradict.
    """
    if is_preset_prompt_text(prompt):
        return ""
    return prompt


def migrate_saved_default(prompt: str, default: str) -> str:
    """Return ``""`` when a saved *prompt* is a copy of *default*, else *prompt*.

    For prompts without per-language presets (content review): ``""`` already means
    "use the built-in default", so a saved copy of it means the same.
    """
    if prompt.strip() and normalize_prompt_text(prompt) == normalize_prompt_text(default):
        return ""
    return prompt
