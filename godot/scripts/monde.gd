# Scène principale (étape 10) : la station balnéaire animée par le flux du pont.
#
# Le même code sert le direct et le rejeu : il ne fait qu'appliquer les messages reçus (D1).
# Un clic sur un habitant ouvre sa fiche (D2) ; le panneau du bas injecte une perturbation ou
# pilote le run (D3). Le décor suit le plan du run (`monde.plan`, nombre de zones de chaque type),
# et l'interface de paramétrage (« Nouvelle simulation », ouverte au démarrage si aucun run ne
# tourne) lance un run à travers le pont (étape 15 bis).
#
# Options (après `--`) : --pont http://127.0.0.1:8000  --rejeu <run_id>  --vitesse 2
#   --parametrage (ouvre l'interface de paramétrage dès le démarrage)
#   et, pour les vérifications automatiques : --capture fichier.png --apres 8 --inspecter <agent_id>
extends Node3D

const NOMS_METEO := {"ensoleille": "ensoleillé", "nuageux": "nuageux", "pluie": "pluie", "canicule": "canicule"}

var decor: Decor
var pont: Pont
var ui: Interface
var menu: Parametrage
var habitants := {}  # agent_id -> Habitant
var _places := {}  # zone -> Array de [position, agent_id ou ""]
var _place_de := {}  # agent_id -> [zone, indice]
var _jour := 0
var _meteo := "—"
var _orage_du_jour := false  # un orage injecté aujourd'hui : la pluie du jour en vient
var _mode := "en direct"
var _messages_jour := 0
var _incidents := 0
var _mesures := {}  # dernières valeurs globales du service des indicateurs (étape 14)
var _selection: Habitant
var _fiche_serveur := {}  # dernière fiche reçue du pont pour l'agent sélectionné
var _capture := ""
var _apres := 8.0
var _inspecter := ""
var _menu_au_demarrage := true  # ouvert au premier instantané si aucun run ne tourne
var _suivi_lancement: Timer
var _etat_lancement := ""

var _camera: Camera3D
var _cible := Vector3(-2, 0, -2)
var _distance := 58.0
var _lacet := 0.12
var _tangage := -0.93
var _rotation_active := false


func _ready() -> void:
	decor = Decor.new()
	add_child(decor)
	decor.construire()
	_preparer_places()
	_camera = Camera3D.new()
	_camera.fov = 40.0
	_camera.far = 400.0
	add_child(_camera)
	_placer_camera()

	ui = Interface.new()
	add_child(ui)
	ui.construire(Decor.COULEURS_SENS)
	ui.definir_zones(_zones_d_injection())
	ui.parametrage_demande.connect(_ouvrir_parametrage)
	ui.commande.connect(func(nom): pont.envoyer("/commandes", {"commande": nom}))
	ui.injection.connect(_injecter)
	ui.rejeu_demande.connect(func(run_id, vitesse): pont.rejouer(run_id, vitesse))
	ui.direct_demande.connect(func(): pont.passer_en_direct())
	ui.fiche_fermee.connect(_deselectionner)

	menu = Parametrage.new()
	add_child(menu)
	menu.construire(Decor.NOMS_ZONES)
	menu.base_demandee.connect(func(base): pont.obtenir("/parametres?base=%s" % base.uri_encode()))
	menu.lancement_demande.connect(func(corps): pont.envoyer("/lancements", corps))
	_suivi_lancement = Timer.new()
	_suivi_lancement.wait_time = 2.0
	_suivi_lancement.timeout.connect(func(): pont.obtenir("/lancements"))
	add_child(_suivi_lancement)

	pont = Pont.new()
	add_child(pont)
	pont.message_recu.connect(_sur_message)
	pont.etat_connexion.connect(func(ok, detail): ui.afficher_connexion(detail, ok))
	pont.reponse.connect(_sur_reponse)
	_lire_options()
	if pont.rejeu != "" or _capture != "":
		_menu_au_demarrage = false
	pont.connecter()
	pont.obtenir("/runs")
	pont.obtenir("/lancements")  # un run lancé plus tôt par le pont est suivi jusqu'au bout
	if OS.get_cmdline_user_args().has("--parametrage"):
		_menu_au_demarrage = false
		_ouvrir_parametrage()
	_rafraichir()
	if _capture != "":
		_capturer()


