"""Corpus et RAG (étape 12) : corpus fictif, filtres de date et de rôle, mémoire des agents (A1),
traçabilité des sources (F2, F6), évaluation. Sans Ollama grâce à un embedding par sacs de mots."""

import asyncio
import math
import random
import re
import uuid
import zlib
from pathlib import Path

import pytest

from agents.agent_llm import AgentLLM
from agents.base import Perception
from agents.roles import Role
from bus import events as ev
from llm.routeur import Routeur
from rag.base import BaseCorpus, Memoires, client_memoire_vive
from rag.corpus import TYPES, lire_corpus
from rag.evaluation import DecisionLue, charger_reference, controler, evaluer
from rag.recuperation import Recuperateur
from sim.config import load_config
from sim.domaine import Canal, Sens, Zone, debut_du_jour
from tests.test_llm import ClientScripte, _json

DEFAULT = Path(__file__).parent.parent / "configs" / "default.yaml"
CONFIG = load_config(DEFAULT)
MONDE = CONFIG.monde


class EmbeddeurMots:
    """Embedding de test : sac de mots haché sur 512 dimensions, normalisé. Déterministe."""

    modele = "test-mots"

    def __init__(self) -> None:
        self.appels = 0

    def _vecteur(self, texte: str) -> list[float]:
        v = [0.0] * 512
        for mot in re.findall(r"[a-zà-ÿ0-9]{3,}", texte.lower()):
            v[zlib.crc32(mot.encode()) % 512] += 1.0
        norme = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / norme for x in v]

    async def questions(self, textes: list[str]) -> list[list[float]]:
        self.appels += 1
        return [self._vecteur(t) for t in textes]

    async def documents(self, textes: list[str]) -> list[list[float]]:
        self.appels += 1
        return [self._vecteur(t) for t in textes]


def _embeddeur() -> EmbeddeurMots:
    """ChromaDB partage sa base en mémoire dans tout le processus : un nom de modèle unique
    donne à chaque test ses propres collections."""
    embeddeur = EmbeddeurMots()
    embeddeur.modele = f"test-mots-{uuid.uuid4().hex[:8]}"
    return embeddeur


def _base() -> BaseCorpus:
    base = BaseCorpus(client_memoire_vive(), _embeddeur())
    asyncio.run(base.indexer(lire_corpus(MONDE)))
    return base


def _recuperateur(run_id: str = "test") -> Recuperateur:
    base = _base()
    memoires = Memoires(client_memoire_vive(), base.embeddeur, f"{run_id}-{uuid.uuid4().hex[:8]}")
    return Recuperateur(base, memoires, CONFIG.rag, MONDE)


# --- corpus ----------------------------------------------------------------------------------


def test_corpus_is_fictional_and_uses_the_configured_names() -> None:
    documents = lire_corpus(MONDE)
    assert len(documents) >= 30
    textes = " ".join(d.passage() + d.auteur for d in documents)
    assert "Vaelys" in textes and "nacrelle" in textes and "Zelvane" in textes
    assert "{" not in textes  # tous les gabarits sont remplacés
    for interdit in ("cancer", "maladie", "dermatolog", "soigne", "guérit", "médical"):
        assert interdit not in textes.lower()  # aucune allégation de santé (CLAUDE.md)
    assert {d.type for d in documents} <= set(TYPES)
    assert any(d.visibilite is Role.VENDEUR for d in documents)  # notes réservées à un rôle


def test_reference_set_points_to_existing_documents() -> None:
    ids = {d.id for d in lire_corpus(MONDE)}
    reference = charger_reference(MONDE)
    assert len(reference) >= 30
    for q in reference:
        assert set(q.attendus) <= ids and set(q.interdits) <= ids


# --- filtres et index --------------------------------------------------------------------------


def _ids(passages) -> set[str]:
    return {p.source.removeprefix("corpus:") for p in passages}


def test_retrieval_never_returns_future_documents() -> None:
    base = _base()
    question = "flacon familial de 200 ml pour toute la famille"
    assert "format-familial" not in _ids(
        asyncio.run(base.rechercher(question, 29, Role.SCEPTIQUE, 10))
    )
    assert "format-familial" in _ids(asyncio.run(base.rechercher(question, 30, Role.SCEPTIQUE, 10)))


def test_internal_notes_are_read_only_by_their_role() -> None:
    base = _base()
    question = "réassort des crèmes solaires le vendredi en rayon"
    assert "note-vendeurs" in _ids(asyncio.run(base.rechercher(question, 5, Role.VENDEUR, 6)))
    for role in (Role.BAIGNEUR_OCCASIONNEL, Role.PHARMACIEN, Role.RESPONSABLE_MARQUE):
        assert "note-vendeurs" not in _ids(asyncio.run(base.rechercher(question, 5, role, 37)))


