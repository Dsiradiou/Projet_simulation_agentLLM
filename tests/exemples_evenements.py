"""Un exemple valide de chaque type d'événement, partagé par les tests."""

from bus import events as ev

RUN = "run_test"


def exemples() -> dict[type[ev.Envelope], ev.Envelope]:
    base = {"run_id": RUN, "sim_time_occurred": 600, "sim_time_processed": 602}
    return {
        ev.ClockStep: ev.ClockStep(
            **base, source_agent_id="horloge", payload=ev.ClockStepPayload(jour=1)
        ),
        ev.Weather: ev.Weather(
            **base,
            source_agent_id="meteo",
            payload=ev.WeatherPayload(indice_uv=8, temperature_c=31.5, conditions="ensoleille"),
        ),
        ev.Stock: ev.Stock(
            **base,
            source_agent_id="vendeur_01",
            zone="pharmacie",
            payload=ev.StockPayload(produit="marque", quantite=40),
        ),
        ev.Disturbance: ev.Disturbance(
            **base,
            source_agent_id="orchestrateur",
            payload=ev.DisturbancePayload(nature="vague_chaleur", description="Canicule."),
        ),
        ev.AgentMessage: ev.AgentMessage(
            **base,
            source_agent_id="habitue_soin_03",
            zone="plage",
            payload=ev.AgentMessagePayload(
                canal="direct",
                destinataires=("baigneur_occasionnel_12",),
                contenu="Elle tient bien après la baignade.",
                livraison=720,
            ),
        ),
        ev.AgentDecision: ev.AgentDecision(
            **base,
            source_agent_id="pharmacien_01",
            zone="pharmacie",
            payload=ev.AgentDecisionPayload(
                action="recommander",
                sens="efficace",
                justification="Les retours de mes clients sont bons.",
                porteur=True,
                moteur="llm",
                palier="grand",
                appel_llm=ev.LlmCall(
                    modele="qwen3:8b", tokens_entree=650, tokens_sortie=104, duree_s=2.1
                ),
            ),
        ),
        ev.AgentMove: ev.AgentMove(
            **base,
            source_agent_id="baigneur_occasionnel_12",
            zone="plage",
            payload=ev.AgentMovePayload(depuis="domicile", vers="plage"),
        ),
        ev.Indicator: ev.Indicator(
            **base,
            source_agent_id="indicateurs",
            payload=ev.IndicatorPayload(
                nom="sens_partage", valeur=0.42, fenetre_debut=0, fenetre_fin=600
            ),
        ),
        ev.Injection: ev.Injection(
            **base,
            source_agent_id="pont",
            payload=ev.InjectionPayload(nature="polemique", description="Vidéo critique."),
        ),
        ev.Command: ev.Command(
            **base, source_agent_id="pont", payload=ev.CommandPayload(commande="pause")
        ),
        ev.Incident: ev.Incident(
            **base,
            source_agent_id="orchestrateur",
            payload=ev.IncidentPayload(
                nature="sortie_invalide", agent_id="sceptique_02", detail="JSON tronqué"
            ),
        ),
        ev.Rejection: ev.Rejection(
            **base,
            source_agent_id="bus",
            payload=ev.RejectionPayload(topic_origine="sim.control", brut="{", erreur="illisible"),
        ),
    }