func _lire_options() -> void:
	var args := OS.get_cmdline_user_args()
	for i in args.size() - 1:
		match args[i]:
			"--pont":
				pont.url_http = args[i + 1]
			"--rejeu":
				pont.rejeu = args[i + 1]
			"--vitesse":
				pont.vitesse = float(args[i + 1])
			"--capture":
				_capture = args[i + 1]
			"--apres":
				_apres = float(args[i + 1])
			"--inspecter":
				_inspecter = args[i + 1]


func _capturer() -> void:
	await get_tree().create_timer(_apres).timeout
	if _inspecter != "" and habitants.has(_inspecter):
		_selectionner(habitants[_inspecter])
		await get_tree().create_timer(1.5).timeout
	await RenderingServer.frame_post_draw
	get_viewport().get_texture().get_image().save_png(_capture)
	get_tree().quit()


# --- flux du pont -------------------------------------------------------------------------------


func _sur_message(d: Dictionary) -> void:
	var type: String = d.get("type", "")
	var p: Dictionary = d.get("payload", {})
	var qui: String = d.get("source_agent_id", "")
	match type:
		"pont.instantane":
			_instantane(d)
		"pont.nouveau_run":
			_vider()
			_mode = "en direct"
			ui.ajouter_au_fil("[b]Nouveau run[/b] %s" % d.get("run_id", ""))
		"pont.rejeu":
			_vider()
			_reconstruire([])  # un run sans plan a les sept zones d'origine
			_mode = "rejeu de %s" % d.get("run_id", "")
			ui.mode_rejeu(true)
			ui.ajouter_au_fil("[b]Rejeu[/b] de %s (%d événements, aucun appel LLM)" % [d.get("run_id", ""), d.get("evenements", 0)])
		"pont.fin_rejeu":
			ui.ajouter_au_fil("[b]Fin du rejeu[/b]")
		"pont.erreur":
			ui.ajouter_au_fil("[color=#ff8a80]%s[/color]" % d.get("detail", ""))
		"monde.plan":
			_reconstruire(p.get("lieux", []))
			if p.get("lieux", []).size() != Decor.LIEUX_ORIGINE.size():
				ui.ajouter_au_fil("[b]Plan du monde[/b] : %d zones" % decor.lieux.size())
		"horloge.pas":
			_jour = int(p["jour"])
			_messages_jour = 0
			_orage_du_jour = false
			ui.ajouter_au_fil("[color=#9fb3c8]— Jour %d —[/color]" % _jour)
		"horloge.fin_jour":
			decor.vider_jauges()  # les zones vides n'ont pas de nouvelle valeur
		"indicateur.valeur":
			_indicateur(d, p)
		"monde.meteo":
			_afficher_meteo(p)
		"agent.decision":
			_decision(qui, d.get("zone"), p)
		"agent.deplacement":
			_installer(qui, p["vers"], false)
		"monde.stock":
			decor.afficher_stock(d.get("zone", ""), int(p["quantite"]))
		"agent.message":
			_message(qui, p)
		"monde.perturbation":
			if p["nature"] == "orage":  # le bulletin de pluie du jour suit la perturbation
				_orage_du_jour = true
			var zone = d.get("zone")
			for z in decor.lieux_concernes(zone if zone else ""):
				decor.signaler_zone(z, Color("ff5a3c"), 8.0)
			ui.ajouter_au_fil("[color=#ff9e80][b]Perturbation[/b] %s%s : %s[/color]" % [p["nature"], " (%s)" % decor.nom_lieu(zone) if zone else "", p["description"]])
		"controle.injection":
			ui.ajouter_au_fil("[color=#ffd180]Injection demandée : %s[/color]" % p["description"])
		"controle.commande":
			ui.ajouter_au_fil("[color=#ffd180]Commande : %s[/color]" % p["commande"])
		"incident":
			_incidents += 1
			var agent = p.get("agent_id")
			if agent and habitants.has(agent):
				habitants[agent].parler("⚠ %s" % p["nature"], Color("ff8a80"), 3.0)
			ui.ajouter_au_fil("[color=#ff8a80]Incident %s %s[/color]" % [p["nature"], agent if agent else ""])
	_rafraichir()


