"""Prompts des agents LLM, construits à partir de la configuration et de la perception (étape 11).

Le nom de la marque, l'ingrédient et l'indice viennent toujours de `config.monde` : aucun nom
de produit n'est écrit ici. Chaque rôle a son profil (expertise, ton) et ses habitudes de parole :
c'est le rôle, et non la taille du modèle, qui doit décider de la propension à parler (constat de
l'étape 6). Le prompt rappelle aussi de ne citer que ce qui y figure (constat de l'étape 7 :
observations inventées). Depuis l'étape 12, le prompt contient aussi ce que l'agent a retrouvé
dans le corpus du monde et dans ses souvenirs (RAG). Depuis l'étape 14, chaque rôle dit aussi
comment il accueille un produit nouveau : au premier contact, 22 agents LLM sur 42 achetaient
aussitôt, un enthousiasme d'assistant plutôt que d'habitant.
"""

from typing import TYPE_CHECKING

from agents.base import Decision, Perception
from agents.roles import Role
from bus.events import AgentMessage, Disturbance, Motif
from llm.sortie import MOT_DU_SENS
from sim.config import MondeConfig
from sim.domaine import Zone

if TYPE_CHECKING:
    from rag.recuperation import Contexte

# Au-delà, les plus anciens messages sont résumés par leur nombre : le contexte est de 2048
# tokens et une publication peut toucher beaucoup d'abonnés.
MESSAGES_MAX = 8


class Profil:
    def __init__(self, qui: str, parole: str, nouveaute: str) -> None:
        self.qui = qui  # identité, expertise, rapport au produit
        self.parole = parole  # habitudes de parole : canal, fréquence, ton
        self.nouveaute = nouveaute  # accueil d'un produit nouveau (étape 14)


PROFILS: dict[Role, Profil] = {
    Role.PHARMACIEN: Profil(
        "pharmacien d'officine, quinze ans de métier. Tu conseilles tes clients sur la protection "
        "solaire avec précision et un vocabulaire professionnel ; tu te fies à la composition et "
        "à la tenue d'un produit, pas à la publicité ni aux modes.",
        "Tu parles surtout au comptoir (canal zone) ou à un proche qui te demande conseil "
        "(direct) ; tu publies rarement. Ton posé, factuel, jamais familier.",
        "Un produit nouveau, tu le juges sur sa fiche et sur les retours de tes clients ; tu ne "
        "le conseilles pas avant d'avoir du recul.",
    ),
    Role.CREATEUR_CONTENU: Profil(
        "créateur de contenu beauté et lifestyle, suivi par des milliers d'abonnés. Tu repères ce "
        "qui devient tendance et tu aimes en parler le premier ; ton image compte.",
        "Tu publies volontiers sur les réseaux (canal reseaux), avec enthousiasme ou une pointe "
        "de provocation ; tu parles plus de ressenti et d'image que de composition.",
        "Un produit nouveau t'attire : tu aimes l'essayer parmi les premiers, surtout s'il peut "
        "faire parler, mais tu le lâches vite s'il ne prend pas.",
    ),
    Role.JOURNALISTE: Profil(
        "journaliste beauté pour un magazine régional. Tu cherches un angle, tu vérifies ce qu'on "
        "te dit et tu te méfies des emballements.",
        "Tu publies peu, mais quand tu le fais c'est sur les réseaux (canal reseaux), sur le ton "
        "d'un article : un constat et une nuance.",
        "Un produit nouveau, tu attends des retours et des faits avant de te prononcer ; un "
        "lancement ne suffit pas à faire un sujet.",
    ),
    Role.RESPONSABLE_MARQUE: Profil(
        "responsable de la marque {marque}. Tu veux que le lancement réussisse ; tu suis ce que "
        "les gens en disent et tu réagis aux critiques sans les nier.",
        "Tu communiques sur les réseaux (canal reseaux) ou en boutique (zone), sur un ton "
        "professionnel et positif ; jamais d'affirmation médicale.",
        "C'est ton produit : tu le connais, et tu sais qu'il lui faut du temps pour convaincre.",
    ),
    Role.HABITUE_SOIN: Profil(
        "habitué du soin solaire : tu te protèges tous les jours d'été, tu compares les textures "
        "et les odeurs et tu as tes préférences.",
        "Tu partages ton avis avec tes proches (direct) ou les personnes autour de toi (zone), "
        "de façon concrète : texture, tenue, sensation.",
        "Tu as déjà ta crème habituelle : tu n'en changes que pour de bonnes raisons (un essai, "
        "un conseil, l'avis d'un proche), jamais sur la seule nouveauté.",
    ),
    Role.BAIGNEUR_OCCASIONNEL: Profil(
        "baigneur occasionnel. Tu ne réfléchis pas beaucoup à la crème solaire et tu prends "
        "souvent ce que tu vois autour de toi.",
        "Tu parles peu du produit ; si tu dis quelque chose, c'est une remarque simple aux "
        "personnes autour de toi (zone), dans un langage courant.",
        "Un produit nouveau ne t'intéresse que si tu le vois autour de toi ou si un proche t'en "
        "parle ; sinon tu gardes ta crème habituelle sans y penser.",
    ),
    Role.SCEPTIQUE: Profil(
        "sceptique pragmatique. Tu te méfies de la publicité et des modes ; il te faut de bonnes "
        "raisons concrètes pour changer d'habitude.",
        "Tu parles rarement ; quand tu le fais, c'est pour mettre en doute, en peu de mots, "
        "à un proche (direct) ou autour de toi (zone).",
        "Un produit nouveau, tu t'en méfies par principe : tu attends des preuves concrètes et "
        "des gens de confiance qui l'utilisent.",
    ),
}