def test_index_is_reused_until_the_corpus_changes() -> None:
    client = client_memoire_vive()
    documents = lire_corpus(MONDE)
    base = BaseCorpus(client, _embeddeur())
    assert asyncio.run(base.indexer(documents)) is True
    assert asyncio.run(BaseCorpus(client, base.embeddeur).indexer(documents)) is False
    autre_monde = MONDE.model_copy(update={"brand_name": "Kelvora"})
    assert asyncio.run(BaseCorpus(client, base.embeddeur).indexer(lire_corpus(autre_monde)))


def test_reference_evaluation_reports_no_filter_violation() -> None:
    resultat = asyncio.run(evaluer(_base(), charger_reference(MONDE)))
    assert resultat.violations == 0
    assert 0 < resultat.reussite <= 1


# --- mémoire et récupération -----------------------------------------------------------------


def _perception(jour: int, messages=(), zone=Zone.PLAGE) -> Perception:
    return Perception(jour, debut_du_jour(jour) + 600, zone, messages=messages)


def _message(de: str, contenu: str, jour: int) -> ev.AgentMessage:
    t = debut_du_jour(jour) + 600
    return ev.AgentMessage(
        run_id="test", sim_time_occurred=t, sim_time_processed=t, source_agent_id=de,
        payload=ev.AgentMessagePayload(canal=Canal.DIRECT, destinataires=("sceptique_01",),
                                       contenu=contenu, livraison=t),
    )  # fmt: skip


def test_memories_are_private_and_only_from_past_days() -> None:
    rec = _recuperateur()
    asyncio.run(rec.memoires.ajouter("sceptique_01", 3, "Jour 3 : on m'a parlé de la nacrelle.", 2))
    asyncio.run(rec.memoires.ajouter("sceptique_02", 3, "Jour 3 : souvenir d'un autre agent.", 2))
    (vecteur,) = asyncio.run(rec.corpus.embeddeur.questions(["nacrelle"]))
    assert rec.memoires.rechercher_vecteur(vecteur, "sceptique_01", 3) == []  # pas le jour même
    souvenirs = rec.memoires.rechercher_vecteur(vecteur, "sceptique_01", 4)
    assert [s.source for s in souvenirs] == ["memoire:sceptique_01:j3"]


def test_retrieval_prefers_documents_not_yet_read() -> None:
    rec = _recuperateur()
    premiers = asyncio.run(rec.contexte("sceptique_01", Role.SCEPTIQUE, _perception(5)))
    seconds = asyncio.run(rec.contexte("sceptique_01", Role.SCEPTIQUE, _perception(9)))
    assert len(premiers.passages) == len(seconds.passages) == CONFIG.rag.passages
    deja_vus = set(premiers.sources) & set(seconds.sources)
    assert len(deja_vus) <= 1


def test_agent_remembers_an_exchange_several_days_later() -> None:
    """A1 : un message reçu le jour 3 revient dans le prompt du jour 9, sans nouveau message."""
    rec = _recuperateur()
    client = ClientScripte(_json(sens="banal", action="ignorer"), _json(sens="demode"))
    agent = AgentLLM("sceptique_01", Role.SCEPTIQUE, Zone.PLAGE, random.Random(0),
                     Routeur(client, CONFIG.llm), MONDE, 42, [], 0.3, recuperateur=rec)  # fmt: skip
    message = _message(
        "pharmacien_02", "Attention, la crème Vaelys colle au sable en plein midi.", 3
    )
    agent.motif = ev.Motif.MESSAGE
    premiere = asyncio.run(agent.decider(_perception(3, (message,))))
    agent.noter_decision(premiere, _perception(3))
    agent.motif = ev.Motif.LASSITUDE
    seconde = asyncio.run(agent.decider(_perception(9)))
    prompt_jour_9 = client.appels[1]["prompt"]
    assert "Ce dont tu te souviens" in prompt_jour_9
    assert "colle au sable" in prompt_jour_9 and "pharmacien_02" in prompt_jour_9
    assert "memoire:sceptique_01:j3" in seconde.sources  # traçable (F2, F6)
    assert seconde.sens is Sens.DEPASSE  # « demode » côté modèle, catégorie interne inchangée
    assert "te semblait « banal »" in prompt_jour_9
    assert all(s.startswith("corpus:") for s in premiere.sources)  # le jour 3, aucun souvenir