func _instantane(d: Dictionary) -> void:
	_vider()
	_reconstruire(d.get("lieux", []))
	_mode = "en direct"
	ui.mode_rejeu(false)
	var run = d.get("run")
	if _menu_au_demarrage:
		_menu_au_demarrage = false
		if run == null or not str(run.get("statut", "")) in ["en_cours", "pause"]:
			_ouvrir_parametrage()
	if run:
		_jour = int(run.get("jour", 0))
		_mode = "en direct · %s (%s)" % [run.get("run_id", ""), run.get("statut", "")]
	_orage_du_jour = d.get("perturbations", []).any(func(x): return x.get("nature") == "orage" and int(x.get("jour", -1)) == _jour)
	var meteo = d.get("meteo")
	if meteo:
		_afficher_meteo(meteo)
	for fiche in d.get("agents", []):
		var h := _habitant(fiche["agent_id"], fiche.get("zone", "domicile"))
		h.role = fiche.get("role", "")
		h.palier = fiche.get("palier", "")
		if fiche.get("sens", "") != "":
			h.definir_sens(fiche["sens"], Decor.COULEURS_SENS[fiche["sens"]], decor.materiau)
		h.definir_porteur(str(fiche.get("porteur", "0")) == "1")


func _afficher_meteo(m: Dictionary) -> void:
	decor.appliquer_meteo(m["conditions"], _orage_du_jour)
	var nom: String = "orage" if decor.effets.etat == "orage" else NOMS_METEO.get(m["conditions"], m["conditions"])
	_meteo = "%s, %d °C, UV %d" % [nom, round(m["temperature_c"]), m["indice_uv"]]


func _decision(agent_id: String, zone, p: Dictionary) -> void:
	var h := _habitant(agent_id, zone if zone else "domicile")
	if p.get("palier"):
		h.palier = p["palier"]
	h.decisions.push_front({"jour": _jour, "sens": p["sens"], "action": p["action"], "porteur": p["porteur"], "moteur": p["moteur"], "justification": p["justification"], "appel": p.get("appel_llm"), "motif": p.get("motif"), "sources": p.get("sources", [])})
	if h.decisions.size() > 10:
		h.decisions.resize(10)
	var avant := h.sens
	h.definir_sens(p["sens"], Decor.COULEURS_SENS[p["sens"]], decor.materiau)
	h.definir_porteur(p["porteur"])
	h.reagir(p["action"])
	if avant != "" and avant != p["sens"]:
		ui.ajouter_au_fil("%s : %s → [color=#%s]%s[/color]" % [agent_id, avant, Decor.COULEURS_SENS[p["sens"]].to_html(false), p["sens"]])
	if p["moteur"] == "repli":
		h.parler("(repli sur règle)", Color("ffcc80"), 3.0)
	if h == _selection:
		_afficher_fiche()


func _indicateur(d: Dictionary, p: Dictionary) -> void:
	## Valeurs du service des indicateurs (B2) : globales dans le panneau et la courbe, par zone
	## dans les jauges du décor. Elles arrivent avec le jour qu'elles décrivent, en direct comme
	## en rejeu.
	var nom: String = p.get("nom", "")
	var groupe = p.get("groupe")
	var jour := int(d.get("sim_time_occurred", 0)) / 1440 + 1
	if groupe == null:
		_mesures[nom] = float(p["valeur"])
		if nom == "sens_partage" and p.get("repartition") != null:
			_mesures["repartition"] = p["repartition"]
		ui.courbe.ajouter(nom, jour, float(p["valeur"]))
	elif nom == "sens_partage" and str(groupe).begins_with("zone:"):
		decor.afficher_jauge(str(groupe).trim_prefix("zone:"), p.get("repartition", {}), float(p["valeur"]))


func _message(agent_id: String, p: Dictionary) -> void:
	_messages_jour += 1
	if not habitants.has(agent_id):
		return
	var h: Habitant = habitants[agent_id]
	h.messages.push_front({"jour": _jour, "canal": p["canal"], "contenu": p["contenu"], "destinataires": p.get("destinataires", [])})
	if h.messages.size() > 10:
		h.messages.resize(10)
	var couleurs := {"zone": Color("ffffff"), "direct": Color("b3e5fc"), "reseaux": Color("c5cae9")}
	h.parler(p["contenu"], couleurs.get(p["canal"], Color.WHITE))
	if p["canal"] == "reseaux":
		decor.afficher_publication(agent_id, p["contenu"])
	var vers := " → %s" % ", ".join(p.get("destinataires", [])) if p["canal"] == "direct" else ""
	ui.ajouter_au_fil("[i]%s[/i] [%s%s] « %s »" % [agent_id, p["canal"], vers, p["contenu"].left(90)])


