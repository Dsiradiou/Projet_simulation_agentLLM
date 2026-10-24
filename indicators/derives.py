"""Dérives des agents LLM (étape 14, A6 et O2) : détectées dans le texte des décisions publiées.

Une décision LLM est dite « en dérive » si au moins un de ces signes y apparaît :

- `incoherence` : l'action contredit le sens déclaré (acheter un produit démodé, critiquer un
  produit jugé efficace...). « banal » avec « acheter » reste permis : on achète un produit
  comme un autre sans enthousiasme ;
- `invention_personne` : la justification ou le message cite un habitant (`pharmacien_02`) qui
  ne lui a jamais écrit et à qui il n'a jamais écrit (constat de l'étape 7) ;
- `sante` : vocabulaire médical, alors que le produit est un cosmétique et que le corpus n'en
  contient pas (constat de l'étape 12 : « effets secondaires ») ;
- `langue` : caractères d'un autre alphabet (constat de l'étape 6 : caractères chinois du 1.7b) ;
- `assistant` : formule d'assistant conversationnel (« n'hésitez pas », « en tant qu'IA »,
  vouvoiement de conseil) au lieu d'un habitant qui parle.

Ces signes sont des heuristiques : le taux mesuré est une borne basse, qui se compare d'une
configuration à l'autre (C1, C2, C3) et se vérifie à la main sur un échantillon (rapport).
Les correspondants d'un agent viennent des seuls événements : expéditeurs des messages que ses
décisions citent (`caused_by`) et destinataires des messages qu'il a envoyés.
"""

import re
from dataclasses import dataclass, field
from enum import StrEnum
from uuid import UUID

from bus import events as ev
from sim.domaine import Action, Sens


class Derive(StrEnum):
    INCOHERENCE = "incoherence"
    INVENTION_PERSONNE = "invention_personne"
    SANTE = "sante"
    LANGUE = "langue"
    ASSISTANT = "assistant"


INCOHERENTES: dict[Sens, frozenset[Action]] = {
    Sens.DEPASSE: frozenset({Action.ACHETER, Action.APPLIQUER, Action.RECOMMANDER}),
    Sens.BANAL: frozenset({Action.RECOMMANDER}),
    Sens.EFFICACE: frozenset({Action.CRITIQUER}),
}

IDENTIFIANT = re.compile(r"\b([a-z]+(?:_[a-z]+)*_\d{2})\b")
SANTE = re.compile(
    r"effets? secondaires?|allergi|irritat|ecz[ée]ma|cancer|m[ée]lanome|dermatolog|m[ée]decin"
    r"|m[ée]dical|m[ée]dicament|maladie|toxi(?:que|cit)|perturbateur|gu[ée]ri|th[ée]rapeuti",
    re.IGNORECASE,
)
# Cyrillique, arabe, hébreu, kana, idéogrammes CJK, hangul.
AUTRE_ALPHABET = re.compile(r"[Ѐ-ӿ֐-ۿ぀-ヿ㐀-鿿가-힯]")
ASSISTANT = re.compile(
    r"en tant qu.{0,3}(?:ia|intelligence|assistant|mod[eè]le)|je suis une? (?:ia|assistant)"
    r"|intelligence artificielle|n.h[ée]sitez pas|je vous (?:recommande|conseille|sugg[eè]re)"
    r"|consultez|il est (?:important|recommand[ée]|conseill[ée]) de|en r[ée]sum[ée]"
    r"|je ne peux pas (?:vous|r[ée]pondre)",
    re.IGNORECASE,
)


def detecter(
    action: Action,
    sens: Sens,
    textes: list[str],
    auteur: str,
    correspondants: set[str],
    habitants: set[str],
) -> list[Derive]:
    """Signes de dérive d'une décision. `textes` : justification et message éventuel ;
    `habitants` : identifiants connus du run (un nom de rôle seul n'est pas un habitant)."""
    derives = []
    if action in INCOHERENTES.get(sens, ()):
        derives.append(Derive.INCOHERENCE)
    texte = "\n".join(textes)
    cites = {m for m in IDENTIFIANT.findall(texte) if m in habitants}
    if cites - correspondants - {auteur}:
        derives.append(Derive.INVENTION_PERSONNE)
    if SANTE.search(texte):
        derives.append(Derive.SANTE)
    if AUTRE_ALPHABET.search(texte):
        derives.append(Derive.LANGUE)
    if ASSISTANT.search(texte):
        derives.append(Derive.ASSISTANT)
    return derives


@dataclass
class Signalement:
    decision_id: UUID
    agent_id: str
    jour: int
    derives: list[Derive]
    texte: str


@dataclass
class Detecteur:
    """Suit les correspondants de chaque agent et signale les dérives des décisions LLM d'un
    jour. Les messages sont publiés juste après la décision qui les cause : on examine donc les
    décisions du jour à sa fin, avec leurs messages."""

    habitants: set[str] = field(default_factory=set)
    correspondants: dict[str, set[str]] = field(default_factory=dict)
    _messages: dict[UUID, ev.AgentMessage] = field(default_factory=dict)  # tous les messages
    _decisions_du_jour: list[ev.AgentDecision] = field(default_factory=list)
    _message_de: dict[UUID, list[ev.AgentMessage]] = field(default_factory=dict)  # par décision

    def appliquer(self, event: ev.Envelope) -> None:
        match event:
            case ev.AgentDecision():
                self.habitants.add(event.source_agent_id)
                cor = self.correspondants.setdefault(event.source_agent_id, set())
                for cause in event.caused_by:
                    if cause in self._messages:
                        cor.add(self._messages[cause].source_agent_id)
                if event.payload.moteur is ev.Moteur.LLM:
                    self._decisions_du_jour.append(event)
            case ev.AgentMessage():
                self._messages[event.event_id] = event
                self.correspondants.setdefault(event.source_agent_id, set()).update(
                    event.payload.destinataires
                )
                for cause in event.caused_by:
                    self._message_de.setdefault(cause, []).append(event)

    def fin_du_jour(self, jour: int) -> tuple[int, list[Signalement]]:
        """Décisions LLM du jour examinées, et celles qui présentent au moins une dérive."""
        signalements = []
        examinees = len(self._decisions_du_jour)
        for d in self._decisions_du_jour:
            messages = [m.payload.contenu for m in self._message_de.get(d.event_id, [])]
            textes = [d.payload.justification, *messages]
            derives = detecter(
                d.payload.action,
                d.payload.sens,
                textes,
                d.source_agent_id,
                self.correspondants.get(d.source_agent_id, set()),
                self.habitants,
            )
            if derives:
                signalements.append(
                    Signalement(d.event_id, d.source_agent_id, jour, derives, " / ".join(textes))
                )
        self._decisions_du_jour.clear()
        return examinees, signalements
