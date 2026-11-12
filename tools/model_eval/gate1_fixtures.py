"""The texts gate 1 sends: invented fixtures, one per language, notation and length.

Data only.  The texts are invented for the check, never a corpus text, so they can live in the
repository; ``test_structured_check.py`` pins the text of the full page, because saved answers
(``gate1_answers``) were made on this exact wording and a changed fixture would make them
unjudgeable (their ``text_sha256`` no longer matches).  What is done with a fixture — building the
text that is sent, judging an answer — is :mod:`.structured_check`.
"""

from __future__ import annotations

from dataclasses import dataclass

from teachers_teammate.domain.formatted_text import TextFormat


@dataclass(frozen=True)
class Sentence:
    """One sentence of a fixture, and whether a proofreader must change it."""

    text: str
    error: bool = False


@dataclass(frozen=True)
class Fixture:
    """One text the check sends, with the language and notation it needs."""

    name: str
    language: str
    """The language *name*, as the app's corrector is asked for it."""
    paragraphs: tuple[tuple[Sentence, ...], ...]
    text_format: TextFormat = "plain"
    long: bool = False
    """Whether the coverage verdict applies: the text is long enough to be answered in part."""
    fill_budget: bool = False
    """Whether the text is sent as the longest prefix of whole sentences that fits the budget."""


FULL_PAGE_DE: tuple[tuple[Sentence, ...], ...] = (
    (
        Sentence(
            "Am letzten Wochenende ich bin mit meine Klasse auf ein Ausflug in die Berge gefahren.",
            error=True,
        ),
        Sentence(
            "Wir haben uns um sieben Uhr am Bahnhof getroffen und der Lehrer hat die"
            " Fahrkarten für alle gekauft."
        ),
        Sentence(
            "Weil wir haben lange warten müssen, war der Zug schon voll und wir mussten stehen.",
            error=True,
        ),
        Sentence(
            "Nach zwei Stunden wir sind endlich angekommen und die Sonne hat geschienen,"
            " obwohl der Wetterbericht hatte Regen angesagt.",
            error=True,
        ),
    ),
    (
        Sentence(
            "Zuerst sind wir ein steilen Weg hinauf gegangen, wo man aufpassen muss, weil die"
            " Steine locker liegen.",
            error=True,
        ),
        Sentence(
            "Meine Freundin ist hingefallen und hat sich das Knie verletzt, aber sie hat"
            " gesagt, das tut nicht so weh."
        ),
        Sentence("Auf dem Gipfel war es windig und wir haben unsere Jacken angezogen."),
        Sentence(
            "Von oben wir konnten das ganze Tal sehen und die kleinen Häuser sahen wie"
            " Spielzeug aus.",
            error=True,
        ),
        Sentence("Ein Mitschüler hat ein Fernrohr mitgebracht und wir haben die Vögel beobachtet."),
    ),
    (
        Sentence(
            "Zum Mittagessen wir hatten Brote und Äpfel dabei, aber die Butter war in der"
            " Sonne weich geworden.",
            error=True,
        ),
        Sentence("Danach haben wir ein Spiel gespielt, bei dem man sich die Namen merken muss."),
        Sentence("Es hat viel Spass gemacht und alle haben gelacht.", error=True),
        Sentence(
            "Der Lehrer hat gesagt, dass wir leise sein sollen, damit die anderen Wanderer"
            " nicht gestört werden."
        ),
    ),
    (
        Sentence("Auf dem Rückweg hat es angefangen zu regnen und wir sind schnell gelaufen."),
        Sentence(
            "Weil der Weg nass war, ist eine Mädchen ausgerutscht, aber es ist nichts passiert.",
            error=True,
        ),
        Sentence("Im Zug haben wir müde aus dem Fenster geschaut und manche sind eingeschlafen."),
        Sentence(
            "Als wir zu Hause angekommen sind, hat meine Mutter gefragt, wie es war, und ich"
            " habe gesagt, es war ein sehr schöner Tag."
        ),
        Sentence("Am nächsten Tag ich hatte Muskelkater, aber das war es wert.", error=True),
    ),
    (
        Sentence(
            "In der Schule haben wir danach über den Ausflug gesprochen und jeder hat einen"
            " kleinen Text darüber geschrieben."
        ),
        Sentence(
            "Mir hat am besten gefallen, wie wir zusammen den steilen Weg geschafft haben,"
            " weil keiner aufgegeben hat."
        ),
        Sentence(
            "Nächstes Jahr ich möchte wieder mitkommen, vielleicht sogar im Winter, wenn"
            " Schnee liegt.",
            error=True,
        ),
        Sentence(
            "Der Lehrer hat gesagt, dass er uns dann früher Bescheid gibt, damit alle planen"
            " können."
        ),
    ),
)
"""One invented page of dense German prose, with the sentences a proofreader must change.

Ten of its 22 sentences carry an error, two of them (18 and 21) in the last third, which is
what the coverage verdict needs.  Only the error flags matter here: a model that also touches a
correct sentence is not judged by gate 1 (gate 2 measures "left alone").
"""