# --- habitants et emplacements ------------------------------------------------------------------


func _habitant(agent_id: String, zone: String) -> Habitant:
	if habitants.has(agent_id):
		return habitants[agent_id]
	var h := Habitant.new()
	add_child(h)
	h.configurer(agent_id, decor.materiau)
	h.clique.connect(_selectionner)
	habitants[agent_id] = h
	_installer(agent_id, zone, true)
	return h


func _installer(agent_id: String, zone: String, instantane: bool) -> void:
	if not _places.has(zone) or not habitants.has(agent_id):
		return
	if _place_de.has(agent_id):
		var ancienne: Array = _place_de[agent_id]
		_places[ancienne[0]][ancienne[1]][1] = ""
	var places: Array = _places[zone]
	var cible := decor.centre_zone(zone)
	var indice := -1
	for i in places.size():
		if places[i][1] == "":
			indice = i
			break
	if indice >= 0:
		places[indice][1] = agent_id
		cible = places[indice][0]
		_place_de[agent_id] = [zone, indice]
	else:  # zone pleine : autour du centre
		_place_de.erase(agent_id)
		cible += Vector3(randf_range(-2, 2), 0, randf_range(-2, 2))
	var h: Habitant = habitants[agent_id]
	h.zone = zone
	h.aller_vers(cible, instantane)


func _preparer_places() -> void:
	_places.clear()
	_place_de.clear()
	for zone in decor.lieux:
		_places[zone] = decor.emplacements(zone).map(func(p): return [p, ""])


func _reconstruire(liste: Array) -> void:
	## Décor du plan reçu ; sans liste, les sept zones d'origine. Les habitants déjà là sont
	## replacés, les jauges et la liste des zones d'injection suivent.
	var voulus: Array = liste if liste.size() > 0 else Decor.LIEUX_ORIGINE
	if voulus == decor.lieux:
		return
	decor.construire(voulus)
	_preparer_places()
	for agent_id in habitants:
		_installer(agent_id, habitants[agent_id].zone, true)
	ui.definir_zones(_zones_d_injection())
	_cadrer()


func _zones_d_injection() -> Array:
	## [identifiant, nom] : chaque type ; s'il a plusieurs exemplaires, le type entier puis
	## chacun d'eux (une perturbation sur un type touche tous ses exemplaires).
	var zones: Array = []
	var vus := {}
	for lieu in decor.lieux:
		var type := Decor.type_de(lieu)
		if type == lieu:
			zones.append([lieu, decor.nom_lieu(lieu)])
			continue
		if not vus.has(type):
			vus[type] = true
			zones.append([type, "%s (tous)" % Decor.NOMS_ZONES.get(type, type)])
		zones.append([lieu, decor.nom_lieu(lieu)])
	return zones


func _cadrer() -> void:
	## Caméra sur tout le monde construit ; la vue d'origine pour les sept zones d'origine.
	if decor.est_d_origine():
		_cible = Vector3(-2, 0, -2)
		_distance = 58.0
	else:
		var b := decor.bornes()
		_cible = Vector3(b.get_center().x, 0, b.get_center().y)
		_distance = clampf(maxf(b.size.x, b.size.y * 1.6) * 1.05, 58.0, 190.0)
	_placer_camera()


func _vider() -> void:
	for h in habitants.values():
		h.queue_free()
	habitants.clear()
	_place_de.clear()
	for zone in _places:
		for place in _places[zone]:
			place[1] = ""
	_selection = null
	_incidents = 0
	_messages_jour = 0
	_mesures = {}
	ui.courbe.vider()
	decor.vider_jauges()
	ui.cacher_fiche()


func _rafraichir() -> void:
	var comptes := {}
	var porteurs := 0
	for h in habitants.values():
		if h.sens != "":
			comptes[h.sens] = comptes.get(h.sens, 0) + 1
		porteurs += int(h.porteur)
	ui.afficher_indicateurs(comptes, porteurs, habitants.size(), _messages_jour, _incidents, _mesures)
	ui.afficher_etat(_jour, _meteo, _mode)


# --- inspection (D2) ----------------------------------------------------------------------------


func _selectionner(h: Habitant) -> void:
	if _selection:
		_selection.selectionner(false)
	_selection = h
	_fiche_serveur = {}
	h.selectionner(true)
	_afficher_fiche()
	if pont.rejeu == "":
		pont.obtenir("/agents/%s" % h.agent_id)


