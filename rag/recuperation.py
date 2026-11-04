"""Récupération pour les agents LLM (étape 12) : ce que l'agent retrouve avant de décider (F1) et
ce qu'il retient après (A1).

Avant chaque décision, une seule question est formée à partir de sa situation (messages reçus,
événements, motif du jour) ; son vecteur sert à chercher dans trois sources : le corpus du monde
(filtré par date et par rôle), les souvenirs de l'agent (jours précédents seulement) et, depuis
l'étape 16, la mémoire collective (faits publics livrés avant son jour, dans sa portée sociale).
Chaque source a son quota ; le choix suit le score de `rag.selection` (pertinence, rétention,
importance, nouveauté, proximité, redondance pénalisée par la lassitude).

Après la décision, un souvenir est enregistré (ce qu'il a reçu, ce qu'il a décidé et pourquoi),
et ce qui a été montré au modèle est noté : les souvenirs montrés sont rappelés (leur rétention
repart à 1), et tout élément montré compte comme déjà vu pendant `memoire.nouveaute_jours`.
Sans cela, les mêmes documents généraux (fiche produit, questions fréquentes) revenaient à chaque
décision (essai du 18 oct.).

Consolidation (étape 16, décidé le 4 nov.) : à sa première décision d'une nouvelle semaine,
l'agent reçoit le bilan, calculé par règle, de chaque semaine passée où il a décidé. Épisodes et
bilans sont rendus avec la décision, que l'orchestrateur publie en `agent.souvenir` (texte en
clair dans le journal du run : ChromaDB n'est que l'index de recherche).
"""

from collections import Counter
from dataclasses import dataclass

from agents.base import Decision, Perception, SouvenirRetenu
from agents.roles import Role
from bus.events import Disturbance, NatureSouvenir
from llm.sortie import MOT_DU_SENS
from rag.base import BaseCorpus, Memoires, Passage
from rag.collectif import MemoireCollective
from rag.selection import Candidat, Historique, choisir
from sim.config import MondeConfig, RagConfig
from sim.domaine import Action, Sens, type_de_lieu

MESSAGES_DANS_LA_QUESTION = 3
MESSAGES_DANS_LE_SOUVENIR = 2
CARACTERES_PAR_MESSAGE = 70  # dans un souvenir : l'essentiel de ce qui a été dit
CARACTERES_RAISON = 110
JOURS_PAR_SEMAINE = 7
AUTEURS_DANS_LE_BILAN = 3


@dataclass(frozen=True)
class Contexte:
    passages: tuple[Passage, ...] = ()  # du plus au moins utile
    souvenirs: tuple[Passage, ...] = ()  # dans l'ordre des jours
    collectif: tuple[Passage, ...] = ()  # dans l'ordre des jours

    @property
    def sources(self) -> tuple[str, ...]:
        return tuple(p.source for p in self.passages + self.souvenirs + self.collectif)


def question_du_jour(perception: Perception, monde: MondeConfig) -> str:
    """Ce que l'agent cherche : les sujets de ses messages et des événements du jour, ou à défaut
    ce qu'il sait du produit dans le lieu où il se trouve."""
    morceaux = [f"{monde.brand_name} :"]
    morceaux += [m.payload.contenu for m in perception.messages[-MESSAGES_DANS_LA_QUESTION:]]
    morceaux += [e.payload.description for e in perception.evenements if isinstance(e, Disturbance)]
    if len(morceaux) == 1:
        zone = type_de_lieu(perception.zone).value
        morceaux.append(f"que faut-il savoir sur cette crème solaire ({zone}) ?")
    return " ".join(morceaux)


def _court(texte: str, n: int) -> str:
    """Texte ramené à `n` caractères au plus, coupé entre deux mots, avec une ellipse."""
    texte = " ".join(texte.split())
    if len(texte) <= n:
        return texte
    return texte[:n].rsplit(" ", 1)[0].rstrip(" ,;:.") + "…"


