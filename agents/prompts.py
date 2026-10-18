"""Prompts des agents LLM, construits à partir de la configuration et de la perception.

Le nom de la marque, l'ingrédient et l'indice viennent toujours de `config.monde` : aucun nom
de produit n'est écrit ici. Les profils de rôle sont provisoires ; ils seront retravaillés à
l'étape 11 (population) et complétés par la mémoire et le RAG à l'étape 12.
"""

from agents.base import Decision, Perception
from agents.roles import Role
from bus.events import AgentMessage, Disturbance
from sim.config import MondeConfig
from sim.domaine import Zone

# Au-delà, les plus anciens messages sont résumés par leur nombre : le contexte est de 2048
# tokens et un message de réseaux peut toucher beaucoup d'agents.
MESSAGES_MAX = 8

PROFILS: dict[Role, str] = {
    Role.PHARMACIEN: (
        "pharmacien d'officine expérimenté. Tu conseilles tes clients sur la protection "
        "solaire, tu te fies aux fiches produit et tu te méfies des effets de mode."
    ),
    Role.CREATEUR_CONTENU: (
        "créateur de contenu beauté suivi sur les réseaux sociaux. Tu repères ce qui devient "
        "tendance et tu aimes en parler le premier ; ton audience compte pour toi."
    ),
    Role.JOURNALISTE: (
        "journaliste beauté. Tu écris sur les nouveautés de la saison, tu cherches un angle et "
        "tu vérifies ce qu'on te dit avant de le reprendre."
    ),
    Role.RESPONSABLE_MARQUE: (
        "responsable de la marque {marque}. Tu veux que le lancement réussisse et tu suis ce "
        "que les gens en disent pour réagir."
    ),
    Role.HABITUE_SOIN: (
        "habitué du soin solaire. Tu te protèges tous les jours d'été, tu compares les "
        "textures et tu écoutes l'avis de tes proches."
    ),
    Role.BAIGNEUR_OCCASIONNEL: (
        "baigneur occasionnel. Tu ne réfléchis pas beaucoup à la crème solaire et tu prends "
        "souvent ce que tu vois autour de toi."
    ),
    Role.SCEPTIQUE: (
        "sceptique pragmatique. Tu te méfies de la publicité et des modes ; il te faut de "
        "bonnes raisons pour changer d'habitude."
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

_SYSTEME = """\
Tu incarnes une personne d'une ville balnéaire fictive pendant un été. Tu es {profil}
Tu n'es pas un assistant et tu ne parles jamais d'intelligence artificielle : tu réagis comme \
cette personne, avec ses habitudes et ses préjugés, sans chercher à être prudent ou consensuel.

Le produit : la crème solaire {marque}, nouvelle cet été, à l'ingrédient {ingredient}, indice \
{indice}. Le monde est fictif : n'utilise aucune connaissance sur des marques ou des produits \
réels, seulement ce qu'on te dit. C'est un cosmétique : aucune affirmation médicale.

Chaque jour, tu réponds en JSON :
- justification : une ou deux phrases courtes, à la première personne, appuyées sur ce que tu \
as vu, entendu ou lu ;
- sens : ce que le produit représente pour toi, exactement une valeur parmi efficace (il remplit \
sa fonction), tendance (on le porte pour être dans le coup), banal (un produit comme un autre), \
depasse (sa mode est passée) ;
- action : ce que tu fais aujourd'hui, parmi acheter, appliquer, recommander, critiquer, \
en_parler, ignorer, abandonner ;
- message : null la plupart du temps. Si tu veux vraiment dire quelque chose, une phrase courte, \
avec le canal zone (les personnes autour de toi), reseaux (une publication) ou direct (un de tes \
contacts, avec son identifiant)."""


def prompt_systeme(role: Role, monde: MondeConfig) -> str:
    marque = monde.brand_name
    return _SYSTEME.format(
        profil=PROFILS[role].format(marque=marque),
        marque=marque,
        ingredient=monde.ingredient,
        indice=monde.indice_protection,
    )


def prompt_jour(
    perception: Perception,
    precedente: Decision | None,
    contacts: list[str],
    monde: MondeConfig,
) -> tuple[str, tuple[AgentMessage, ...]]:
    """Prompt du jour et messages effectivement montrés au modèle : seuls ceux-là sont cités
    dans `caused_by` de la décision (F6)."""
    marque = monde.brand_name
    lignes = [f"Jour {perception.jour} de l'été. Tu es {ZONES_LISIBLES[perception.zone]}."]
    if perception.meteo:
        m = perception.meteo
        lignes.append(f"Météo : {m.conditions}, {m.temperature_c:g} °C, indice UV {m.indice_uv}.")
    lignes.append(
        f"Hier, {perception.part_porteurs_zone:.0%} des personnes présentes ici "
        f"utilisaient {marque}."
    )
    if precedente is None:
        lignes.append(f"Tu n'as pas encore d'avis sur {marque}.")
    else:
        usage = "tu l'utilises" if precedente.porteur else "tu ne l'utilises pas"
        lignes.append(
            f"Jusqu'ici, {marque} te semblait « {precedente.sens.value} » "
            f"(dernière action : {precedente.action.value}) ; {usage}."
        )

    montres = perception.messages[-MESSAGES_MAX:]
    if montres:
        lignes.append("Messages reçus depuis ta dernière décision :")
        anciens = len(perception.messages) - len(montres)
        if anciens:
            lignes.append(f"- ({anciens} messages plus anciens, non détaillés)")
        lignes += [
            f"- [{m.payload.canal.value}] {m.source_agent_id} : « {m.payload.contenu} »"
            for m in montres
        ]
    evenements = [e for e in perception.evenements if isinstance(e, Disturbance)]
    if evenements:
        lignes.append("Ce qui s'est passé :")
        lignes += [f"- {e.payload.description}" for e in evenements]
    if contacts:
        lignes.append(f"Tes contacts : {', '.join(contacts)}.")
    lignes.append(f"Que fais-tu aujourd'hui, et que représente {marque} pour toi ?")
    return "\n".join(lignes), montres