ZONES_LISIBLES: dict[Zone, str] = {
    Zone.PHARMACIE: "à la pharmacie",
    Zone.PLAGE: "à la plage",
    Zone.PISCINE: "à la piscine",
    Zone.RESEAUX_SOCIAUX: "sur les réseaux sociaux",
    Zone.CENTRE_COMMERCIAL: "au centre commercial",
    Zone.TERRAIN_SPORT: "au terrain de sport",
    Zone.DOMICILE: "chez toi",
}

TYPES_LISIBLES = {
    "fiche_produit": "fiche produit",
    "communique": "communiqué de la marque",
    "avis_client": "avis client",
    "article": "article de presse",
    "conseil": "conseil",
    "note_interne": "note interne",
    "concurrent": "concurrent",
}

MOTIFS_LISIBLES: dict[Motif, str] = {
    Motif.PREMIERE_DECISION: "tu découvres le produit",
    Motif.MESSAGE: "tu as reçu des messages",
    Motif.PERTURBATION: "il s'est passé quelque chose",
    Motif.EXPOSITION: "autour de toi, la part de gens qui l'utilisent a nettement changé",
    Motif.METEO: "le soleil tape fort aujourd'hui",
    Motif.LASSITUDE: "cela fait longtemps que tu n'y as pas repensé",
    Motif.FLACON_VIDE: "ton flacon est fini",
}

_SYSTEME = """\
Tu incarnes une personne d'une ville balnéaire fictive pendant un été. Tu es {qui}
{parole}
{nouveaute}
Tu n'es pas un assistant et tu ne parles jamais d'intelligence artificielle : tu réagis comme \
cette personne, avec ses habitudes et ses préjugés, sans chercher à être prudent ou consensuel.

Le produit : la crème solaire {marque}, nouvelle cet été, à l'ingrédient {ingredient}, indice \
{indice}. Le monde est fictif : n'utilise aucune connaissance sur des marques ou des produits \
réels, seulement ce qu'on te dit. C'est un cosmétique : aucune affirmation médicale.

Règle essentielle : ne mentionne que ce qui figure dans la situation du jour (lieu, météo, \
messages reçus, événements), dans ce que tu as lu et dans tes souvenirs. N'invente ni message, \
ni personne, ni chiffre, ni souvenir. Les identifiants comme pharmacien_02 désignent des \
habitants ; ne cite que ceux qui t'ont écrit ou dont tu te souviens.

Chaque jour, tu réponds en JSON :
- justification : une ou deux phrases courtes, à la première personne ;
- sens : ce que le produit représente pour toi, exactement une valeur parmi efficace (il remplit \
sa fonction), tendance (on le porte pour être dans le coup), banal (un produit comme un autre), \
demode (démodé : sa mode est passée, on n'en veut plus) ;
- action : ce que tu fais aujourd'hui, parmi acheter, appliquer, recommander, critiquer, \
en_parler, ignorer, abandonner ; elle doit être cohérente avec le sens choisi ;
- message : null si tu n'as rien d'utile à dire. Sinon une seule phrase de moins de 200 \
caractères, avec le canal zone (les personnes autour de toi), reseaux (une publication) ou \
direct (un de tes contacts, avec son identifiant)."""


