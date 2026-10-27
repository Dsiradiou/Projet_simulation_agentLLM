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

	# Étape 15 bis : effets visuels de la météo.
	var effets: EffetsMeteo = monde.decor.effets
	var bulletin := func(conditions: String) -> Dictionary:
		return {"type": "monde.meteo", "payload": {"conditions": conditions, "temperature_c": 24.0, "indice_uv": 5}}
	monde._sur_message(bulletin.call("pluie"))
	verifier(effets.etat == "pluie" and effets._pluie.emitting and not effets._chaleur.emitting, "la pluie tombe")
	verifier(effets.nuages_visibles() == 18, "le ciel se couvre")
	monde._sur_message({"type": "horloge.pas", "payload": {"jour": 5}})
	monde._sur_message({"type": "monde.perturbation", "payload": {"nature": "orage", "description": "Un orage éclate."}})
	monde._sur_message(bulletin.call("pluie"))
	verifier(effets.etat == "orage" and effets._orage and monde._meteo.begins_with("orage"), "un orage injecté se voit, avec ses éclairs")
	var eclair: Node3D = effets._eclair()
	verifier(eclair.get_child_count() == 6, "un éclair descend en zigzag")
	monde._sur_message({"type": "horloge.pas", "payload": {"jour": 6}})
	monde._sur_message(bulletin.call("pluie"))
	verifier(effets.etat == "pluie", "le lendemain, une pluie ordinaire")
	monde._sur_message(bulletin.call("canicule"))
	verifier(effets.etat == "canicule" and effets._chaleur.emitting and not effets._pluie.emitting and effets.nuages_visibles() == 0, "la canicule fait onduler l'air, sans nuage")
	monde._sur_message(bulletin.call("ensoleille"))
	verifier(not effets._pluie.emitting and not effets._chaleur.emitting and effets.nuages_visibles() == 5, "beau temps : quelques nuages")
	monde._sur_message(bulletin.call("canicule"))

	# Étape 15 bis : décor construit d'après le plan du run, plusieurs zones d'un même type.
	verifier(monde.decor.est_d_origine(), "sans plan, les sept zones d'origine")
	var plan := ["pharmacie_1", "pharmacie_2", "plage_1", "plage_2", "plage_3", "piscine", "reseaux_sociaux", "centre_commercial", "terrain_sport", "domicile_1", "domicile_2"]
	monde._sur_message({"type": "monde.plan", "payload": {"lieux": plan}})
	var decor: Decor = monde.decor
	verifier(decor.lieux == plan and monde._places.has("plage_3"), "le plan construit une zone par lieu")
	verifier(decor.effets.etat == "canicule" and decor.effets._chaleur.emitting, "le décor reconstruit garde la météo du jour")
	verifier(decor.centre_zone("plage_2").x > decor.centre_zone("plage_1").x and decor.centre_zone("plage_2").z == decor.centre_zone("plage_1").z, "les plages se suivent le long de la côte")
	verifier(decor.centre_zone("domicile_2").z < decor.centre_zone("domicile_1").z, "les autres zones s'alignent vers l'intérieur")
	verifier(decor.nom_lieu("plage_2") == "Plage 2" and decor.nom_lieu("plage") == "Plage", "noms des exemplaires")
	verifier(decor._stocks.has("pharmacie_2") and decor._ecrans.size() == 1, "un stock par pharmacie, un écran par place des réseaux")
	monde._sur_message({"type": "agent.decision", "source_agent_id": "baigneur_occasionnel_01", "zone": "plage_2", "payload": {"sens": "banal", "action": "ignorer", "porteur": false, "moteur": "regle", "palier": null, "justification": "Règle.", "appel_llm": null}})
	var b = monde.habitants["baigneur_occasionnel_01"]
	verifier(b.zone == "plage_2" and monde._place_de["baigneur_occasionnel_01"][0] == "plage_2", "un habitant se place dans son exemplaire")
	monde._sur_message({"type": "agent.deplacement", "source_agent_id": "baigneur_occasionnel_01", "payload": {"depuis": "plage_2", "vers": "plage_3"}})
	verifier(b.zone == "plage_3", "et passe d'une plage à l'autre")
	monde._sur_message({"type": "monde.perturbation", "zone": "plage", "payload": {"nature": "polemique", "description": "Sur toutes les plages."}})
	verifier(decor._halos["plage_1"].visible and decor._halos["plage_3"].visible and not decor._halos["piscine"].visible, "une perturbation sur un type touche tous ses exemplaires")
	monde._sur_message({"type": "monde.perturbation", "zone": "pharmacie_2", "payload": {"nature": "rupture_stock", "description": "Rayon vide."}})
	verifier(decor._halos["pharmacie_2"].visible and not decor._halos["pharmacie_1"].visible, "sur un exemplaire, lui seul")
	monde._sur_message({"type": "monde.stock", "zone": "pharmacie_2", "payload": {"produit": "marque", "quantite": 0}})
	verifier(decor._stocks["pharmacie_2"].text == "Rupture de stock" and decor._stocks["pharmacie_1"].text == "", "le stock s'affiche sur son exemplaire")
	var proposees: Array = monde.ui.zones_proposees()
	verifier(proposees.count("plage") == 1 and proposees.has("plage_2") and proposees.has("piscine"), "l'injection propose un type entier ou un exemplaire")
	monde._sur_message({"type": "indicateur.valeur", "zone": "plage_2", "sim_time_occurred": 2879, "payload": {"nom": "sens_partage", "valeur": 1.0, "groupe": "zone:plage_2", "repartition": {"banal": 1}}})
	verifier(decor.jauge("plage_2")[1]["banal"].visible, "chaque exemplaire a sa jauge")
	monde._sur_message({"type": "pont.rejeu", "run_id": "ancien", "evenements": 3})
	verifier(decor.est_d_origine() and monde._places.has("plage"), "un rejeu sans plan retrouve les zones d'origine")

	# Étape 15 bis : interface de paramétrage, construite d'après la description du pont.
	var menu: Parametrage = monde.menu
	var parametres := {
		"base": "default.yaml", "bases": ["default.yaml", "dev.yaml"],
		"roles": [
			{"role": "pharmacien", "palier": "grand", "modele": "qwen3:8b", "nombre": 6, "max": 60},
			{"role": "baigneur_occasionnel", "palier": "petit", "modele": "qwen3:1.7b", "nombre": 36, "max": 60},
			{"role": "vendeur", "palier": "regles", "modele": null, "nombre": 6, "max": 60},
			{"role": "meteo", "palier": "regles", "modele": null, "nombre": 1, "max": 1},
		],
		"zones": [{"zone": "plage", "nombre": 1, "max": 4}, {"zone": "pharmacie", "nombre": 1, "max": 4}],
		"modes": ["synchrone", "asynchrone"], "mode": "synchrone",
		"moteurs": ["llm", "regles"], "moteur": "llm",
		"duree_jours": 90, "duree_max": 120, "graine": 42,
	}
	menu.ouvrir()
	menu.remplir(parametres)
	verifier(menu.total() == 49 and menu._roles.size() == 4 and menu._zones.size() == 2, "le formulaire suit la description du pont")
	verifier(menu._resume.text.contains("6 en qwen3:8b") and menu._resume.text.contains("7 à règles"), "le résumé compte les agents par modèle")
	menu.repartir(25)
	verifier(menu.total() == 25 and int(menu._roles["meteo"].value) == 1, "le total se répartit au prorata, la météo reste seule")
	verifier(int(menu._roles["baigneur_occasionnel"].value) > int(menu._roles["pharmacien"].value), "en gardant les proportions de la base")
	menu._zones["plage"].value = 3
	menu._mode.select(1)
	var corps := menu.demande()
	verifier(corps["zones"]["plage"] == 3 and corps["mode"] == "asynchrone" and corps["population"]["meteo"] == 1 and corps["graine"] == 42, "la demande de lancement rassemble les réglages")
	monde._sur_reponse("/lancements", 422, {"detail": [{"loc": ["body", "zones", "plage"], "msg": "Input should be less than or equal to 4"}]})
	verifier(menu.est_ouvert() and menu._retour.text.contains("zones.plage"), "un refus du pont s'affiche dans le menu")
	monde._sur_reponse("/lancements", 202, {"statut": "en_cours", "run_id": null, "dernieres_lignes": []})
	verifier(not menu.est_ouvert() and not monde._suivi_lancement.is_stopped(), "un lancement accepté ferme le menu et suit le run")
	monde._sur_reponse("/lancements", 200, {"statut": "echec", "code": 1, "dernieres_lignes": ["Erreur : modèles absents : qwen3:8b"]})
	verifier(menu.est_ouvert() and menu._retour.text.contains("modèles absents") and monde._suivi_lancement.is_stopped(), "un run en échec rouvre le menu avec sa sortie")

	print("test_monde : %s" % ("OK" if _echecs == 0 else "%d échec(s)" % _echecs))
	quit(1 if _echecs > 0 else 0)
