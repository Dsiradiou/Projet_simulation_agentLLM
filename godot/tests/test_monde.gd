# Test de la scène sans écran ni pont : on lui donne des messages du pont et on vérifie son état.
# Usage : godot --headless --path godot --script res://tests/test_monde.gd  (code de sortie 1 si échec)
extends SceneTree

var _echecs := 0


func verifier(condition: bool, message: String) -> void:
	if not condition:
		_echecs += 1
		printerr("ÉCHEC : ", message)


func _initialize() -> void:
	var monde: Node3D = load("res://scenes/monde.tscn").instantiate()
	root.add_child(monde)
	await process_frame
	var agents := [
		{"agent_id": "pharmacien_01", "role": "pharmacien", "palier": "grand", "zone": "pharmacie", "sens": "efficace", "porteur": "1"},
		{"agent_id": "sceptique_01", "role": "sceptique", "palier": "petit", "zone": "plage", "sens": "", "porteur": "0"},
	]
	monde._sur_message({"type": "pont.instantane", "run": {"run_id": "r", "statut": "en_cours", "jour": 3}, "meteo": {"conditions": "canicule", "temperature_c": 35.0, "indice_uv": 10}, "agents": agents, "zones": {}, "perturbations": []})
	verifier(monde.habitants.size() == 2, "deux habitants après l'instantané")
	var ph = monde.habitants["pharmacien_01"]
	verifier(ph.zone == "pharmacie" and ph.porteur and ph.sens == "efficace", "fiche du pharmacien appliquée")
	verifier(ph._flacon.visible, "le flacon signale un porteur")

	var decision := {"type": "agent.decision", "source_agent_id": "sceptique_01", "zone": "plage", "payload": {"sens": "tendance", "action": "acheter", "porteur": true, "moteur": "llm", "palier": "petit", "justification": "Tout le monde en a.", "motif": "message", "sources": ["corpus:fiche-produit", "memoire:sceptique_01:j2"], "appel_llm": {"modele": "qwen3:1.7b", "duree_s": 0.8, "tokens_sortie": 90, "tentatives": 1}}}
	monde._sur_message(decision)
	var sc = monde.habitants["sceptique_01"]
	verifier(sc.sens == "tendance" and sc.porteur, "une décision change le sens et le porteur")
	verifier(sc.decisions.size() == 1 and sc.decisions[0]["justification"] == "Tout le monde en a.", "la décision est gardée pour l'inspection")

	monde._sur_message({"type": "agent.decision", "source_agent_id": "vendeur_03", "zone": "centre_commercial", "payload": {"sens": "banal", "action": "ignorer", "porteur": false, "moteur": "regle", "palier": null, "justification": "Règle de seuil.", "appel_llm": null}})
	verifier(monde.habitants.has("vendeur_03"), "un agent inconnu apparaît à sa première décision")
	verifier(monde.habitants["vendeur_03"].zone == "centre_commercial", "dans la zone de l'événement")

	monde._sur_message({"type": "agent.deplacement", "source_agent_id": "sceptique_01", "payload": {"depuis": "plage", "vers": "piscine"}})
	verifier(sc.zone == "piscine", "un déplacement change de zone")
	verifier(monde._place_de["sceptique_01"][0] == "piscine", "et réserve une place dans la nouvelle zone")

	monde._sur_message({"type": "agent.message", "source_agent_id": "sceptique_01", "payload": {"canal": "reseaux", "contenu": "Elle tient à la baignade.", "destinataires": []}})
	verifier(sc._bulle.visible and sc._bulle.text.begins_with("Elle tient"), "la bulle montre le message")
	verifier(monde.decor._ecran.text.contains("Elle tient"), "l'écran des réseaux affiche la publication")

	monde._sur_message({"type": "horloge.pas", "payload": {"jour": 4}})
	monde._sur_message({"type": "monde.perturbation", "zone": "plage", "payload": {"nature": "polemique", "description": "Une vidéo virale."}})
	verifier(monde.decor._halos["plage"].visible, "la zone perturbée est signalée")
	verifier(monde._jour == 4, "le jour avance avec l'horloge")

	monde._selectionner(sc)
	var fiche: String = monde.ui._fiche_texte.text
	verifier(fiche.contains("sceptique_01") and fiche.contains("Tout le monde en a."), "la fiche montre la justification")
	verifier(fiche.contains("qwen3:1.7b"), "la fiche montre le modèle et le coût")
	verifier(fiche.contains("fiche-produit") and fiche.contains("J2"), "la fiche montre ce que l'agent avait lu et ses souvenirs")

	monde._sur_message({"type": "monde.stock", "zone": "pharmacie", "payload": {"produit": "marque", "quantite": 0}})
	verifier(monde.decor._stocks["pharmacie"].text == "Rupture de stock", "une rupture s'affiche au-dessus de la pharmacie")
	monde._sur_message({"type": "monde.stock", "zone": "pharmacie", "payload": {"produit": "marque", "quantite": 7}})
	verifier(monde.decor._stocks["pharmacie"].text.contains("7 flacons"), "le réassort s'affiche")

	# Étape 14 : indicateurs du service (B2), dans le panneau, la courbe et les jauges des zones.
	verifier(monde.ui._mesures.text.contains("non reçus"), "sans indicateurs, le panneau le dit")
	monde._sur_message({"type": "indicateur.valeur", "sim_time_occurred": 1439, "payload": {"nom": "sens_partage", "valeur": 0.42, "groupe": null, "repartition": {"efficace": 3, "banal": 1}}})
	monde._sur_message({"type": "indicateur.valeur", "sim_time_occurred": 2879, "payload": {"nom": "sens_partage", "valeur": 0.61, "groupe": null, "repartition": {"efficace": 3, "banal": 1}}})
	monde._sur_message({"type": "indicateur.valeur", "sim_time_occurred": 2879, "payload": {"nom": "information_mutuelle", "valeur": 0.3, "groupe": null}})
	monde._sur_message({"type": "indicateur.valeur", "sim_time_occurred": 2879, "payload": {"nom": "derives", "valeur": 0.05, "groupe": null}})
	verifier(monde.ui._mesures.text.contains("0.61") and monde.ui._mesures.text.contains("5 %"), "le panneau montre les valeurs du service")
	verifier(monde.ui._barres["efficace"][1].text.contains("75 %"), "la répartition vient du service")
	verifier(monde.ui.courbe.points("sens_partage") == 2, "la courbe garde une valeur par jour")
	monde._sur_message({"type": "indicateur.valeur", "zone": "plage", "sim_time_occurred": 2879, "payload": {"nom": "sens_partage", "valeur": 1.0, "groupe": "zone:plage", "repartition": {"tendance": 2}}})
	var jauge: Array = monde.decor.jauge("plage")
	verifier(jauge[1]["tendance"].visible and not jauge[1]["efficace"].visible, "la jauge de la plage montre ses sens")
	verifier((jauge[2] as Label3D).text.contains("C 1.00"), "et la convergence de la zone")
	monde._sur_message({"type": "horloge.fin_jour", "payload": {"jour": 2}})
	verifier(not jauge[1]["tendance"].visible, "une fin de jour remet les jauges à zéro")

	monde._sur_message({"type": "pont.nouveau_run", "run_id": "r2"})
	verifier(monde.habitants.is_empty(), "un nouveau run repart d'un monde vide")
	verifier(monde.ui.courbe.points("sens_partage") == 0, "et une courbe vide")

	print("test_monde : %s" % ("OK" if _echecs == 0 else "%d échec(s)" % _echecs))
	quit(1 if _echecs > 0 else 0)