def souvenir(
    perception: Perception,
    decision: Decision,
    monde: MondeConfig,
    precedente: Decision | None = None,
) -> tuple[str, int]:
    """Texte du souvenir et son importance : 1, plus les messages reçus (3 au plus), plus 2 pour
    chaque moment marquant (un événement, un changement d'avis par rapport à `precedente`, un achat
    raté la veille).

    Souvenir compact (étape 15 ter) : qui a dit quoi, la décision et sa raison, en abrégé. Les
    souvenirs étaient la brique la plus lourde du prompt (452 caractères en médiane) et c'est
    eux que la réduction du prompt retirait : 40 % n'étaient jamais montrés au modèle en C1."""
    parties = [f"Jour {perception.jour}, {type_de_lieu(perception.zone).value}."]
    recus = perception.messages[-MESSAGES_DANS_LE_SOUVENIR:]
    if recus:
        parties.append(
            "Reçu : "
            + " ; ".join(
                f"{m.source_agent_id} « {_court(m.payload.contenu, CARACTERES_PAR_MESSAGE)} »"
                for m in recus
            )
            + "."
        )
    evenements = [e for e in perception.evenements if isinstance(e, Disturbance)]
    if evenements:
        parties.append(
            "Événement : "
            + " ; ".join(_court(e.payload.description, CARACTERES_PAR_MESSAGE) for e in evenements)
            + "."
        )
    parties.append(
        f"J'ai décidé : {decision.action.value}, {monde.brand_name} « "
        f"{MOT_DU_SENS[decision.sens]} », car {_court(decision.justification, CARACTERES_RAISON)}"
    )
    if decision.messages:
        m = decision.messages[0]
        parties.append(f"J'ai dit : « {_court(m.contenu, CARACTERES_PAR_MESSAGE)} ».")
    change = precedente is not None and (
        precedente.sens is not decision.sens or precedente.porteur != decision.porteur
    )
    marquants = bool(evenements) + change + perception.achat_rate
    importance = 1 + len(perception.messages[-3:]) + 2 * marquants
    return " ".join(parties), importance


@dataclass(frozen=True)
class Trace:
    """Ce qu'une décision laisse au bilan de sa semaine (consolidation, étape 16)."""

    jour: int
    action: Action
    sens: Sens
    porteur: bool
    auteurs: tuple[str, ...]  # auteurs des messages reçus
    evenements: tuple[str, ...]  # descriptions des perturbations perçues
    importance: int


def semaine(jour: int) -> int:
    """Semaine simulée du jour : jours 1 à 7 en semaine 1."""
    return (jour - 1) // JOURS_PAR_SEMAINE + 1


def bilan_de_semaine(numero: int, traces: list[Trace], monde: MondeConfig) -> tuple[str, int]:
    """Bilan d'une semaine passée, par règle (M6, décidé le 4 nov.) : ce que l'agent a fait, le
    sens qu'il a le plus souvent donné au produit, ceux qui lui en ont le plus parlé. Son
    importance est celle du moment le plus marquant de la semaine. Sans LLM : gratuit, rejouable."""
    fin = numero * JOURS_PAR_SEMAINE
    parties = [
        f"Semaine {numero} (jours {fin - JOURS_PAR_SEMAINE + 1} à {fin}) : "
        f"{len(traces)} décision{'s' if len(traces) > 1 else ''}."
    ]
    actions = list(dict.fromkeys(t.action.value for t in traces))  # dans l'ordre, sans doublon
    parties.append(f"J'ai décidé : {', '.join(actions)}.")
    sens = Counter(t.sens for t in traces)
    ((dominant, _),) = sens.most_common(1)  # à égalité, le premier donné dans la semaine
    parties.append(f"{monde.brand_name} m'a surtout paru « {MOT_DU_SENS[dominant]} ».")
    parties.append("Je l'utilise." if traces[-1].porteur else "Je ne l'utilise pas.")
    auteurs = Counter(a for t in traces for a in t.auteurs)
    if auteurs:
        principaux = sorted(auteurs, key=lambda a: (-auteurs[a], a))[:AUTEURS_DANS_LE_BILAN]
        cites = ", ".join(f"{a} ({auteurs[a]})" for a in principaux)
        parties.append(f"Ceux qui m'en ont parlé : {cites}.")
    evenements = list(dict.fromkeys(e for t in traces for e in t.evenements))
    if evenements:
        parties.append(
            "Événements : "
            + " ; ".join(_court(e, CARACTERES_PAR_MESSAGE) for e in evenements[:2])
            + "."
        )
    return " ".join(parties), max(t.importance for t in traces)


def _par_jour(choisis: list[Candidat]) -> tuple[Passage, ...]:
    return tuple(sorted((c.passage for c in choisis), key=lambda p: (p.jour, p.source)))


