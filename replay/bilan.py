"""Bilan d'un run recalculé à partir de ses seuls événements (E3, E4, F6).

Si le rejeu est fidèle, ce bilan est identique à celui que l'orchestrateur a tenu pendant le run
(`bilan.json`). Les compteurs sont refaits avec la même classe `Bilan` et dans le même ordre de
publication, donc à l'identique, y compris les sommes de flottants.

Ne sont pas comparés : les temps réels de l'exécution (durée totale, chargements de modèles,
bascules de palier), qui décrivent la machine et non le monde simulé.
"""

from agents.roles import Role
from bus import events as ev
from sim.config import Config
from sim.domaine import jour
from sim.orchestrateur import Bilan

COMPARABLES = (
    "jours",
    "decisions",
    "appels_llm",
    "decisions_conservees",
    "messages",
    "deplacements",
    "incidents",
    "violations_temporelles",
    "informations_manquees",
    "incidents_par_nature",
    "injections",
    "declenchements",
    "relances",
    "replis",
    "tokens_entree",
    "tokens_sortie",
    "duree_llm_s",
    "cout_par_modele",
)


def population_agents(manifeste: dict) -> int:
    """Nombre d'agents du run (la météo est un acteur du monde, pas un agent)."""
    config = Config.model_validate(manifeste["config"])
    return config.nb_agents - config.population.get(Role.METEO, 0)


def bilan_depuis(events: list[ev.Envelope], population: int) -> dict:
    bilan = Bilan()
    echecs: set[tuple[str | None, int]] = set()  # agents déclenchés sans décision publiée
    for event in events:
        match event:
            case ev.ClockStep():
                bilan.jours = max(bilan.jours, event.payload.jour)
            case ev.ClockEnd():  # mode asynchrone (étape 15) ; 0 en synchrone et avant 1.1
                bilan.informations_manquees += event.payload.informations_manquees
            case ev.AgentDecision():
                bilan.decisions += 1
                if event.payload.appel_llm:
                    bilan.compter_appel(event.payload.appel_llm)
                if event.payload.moteur is ev.Moteur.REPLI:
                    bilan.replis += 1
                if event.payload.motif is not None:
                    motif = event.payload.motif.value
                    bilan.declenchements[motif] = bilan.declenchements.get(motif, 0) + 1
            case ev.Disturbance() if event.source_agent_id == "orchestrateur":
                bilan.injections += 1  # conséquence d'une injection (D3)
            case ev.AgentMessage():
                bilan.messages += 1
            case ev.AgentMove():
                bilan.deplacements += 1
            case ev.Incident():
                nature = event.payload.nature
                bilan.incidents += 1
                par_nature = bilan.incidents_par_nature
                par_nature[nature.value] = par_nature.get(nature.value, 0) + 1
                if nature is ev.NatureIncident.VIOLATION_TEMPORELLE:
                    bilan.violations_temporelles += 1
                elif not event.caused_by:
                    # Les incidents d'une décision publiée la citent ; sans cause, l'agent a été
                    # déclenché mais n'a rien produit (panne ou décision mal formée).
                    echecs.add((event.payload.agent_id, jour(event.sim_time_occurred)))
    bilan.decisions_conservees = population * bilan.jours - bilan.decisions - len(echecs)
    bilan.duree_llm_s = round(bilan.duree_llm_s, 3)
    return {k: getattr(bilan, k) for k in COMPARABLES}
