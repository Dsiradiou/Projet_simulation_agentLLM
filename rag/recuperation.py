"""Récupération pour les agents LLM (étape 12) : ce que l'agent retrouve avant de décider (F1) et
ce qu'il retient après (A1).

Avant chaque décision, une seule question est formée à partir de sa situation (messages reçus,
événements, motif du jour) ; son vecteur sert à chercher à la fois dans le corpus du monde (filtré
par date et par rôle) et dans les souvenirs de l'agent (jours précédents seulement). Après la
décision, un souvenir est enregistré : ce qu'il a reçu, ce qu'il a décidé et pourquoi.

Nouveauté : parmi les passages les plus proches, l'agent voit d'abord ceux qu'il n'a pas encore
lus, avec au plus un document déjà vu. Sans cela, les mêmes documents généraux (fiche produit,
questions fréquentes) revenaient à chaque décision (essai du 18 oct.).
"""

from dataclasses import dataclass

from agents.base import Decision, Perception
from agents.roles import Role
from bus.events import Disturbance
from llm.sortie import MOT_DU_SENS
from rag.base import BaseCorpus, Memoires, Passage
from sim.config import MondeConfig, RagConfig
from sim.domaine import type_de_lieu

MESSAGES_DANS_LA_QUESTION = 3
MESSAGES_DANS_LE_SOUVENIR = 3
DEJA_VUS_MAX = 1


@dataclass(frozen=True)
class Contexte:
    passages: tuple[Passage, ...] = ()
    souvenirs: tuple[Passage, ...] = ()

    @property
    def sources(self) -> tuple[str, ...]:
        return tuple(p.source for p in self.passages + self.souvenirs)


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


def souvenir(perception: Perception, decision: Decision, monde: MondeConfig) -> tuple[str, int]:
    """Texte du souvenir et son importance (messages reçus, changement d'avis, événement)."""
    parties = [f"Jour {perception.jour}, {type_de_lieu(perception.zone).value}."]
    recus = perception.messages[-MESSAGES_DANS_LE_SOUVENIR:]
    if recus:
        parties.append(
            "Messages reçus : "
            + " ; ".join(f"{m.source_agent_id} a dit « {m.payload.contenu[:120]} »" for m in recus)
            + "."
        )
    evenements = [e for e in perception.evenements if isinstance(e, Disturbance)]
    if evenements:
        parties.append("Événement : " + " ; ".join(e.payload.description for e in evenements) + ".")
    parties.append(
        f"J'ai décidé : {decision.action.value} ; {monde.brand_name} me semblait "
        f"« {MOT_DU_SENS[decision.sens]} ». Ma raison : {decision.justification[:200]}"
    )
    if decision.messages:
        m = decision.messages[0]
        parties.append(f"J'ai dit ({m.canal.value}) : « {m.contenu[:120]} ».")
    importance = 1 + len(recus) + 2 * bool(evenements)
    return " ".join(parties), importance


class Recuperateur:
    def __init__(
        self, corpus: BaseCorpus, memoires: Memoires, config: RagConfig, monde: MondeConfig
    ) -> None:
        self.corpus = corpus
        self.memoires = memoires
        self.config = config
        self.monde = monde
        self._lus: dict[str, set[str]] = {}  # documents du corpus déjà montrés à chaque agent

    async def contexte(self, agent_id: str, role: Role, perception: Perception) -> Contexte:
        (vecteur,) = await self.corpus.embeddeur.questions(
            [question_du_jour(perception, self.monde)]
        )
        candidats = self.corpus.rechercher_vecteur(
            vecteur, perception.jour, role, 3 * self.config.passages
        )
        passages = self._choisir(agent_id, candidats)
        souvenirs = (
            self.memoires.rechercher_vecteur(
                vecteur, agent_id, perception.jour, self.config.souvenirs
            )
            if self.config.souvenirs
            else []
        )
        return Contexte(tuple(passages), tuple(sorted(souvenirs, key=lambda p: p.jour)))

    def _choisir(self, agent_id: str, candidats: list[Passage]) -> list[Passage]:
        """Les plus proches d'abord, en privilégiant ce que l'agent n'a pas encore lu."""
        lus = self._lus.setdefault(agent_id, set())
        nouveaux = [p for p in candidats if p.source not in lus]
        deja_vus = [p for p in candidats if p.source in lus][:DEJA_VUS_MAX]
        choisis = (nouveaux + deja_vus)[: self.config.passages]
        if len(choisis) < self.config.passages:  # tout a été lu : on complète avec le déjà-vu
            choisis += [p for p in candidats if p not in choisis][
                : self.config.passages - len(choisis)
            ]
        choisis.sort(key=lambda p: p.distance)
        lus.update(p.source for p in choisis)
        return choisis

    async def retenir(self, agent_id: str, perception: Perception, decision: Decision) -> str:
        texte, importance = souvenir(perception, decision, self.monde)
        return await self.memoires.ajouter(agent_id, perception.jour, texte, importance)