PAGE_DE_AT_BUDGET: tuple[tuple[Sentence, ...], ...] = (
    (
        Sentence(
            "In den letzten zwei Wochen habe ich ein Praktikum in einer kleinen Firma gemacht."
        ),
        Sentence("Die Firma stellt Möbel her und hat etwa zwanzig Mitarbeiter."),
        Sentence("Am ersten Tag ich war sehr nervös, weil ich niemanden kannte.", error=True),
        Sentence("Meine Mutter hat mich morgens mit dem Auto zur Arbeit gefahren."),
        Sentence(
            "Vor der Tür habe ich noch ein paar Minuten gewartet, weil ich war zu früh.", error=True
        ),
        Sentence("Die Firma liegt in ein alten Gebäude.", error=True),
        Sentence("Ich musste jeden Morgen um Viertel vor sieben losfahren."),
    ),
    (
        Sentence("Der Chef hat mich begrüßt und mir das ganze Gebäude gezeigen.", error=True),
        Sentence("Zuerst habe ich die Werkstatt gesehen, wo die Möbel gebaut werden."),
        Sentence("Weil die Maschinen sehr laut waren, konnte ich kaum verstehen, was er sagte."),
        Sentence("Er hat mir erklärt, dass ich immer einen Helm tragen muss."),
        Sentence("Auf ein Stuhl im Büro habe ich dann die Papiere sortiert.", error=True),
        Sentence("Die Werkstatt riecht nach Holz und nach Öl."),
        Sentence("Am Nachmittag ich habe die anderen Abteilungen gesehen.", error=True),
    ),
    (
        Sentence("Die Kollegen in der Werkstatt waren sehr freundlich zu mir."),
        Sentence("Ein älterer Mann hat mir gezeigt, wie man die Maschine einschaltet."),
        Sentence("Ich habe die Maschine angeschalten und sofort wieder ausgeschaltet.", error=True),
        Sentence("Alle haben gelacht, aber niemand hat böse gewesen.", error=True),
        Sentence("Danach durfte ich nur noch zuschauen und viele Fragen stellen."),
        Sentence("Der Mann hat mir geduldig alles erklärt und mir Mut gemacht."),
        Sentence("Ich habe mich danach viel sicherer gefühlt wie am Anfang.", error=True),
    ),
    (
        Sentence("In der Mittagspause haben wir zusammen in der kleinen Küche gegessen."),
        Sentence("Die Kolleginnen haben mir viele Fragen über die Schule gestellt."),
        Sentence("Ich habe erzählt, das ich im nächsten Jahr mein Abitur machen will.", error=True),
        Sentence("Eine Frau hat gesagt, dass sie früher auch in meiner Schule war."),
        Sentence("Das Gespräch hat mir geholfen, weil ich danach hatte weniger Angst.", error=True),
        Sentence("Das Essen in der Kantine war einfach, aber sehr gut."),
        Sentence("Nach dem Essen haben wir noch ein Kaffee getrunken.", error=True),
    ),
    (
        Sentence("Am zweiten Tag musste ich schon um sechs Uhr aufstehen."),
        Sentence("Der Bus war so voll, dass ich musste die ganze Fahrt stehen.", error=True),
        Sentence("Im Büro habe ich dann die Post geöffnet und sortiert."),
        Sentence("Das Firma hat jeden Tag sehr viele Briefe bekommen.", error=True),
        Sentence("Die Briefe habe ich in verschiedene Fächer gelegt."),
        Sentence("Die Sekretärin hat mir gezeigt, wie funktioniert das Telefon.", error=True),
        Sentence("Ich durfte aber nicht selbst telefonieren."),
    ),
    (
        Sentence("Mein Betreuer hat mir eine Aufgabe gegeben, die mir viel Freude gemacht hat."),
        Sentence("Ich sollte ein Liste von allen Werkzeugen in der Werkstatt machen.", error=True),
        Sentence("Weil ich die Namen nicht kannte, habe ich viele Fragen gestellt."),
        Sentence("Ich habe die Liste auf der Computer geschrieben.", error=True),
        Sentence("Am Ende war die Liste fertig und mein Betreuer war zufrieden."),
        Sentence("Am Abend habe ich die Liste noch einmal geprüft."),
        Sentence("Mein Betreuer hat gesagt, das ich sehr genau gearbeitet habe.", error=True),
    ),
    (
        Sentence("Am dritten Tag habe ich in der Werkstatt mitgeholfen."),
        Sentence("Wir haben zusammen ein großen Schrank gebaut.", error=True),
        Sentence("Mein Kollege hat mir gezeigt, wie man die Bretter zuschneidet."),
        Sentence("Ich habe die Bretter mit ein Pinsel angemalt.", error=True),
        Sentence("Die Farbe war am Anfang noch nass und ich musste lange warten."),
        Sentence(
            "Der Schrank war fast zwei Meter hoch und wir haben ihn in den Verkaufsraum getragen."
        ),
        Sentence("Ich habe dabei geholfen, obwohl ich war sehr müde.", error=True),
    ),
    (
        Sentence("In der zweiten Woche durfte ich im Büro arbeiten."),
        Sentence("Dort habe ich gelernt, wie man Rechnungen schreibt und ablegt."),
        Sentence("Die Kolleginnen war sehr geduldig mit mir.", error=True),
        Sentence("Ich habe viele Fehler gemacht, aber niemand hat sich geärgert."),
        Sentence("Nach zwei Tagen ich habe die Rechnungen allein geschrieben.", error=True),
        Sentence("Die Rechnungen mussten alle am selben Tag verschicken werden.", error=True),
        Sentence("Ich habe dabei gelernt, wie wichtig genaue Zahlen sind."),
    ),
    (
        Sentence("Am Donnerstag hat der Chef mich in sein Büro gerufen."),
        Sentence("Ich hatte Angst, dass ich etwas falsch gemacht hatte."),
        Sentence("Er hat mir aber nur Fragen über die Schule gestellen.", error=True),
        Sentence("Er wollte wissen, ob ich später auch in dieser Firma arbeiten möchte."),
        Sentence("Ich habe gesagt, das ich mir das gut vorstellen kann.", error=True),
        Sentence(
            "Er hat mir außerdem eine Stelle für die Ausbildung angeboten, weil er war zufrieden.",
            error=True,
        ),
        Sentence("Ich habe mich sehr darüber gefreut."),
    ),
    (
        Sentence("Am Freitag haben wir ein Fest für einen Kollegen gefeiert."),
        Sentence("Er hat seit zwanzig Jahren in der Firma gearbeiten.", error=True),
        Sentence("Weil wir hatten viel zu tun, haben wir erst um drei Uhr angefangen.", error=True),
        Sentence("Es gab Kuchen und alle haben gelacht."),
        Sentence("Der Kollege hat sich über unsere Geschenke sehr gefreut."),
        Sentence(
            "Der Kollege hat eine kleine Rede gehalten und wir haben ihm ein Geschenk überreichen.",
            error=True,
        ),
        Sentence("Danach haben wir noch lange zusammengesessen."),
    ),
    (
        Sentence("In der letzten Woche habe ich ein eigenes Projekt bekommen."),
        Sentence("Ich sollte ein kleines Regal bauen, ohne Hilfe von den Kollegen."),
        Sentence("Zuerst habe ich die Pläne nicht verstehen.", error=True),
        Sentence("Nach einer Stunde ich habe endlich angefangen.", error=True),
        Sentence("Das Regal ist am Ende gerade geworden und es hat mir gefallen."),
        Sentence("Ich habe die Pläne noch einmal genau angeschaut."),
        Sentence("Dann habe ich die Bretter zugeschnitten und zusammen gesetzt.", error=True),
    ),
    (
        Sentence("Mein Betreuer hat gesagt, dass ich sehr gut gearbeitet habe."),
        Sentence("Das hat mich sehr gefreut und ich habe richtig stolz gewesen.", error=True),
        Sentence("Er hat mir angeboten, im Sommer wieder zu kommen."),
        Sentence("Ich habe mich bei alle Kollegen bedankt.", error=True),
        Sentence("Sie haben gesagt, dass ich jederzeit willkommen bin."),
        Sentence("Er hat mir auch ein gutes Zeugnis versprechen.", error=True),
        Sentence("Das Zeugnis habe ich später wirklich bekommen."),
        Sentence("Ich habe mich bei ihm herzlich bedankt."),
    ),
    (
        Sentence("Am letzten Tag habe ich allen Kollegen gedankt."),
        Sentence("Ich habe ein kleines Geschenk bekommt.", error=True),
        Sentence("Der Chef hat gesagt, dass ich jederzeit wieder kommen kann."),
        Sentence("Ich habe versprochen, das ich mich bald melde.", error=True),
        Sentence("Dann habe ich meine Sachen gepackt und bin gegangen."),
        Sentence("Die Kollegen haben mir noch viel Glück gewunschen.", error=True),
        Sentence("Ich habe versprochen, im Sommer wieder vorbeizukommen."),
        Sentence("Am Abend haben wir noch zusammen gegessen."),
    ),
    (
        Sentence("Zu Hause habe ich meinen Eltern alles erzählt."),
        Sentence("Sie waren überrascht, wie viel ich in zwei Wochen gelernt hatte."),
        Sentence("Meine Mutter hat gesagt, das ich jetzt erwachsener wirke.", error=True),
        Sentence("Die Arbeit hat mir gefallen, aber ich habe vermisst meine Freunde.", error=True),
        Sentence("Mein Vater hat nur gelacht und nichts gesagt."),
        Sentence(
            "Sie haben mir viele Fragen gestellt und ich habe ihnen alles genau erklären.",
            error=True,
        ),
        Sentence("Danach war ich sehr müde und bin früh ins Bett gegangen."),
    ),
    (
        Sentence("In der Schule habe ich einen Bericht über das Praktikum geschrieben."),
        Sentence("Meine Lehrerin hat ihn sehr gut gefunden."),
        Sentence("Sie hat gesagt, dass ich gut beobachtet habe."),
        Sentence("Über die Erfahrungen ich möchte später mehr erzählen.", error=True),
        Sentence("Einige Mitschüler haben auch ein Praktikum in eine Firma gemacht.", error=True),
        Sentence("Meine Lehrerin hat den Bericht vor der Klasse vorlesen.", error=True),
        Sentence("Das war mir ein bisschen peinlich."),
    ),
    (
        Sentence("Das Praktikum hat mir sehr gut gefallen."),
        Sentence("Ich habe viel gelernt und mit viele nette Menschen gesprochen.", error=True),
        Sentence("Am Anfang war ich unsicher, aber das hat sich schnell geändert."),
        Sentence(
            "Ich kann jedem empfehlen, ein Praktikum zu machen, weil man lernt viel über sich selbst.",
            error=True,
        ),
        Sentence("Für mich war es eine wichtige Erfahrung."),
        Sentence("Vielleicht ich bewerbe mich dort für eine Ausbildung.", error=True),
        Sentence("Ich würde das Praktikum auf jeden Fall wieder machen."),
    ),
)