func _deselectionner() -> void:
	if _selection:
		_selection.selectionner(false)
	_selection = null


func _afficher_fiche() -> void:
	var h := _selection
	var etat: Dictionary = _fiche_serveur.get("etat", {}) if _fiche_serveur.get("etat") else {}
	var lignes: Array[String] = []
	var palier := h.palier if h.palier != "" else str(etat.get("palier", ""))
	lignes.append("[b]%s[/b]   %s · palier %s" % [h.agent_id, etat.get("role", h.role), palier])
	var couleur := (Decor.COULEURS_SENS[h.sens] as Color).to_html(false) if h.sens != "" else "ffffff"
	lignes.append("Zone : %s · Sens : [color=#%s]%s[/color] · %s" % [decor.nom_lieu(h.zone), couleur, h.sens if h.sens != "" else "—", "porteur" if h.porteur else "non porteur"])
	if etat.get("seuil", "") != "":
		lignes.append("Seuil d'adoption : %d %%" % round(float(etat["seuil"]) * 100))
	if etat.has("regle"):
		lignes.append("[i]Règle : %s[/i]" % etat["regle"])
	if h.decisions.size() > 0:
		var d: Dictionary = h.decisions[0]
		var cout := ""
		if d["appel"]:
			cout = " · %s, %.1f s, %d tokens" % [d["appel"]["modele"], d["appel"]["duree_s"], d["appel"]["tokens_sortie"]]
			if int(d["appel"]["tentatives"]) > 1:
				cout += ", %d essais" % d["appel"]["tentatives"]
		lignes.append("\n[b]Dernière décision[/b] (jour %d, %s%s)" % [d["jour"], d["moteur"], cout])
		lignes.append("%s — « %s »" % [d["action"], d["justification"]])
		if d.get("motif"):
			lignes.append("[i]Pourquoi aujourd'hui : %s[/i]" % d["motif"])
		var lus: Array = []
		var souvenirs: Array = []
		for s in d.get("sources", []):
			if str(s).begins_with("corpus:"):
				lus.append(str(s).trim_prefix("corpus:"))
			else:
				souvenirs.append("J" + str(s).rsplit(":", true, 1)[1].trim_prefix("j"))
		if lus.size() > 0:
			lignes.append("Avait lu : %s" % ", ".join(lus))
		if souvenirs.size() > 0:
			lignes.append("Se souvenait de : %s" % ", ".join(souvenirs))
	elif etat.get("justification", "") != "":
		lignes.append("\n[b]Dernière décision[/b] : %s — « %s »" % [etat.get("action", ""), etat["justification"]])
	var recus: Array = etat.get("messages_recus", [])
	if recus.size() > 0:
		lignes.append("\n[b]Messages reçus[/b]")
		for m in recus:
			lignes.append("· %s [%s] : %s" % [m.get("de", ""), m.get("canal", ""), str(m.get("contenu", "")).left(100)])
	if h.messages.size() > 0:
		lignes.append("\n[b]Messages envoyés[/b]")
		for m in h.messages.slice(0, 4):
			lignes.append("· J%d [%s] %s" % [m["jour"], m["canal"], m["contenu"].left(100)])
	if h.decisions.size() > 1:
		lignes.append("\n[b]Décisions précédentes[/b]")
		for d in h.decisions.slice(1, 6):
			lignes.append("· J%d %s · %s" % [d["jour"], d["sens"], d["action"]])
	ui.afficher_fiche("\n".join(lignes))


# --- requêtes et réponses du pont ---------------------------------------------------------------


func _injecter(nature: String, zone: String, description: String) -> void:
	var corps := {"nature": nature, "description": description}
	if zone != "":
		corps["zone"] = zone
	pont.envoyer("/injections", corps)