def test_agent_still_decides_when_retrieval_fails() -> None:
    class EnPanne:
        async def contexte(self, *args):
            raise ConnectionError("Ollama injoignable")

        async def retenir(self, *args):
            raise ConnectionError("Ollama injoignable")

    agent = AgentLLM("sceptique_01", Role.SCEPTIQUE, Zone.PLAGE, random.Random(0),
                     Routeur(ClientScripte(_json()), CONFIG.llm), MONDE, 42, [], 0.3,
                     recuperateur=EnPanne())  # fmt: skip
    decision = asyncio.run(agent.decider(_perception(2)))
    assert decision.moteur is ev.Moteur.LLM and decision.sources == ()
    assert [i.detail.split(" :")[0] for i in decision.incidents] == ["récupération", "mémoire"]


# --- contrôle d'un run -------------------------------------------------------------------------


def test_run_check_detects_future_and_foreign_sources() -> None:
    sources = ("corpus:format-familial", "corpus:note-vendeurs", "memoire:vendeur_01:j4")
    bonnes = [DecisionLue("e1", "vendeur_01", 31, "message", "x", sources)]
    assert controler(bonnes, MONDE) == {"futur": 0, "hors_role": 0, "souvenir_invalide": 0,
                                        "sources": 3}  # fmt: skip
    mauvaises = [DecisionLue("e2", "sceptique_01", 10, None, "x",
                             ("corpus:format-familial", "corpus:note-vendeurs",
                              "memoire:sceptique_01:j10", "memoire:sceptique_02:j3"))]  # fmt: skip
    ecarts = controler(mauvaises, MONDE)
    assert (ecarts["futur"], ecarts["hors_role"], ecarts["souvenir_invalide"]) == (1, 1, 2)


@pytest.mark.ollama
def test_reference_set_with_the_chosen_embedding_model() -> None:
    import httpx

    from rag.embeddings import EmbeddeurOllama
    from sim.settings import load_settings

    host = load_settings().ollama_host
    try:
        noms = {m["name"] for m in httpx.get(f"{host}/api/tags", timeout=3).json()["models"]}
    except httpx.HTTPError:
        pytest.skip("Ollama injoignable")
    modele = CONFIG.rag.modele_embeddings
    if modele not in noms and f"{modele}:latest" not in noms:
        pytest.skip(f"{modele} absent : ollama pull {modele}")

    async def scenario():  # une seule boucle : le client HTTP d'Ollama y est attaché
        base = BaseCorpus(client_memoire_vive(), EmbeddeurOllama(host, modele))
        await base.indexer(lire_corpus(MONDE))
        return await evaluer(base, charger_reference(MONDE))

    resultat = asyncio.run(scenario())
    assert resultat.violations == 0
    assert resultat.reussite >= 0.95


def test_prompt_stays_within_the_context_budget() -> None:
    """Beaucoup de messages longs : le prompt est ramené sous le budget, et les sources publiées
    ne citent que ce que le modèle a réellement vu."""
    rec = _recuperateur()
    for jour in (1, 2, 3):
        texte = f"Jour {jour} : " + "discussion animée sur la texture et le prix de Vaelys. " * 6
        asyncio.run(rec.memoires.ajouter("sceptique_01", jour, texte, 3))
    client = ClientScripte(_json())
    agent = AgentLLM("sceptique_01", Role.SCEPTIQUE, Zone.PLAGE, random.Random(0),
                     Routeur(client, CONFIG.llm), MONDE, 42, [], 0.3, recuperateur=rec)  # fmt: skip
    longs = tuple(
        _message(
            f"habitue_soin_{i:02d}", "Vaelys ? " + "C'est fluide, un peu cher, ça brille. " * 7, 5
        )
        for i in range(10)
    )
    agent.motif = ev.Motif.MESSAGE
    decision = asyncio.run(agent.decider(_perception(5, longs)))
    prompt = client.appels[0]["prompt"]
    assert len(prompt) <= agent.budget_caracteres()
    assert agent.prompts_reduits == 1 and agent.retraits_prompt > 0
    vus = [s for s in decision.sources if s.startswith("memoire:")]
    assert all(f"Jour {s[-1]} :" in prompt for s in vus)  # source citée = texte présent


def test_embedding_is_retried_once_after_a_server_crash() -> None:
    from ollama import ResponseError

    from rag.embeddings import EmbeddeurOllama

    class ClientInstable:
        def __init__(self) -> None:
            self.appels = 0

        async def embed(self, model, input, keep_alive):
            self.appels += 1
            if self.appels == 1:
                raise ResponseError("llama-server process has terminated", 500)

            class Reponse:
                embeddings = [[0.1, 0.2] for _ in input]

            return Reponse()

    embeddeur = EmbeddeurOllama("http://localhost:11434", "bge-m3")
    embeddeur._client = ClientInstable()
    assert asyncio.run(embeddeur.questions(["Vaelys"])) == [[0.1, 0.2]]
    assert (embeddeur.appels, embeddeur.relances) == (1, 1)