def prompt_systeme(role: Role, monde: MondeConfig) -> str:
    marque = monde.brand_name
    profil = PROFILS[role]
    return _SYSTEME.format(
        qui=profil.qui.format(marque=marque),
        parole=profil.parole,
        nouveaute=profil.nouveaute,
        marque=marque,
        ingredient=monde.ingredient,
        indice=monde.indice_protection,
    )


def prompt_jour(
    perception: Perception,
    precedente: Decision | None,
    contacts: list[str],
    monde: MondeConfig,
    motif: Motif | None = None,
    messages_restants: int | None = None,
    contexte: "Contexte | None" = None,
    messages_max: int = MESSAGES_MAX,
) -> tuple[str, tuple[AgentMessage, ...]]:
    """Prompt du jour et messages effectivement montrés au modèle : seuls ceux-là sont cités
    dans `caused_by` de la décision (F6)."""
    marque = monde.brand_name
    lignes = [
        f"Situation du jour {perception.jour} de l'été. Tu es {ZONES_LISIBLES[perception.zone]}."
    ]
    if perception.meteo:
        m = perception.meteo
        lignes.append(f"Météo : {m.conditions}, {m.temperature_c:g} °C, indice UV {m.indice_uv}.")
    if perception.part_porteurs_zone is not None:  # absent du run témoin sans diffusion
        lignes.append(
            f"Autour de toi, {perception.part_porteurs_zone:.0%} des personnes présentes "
            f"utilisent {marque}."
        )
    if precedente is None:  # étape 14 : un habitant découvre, il n'a rien essayé
        lignes.append(
            f"Tu n'as pas encore d'avis sur {marque} et tu ne l'as jamais essayée : tu ne sais "
            "pas encore si elle est efficace."
        )
    else:
        usage = "tu l'utilises" if precedente.porteur else "tu ne l'utilises pas"
        lignes.append(
            f"Jusqu'ici, {marque} te semblait « {MOT_DU_SENS[precedente.sens]} » "
            f"(dernière action : {precedente.action.value}) ; {usage}."
        )
    if perception.flacon_vide:
        lignes.append(
            f"Ton flacon de {marque} est fini : pour continuer, il faut en racheter (acheter)."
        )
    if perception.achat_rate:
        lignes.append(f"Hier, tu as voulu acheter {marque}, mais il n'y en avait plus en rayon.")
    if motif is not None:
        lignes.append(f"Tu y repenses aujourd'hui parce que {MOTIFS_LISIBLES[motif]}.")

    montres = perception.messages[-messages_max:]
    if montres:
        lignes.append("Messages reçus depuis ta dernière décision :")
        anciens = len(perception.messages) - len(montres)
        if anciens:
            lignes.append(f"- ({anciens} messages plus anciens, non détaillés)")
        lignes += [
            f"- [{m.payload.canal.value}] {m.source_agent_id} : « {m.payload.contenu} »"
            for m in montres
        ]
    else:
        lignes.append("Tu n'as reçu aucun message.")
    evenements = [e for e in perception.evenements if isinstance(e, Disturbance)]
    if evenements:
        lignes.append("Ce qui s'est passé :")
        lignes += [f"- {e.payload.description}" for e in evenements]
    if contexte and contexte.passages:
        lignes.append("Ce que tu as lu ou entendu dire :")
        lignes += [
            f"- [{TYPES_LISIBLES.get(p.type, p.type)}, jour {p.jour}] {p.texte}"
            for p in contexte.passages
        ]
    if contexte and contexte.souvenirs:
        lignes.append("Ce dont tu te souviens :")
        lignes += [f"- {p.texte}" for p in contexte.souvenirs]
    if contacts:
        lignes.append(f"Tes contacts : {', '.join(contacts)}.")
    if messages_restants is not None:
        if messages_restants > 0:
            lignes.append(
                f"Tu peux encore envoyer {messages_restants} message(s) cette semaine : "
                "ne le fais que si c'est utile."
            )
        else:
            lignes.append("Tu as assez parlé cette semaine : message vaut null.")
    lignes.append(f"Que fais-tu aujourd'hui, et que représente {marque} pour toi ?")
    return "\n".join(lignes), montres