func _sur_reponse(chemin: String, code: int, donnees) -> void:
	if chemin == "/runs" and code == 200:
		ui.definir_runs(donnees)
	elif chemin.begins_with("/agents/") and code == 200 and _selection and donnees.get("agent_id") == _selection.agent_id:
		_fiche_serveur = donnees
		_afficher_fiche()
	elif chemin == "/injections":
		ui.afficher_retour("Injection envoyée : effet au début du jour %d." % (_jour + 1) if code == 202 else "Injection refusée (%d) : %s" % [code, _detail(donnees)])
	elif chemin == "/commandes":
		ui.afficher_retour("Commande envoyée, appliquée entre deux jours." if code == 202 else "Commande refusée (%d) : %s" % [code, _detail(donnees)])
	elif chemin.begins_with("/parametres"):
		if code == 200:
			menu.remplir(donnees)
		else:
			menu.afficher_retour("Paramètres indisponibles (%d) : %s" % [code, _detail(donnees)], true)
	elif chemin == "/lancements":
		match code:
			202:  # demande acceptée : configuration écrite, processus du run démarré
				menu.fermer()
				_etat_lancement = ""
				ui.ajouter_au_fil("[b]Lancement[/b] : run en préparation (modèles, corpus)…")
				_suivre_lancement(donnees)
			200:
				_suivre_lancement(donnees)
			_:
				menu.lancement_refuse("Lancement refusé (%d) : %s" % [code, _detail(donnees)])


# --- interface de paramétrage (étape 15 bis) ----------------------------------------------------


func _ouvrir_parametrage() -> void:
	menu.ouvrir()
	pont.obtenir("/parametres?base=%s" % menu.base_choisie().uri_encode())


func _suivre_lancement(etat) -> void:
	## État du run lancé par le pont : relevé toutes les deux secondes tant qu'il tourne ; un
	## échec rouvre le paramétrage avec la fin de sa sortie.
	if not etat is Dictionary:
		return
	var statut: String = etat.get("statut", "aucun")
	if statut == "en_cours":
		if _suivi_lancement.is_stopped():
			_suivi_lancement.start()
	else:
		_suivi_lancement.stop()
	if statut == _etat_lancement:
		return
	var premier := _etat_lancement == ""
	_etat_lancement = statut
	var lignes: Array = etat.get("dernieres_lignes", [])
	match statut:
		"termine":
			if not premier:
				ui.ajouter_au_fil("[b]Run terminé[/b] %s" % str(etat.get("run_id", "")))
		"echec":
			if premier:
				return
			for ligne in lignes.slice(-4):
				ui.ajouter_au_fil("[color=#ff8a80]%s[/color]" % str(ligne).left(160))
			menu.ouvrir()
			menu.lancement_refuse("Le run s'est arrêté (code %s) : %s" % [str(etat.get("code")), str(lignes[-1]) if lignes.size() > 0 else "voir %s" % etat.get("journal", "")])


func _detail(donnees) -> String:
	if donnees is Dictionary and donnees.has("detail"):
		var detail = donnees["detail"]
		if detail is Array:  # erreurs de validation : champ et message
			return "; ".join(detail.map(func(e): return "%s : %s" % [".".join(e.get("loc", []).slice(1).map(func(x): return str(x))), e.get("msg", "")]))
		return str(detail)
	return "pont injoignable"


# --- caméra -------------------------------------------------------------------------------------


func _placer_camera() -> void:
	var direction := Vector3(sin(_lacet) * cos(_tangage), -sin(_tangage), cos(_lacet) * cos(_tangage))
	_camera.position = _cible + direction * _distance
	_camera.look_at(_cible)
	decor.suivre(_cible)


func _unhandled_input(evenement: InputEvent) -> void:
	if evenement is InputEventMouseButton:
		match evenement.button_index:
			MOUSE_BUTTON_WHEEL_UP:
				_distance = max(12.0, _distance * 0.9)
			MOUSE_BUTTON_WHEEL_DOWN:
				_distance = min(200.0, _distance * 1.1)
			MOUSE_BUTTON_RIGHT:
				_rotation_active = evenement.pressed
		_placer_camera()
	elif evenement is InputEventMouseMotion and _rotation_active:
		_lacet -= evenement.relative.x * 0.005
		_tangage = clamp(_tangage - evenement.relative.y * 0.005, -1.45, -0.25)
		_placer_camera()


func _process(delta: float) -> void:
	var dir := Vector3.ZERO
	if Input.is_key_pressed(KEY_LEFT) or Input.is_key_pressed(KEY_Q):
		dir.x -= 1
	if Input.is_key_pressed(KEY_RIGHT) or Input.is_key_pressed(KEY_D):
		dir.x += 1
	if Input.is_key_pressed(KEY_UP) or Input.is_key_pressed(KEY_Z):
		dir.z -= 1
	if Input.is_key_pressed(KEY_DOWN) or Input.is_key_pressed(KEY_S):
		dir.z += 1
	if dir != Vector3.ZERO:
		_cible += dir.rotated(Vector3.UP, _lacet).normalized() * delta * 25.0
		_placer_camera()