"""An invented German page of two weeks of work experience, with the sentences to change.

It is 6,533 characters in 16 paragraphs of 367 to 440, with sentences of at most 88 characters,
and 48 of its 114 sentences carry an error: about half, so every prefix of it ends in errors.
The errors are the ones a proofreader must find in standard German (verb second, verb last in a
subordinate clause, case after a preposition, article gender, agreement, participles,
*dass*/*das*), nothing regional and nothing a style preset could decide either way.  One
sentence ("sicherer gefühlt wie am Anfang") is the colloquial *wie* for *als*; it stays because
the recorded answers were made on this exact text, and one miss is within the 80 % coverage line.
It is sent as the longest prefix of whole sentences the app's budget allows, so the gate tests
the size the app really sends.  Like the full page it is invented for this check, never a corpus
text, so it can live in the repository.
"""


FIXTURES: tuple[Fixture, ...] = (
    Fixture(
        name="plain EN",
        language="english",
        paragraphs=(
            (
                Sentence("Yesterday she have gone to the cinema with his friend."),
                Sentence("It was very nice because they was there for the first time."),
            ),
        ),
    ),
    Fixture(
        name="plain DE",
        language="german",
        paragraphs=(
            (
                Sentence("Gestern hat sie ins Kino gegangen mit sein Freundin."),
                Sentence("Es war sehr schön weil sie war dort zum ersten Mal."),
            ),
        ),
    ),
    Fixture(
        name="plain FR",
        language="french",
        paragraphs=(
            (
                Sentence("Hier elle a aller au cinéma avec son ami."),
                Sentence("C'était très bien parce que ils était là pour la première fois."),
            ),
        ),
    ),
    Fixture(
        name="Markdown",
        language="english",
        paragraphs=(
            (
                Sentence(
                    "Dear **diary**, my ~~backpack~~ passport and me went to school.\n\n"
                    "- it was *very* nice\n- we have see a film"
                ),
            ),
        ),
        text_format="markdown",
    ),
    Fixture(name="full page DE", language="german", paragraphs=FULL_PAGE_DE, long=True),
    Fixture(
        name="page DE at budget",
        language="german",
        paragraphs=PAGE_DE_AT_BUDGET,
        long=True,
        fill_budget=True,
    ),
)