class Recuperateur:
    def __init__(
        self,
        corpus: BaseCorpus,
        memoires: Memoires,
        config: RagConfig,
        monde: MondeConfig,
        collectif: MemoireCollective | None = None,
    ) -> None:
        self.corpus = corpus
        self.memoires = memoires
        self.collectif = collectif  # None : pas de mémoire collective (tests, runs d'avant)
        self.config = config
        self.monde = monde
        self._historiques: dict[str, Historique] = {}  # ce qui a été montré à chaque agent
        self._candidats: dict[str, dict[str, Candidat]] = {}  # derniers choix de chaque agent
        self._traces: dict[str, list[Trace]] = {}  # décisions pas encore consolidées

    async def consolider(self, agent_id: str, jour: int) -> tuple[SouvenirRetenu, ...]:
        """Bilan de chaque semaine terminée avant `jour` où l'agent a décidé (M6), daté de son
        dernier jour : il se lit comme un souvenir à partir de la semaine suivante (C6). Les
        épisodes restent en mémoire et s'oublient à leur rythme."""
        traces = self._traces.get(agent_id, [])
        finies = sorted({semaine(t.jour) for t in traces if semaine(t.jour) < semaine(jour)})
        retenus = []
        for numero in finies:
            de_la_semaine = [t for t in traces if semaine(t.jour) == numero]
            texte, importance = bilan_de_semaine(numero, de_la_semaine, self.monde)
            fin = numero * JOURS_PAR_SEMAINE
            source = await self.memoires.ajouter(agent_id, fin, texte, importance, "bilan")
            retenus.append(SouvenirRetenu(source, NatureSouvenir.BILAN, fin, texte, importance))
        if finies:  # après les écritures : un échec les refera (même identifiant, écrasé)
            self._traces[agent_id] = [t for t in traces if semaine(t.jour) >= semaine(jour)]
        return tuple(retenus)

    async def contexte(
        self,
        agent_id: str,
        role: Role,
        perception: Perception,
        abonnements: tuple[str, ...] | None = None,
        contacts: list[str] | tuple[str, ...] = (),
    ) -> Contexte:
        """Ce que l'agent retrouve aujourd'hui, source par source, selon le score de
        `rag.selection`. `abonnements` et `contacts` fixent sa portée sociale (mémoire
        collective) ; sa lassitude et sa notoriété viennent de la perception."""
        (vecteur,) = await self.corpus.embeddeur.questions(
            [question_du_jour(perception, self.monde)]
        )
        reglage, jour = self.config.memoire, perception.jour
        historique = self._historiques.setdefault(agent_id, Historique())

        def garder(candidats: list[Candidat], k: int) -> list[Candidat]:
            return choisir(
                candidats, k, jour, reglage, historique, perception.lassitude, perception.notoriete
            )

        passages = garder(self.corpus.candidats(vecteur, jour, role, reglage), self.config.passages)
        souvenirs: list[Candidat] = []
        if self.config.souvenirs:
            candidats = self.memoires.candidats(vecteur, agent_id, jour, reglage)
            souvenirs = garder(candidats, self.config.souvenirs)
        collectif: list[Candidat] = []
        if self.collectif is not None and self.config.collectif:
            percus = {str(x.event_id) for x in (*perception.messages, *perception.evenements)}
            candidats = self.collectif.candidats(
                vecteur,
                agent_id=agent_id,
                jour=jour,
                lieu=perception.zone,
                abonnements=abonnements,
                contacts=list(contacts),
                deja_percus=percus,
                reglage=reglage,
            )
            collectif = garder(candidats, self.config.collectif)
        self._candidats[agent_id] = {
            c.passage.source: c for c in (*passages, *souvenirs, *collectif)
        }
        return Contexte(
            tuple(c.passage for c in passages), _par_jour(souvenirs), _par_jour(collectif)
        )

    async def retenir(
        self,
        agent_id: str,
        perception: Perception,
        decision: Decision,
        precedente: Decision | None = None,
    ) -> SouvenirRetenu:
        """Enregistre le souvenir de la décision, et ce qui a été montré au modèle (ses
        `sources`, après la réduction du prompt) : déjà vu, et souvenirs rappelés. La décision
        attend le bilan de sa semaine (`consolider`)."""
        candidats = self._candidats.pop(agent_id, {})
        montres = [candidats[s] for s in decision.sources if s in candidats]
        self._historiques.setdefault(agent_id, Historique()).noter(perception.jour, montres)
        self.memoires.rappeler(
            [c.passage.source for c in montres if c.passage.type == "souvenir"], perception.jour
        )
        texte, importance = souvenir(perception, decision, self.monde, precedente)
        source = await self.memoires.ajouter(agent_id, perception.jour, texte, importance)
        self._traces.setdefault(agent_id, []).append(
            Trace(
                perception.jour,
                decision.action,
                decision.sens,
                decision.porteur,
                tuple(m.source_agent_id for m in perception.messages),
                tuple(
                    e.payload.description
                    for e in perception.evenements
                    if isinstance(e, Disturbance)
                ),
                importance,
            )
        )
        return SouvenirRetenu(source, NatureSouvenir.EPISODE, perception.jour, texte, importance)
