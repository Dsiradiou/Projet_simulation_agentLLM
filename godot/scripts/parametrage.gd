# Interface de paramétrage (étape 15 bis) : choisir et lancer une simulation depuis la scène.
#
# Le formulaire est construit à partir de la description donnée par le pont (`GET /parametres`) :
# les rôles avec leur palier et leur modèle, les types de zones, les modes et les bornes de chaque
# réglage. La scène ne connaît ni les rôles ni les règles du monde : elle rassemble des nombres ;
# le pont écrit la configuration, la valide et lance le run (`POST /lancements`).
#
# Elle ne parle pas au pont elle-même : elle émet des signaux, que `monde.gd` traduit en requêtes.
class_name Parametrage
extends CanvasLayer

signal base_demandee(base: String)
signal lancement_demande(corps: Dictionary)

const ROLES_LISIBLES := {
	"pharmacien": "Pharmaciens",
	"createur_contenu": "Créateurs de contenu",
	"journaliste": "Journalistes",
	"responsable_marque": "Responsable de marque",
	"habitue_soin": "Habitués du soin solaire",
	"baigneur_occasionnel": "Baigneurs occasionnels",
	"sceptique": "Sceptiques pragmatiques",
	"fidele_autre_marque": "Fidèles à une autre marque",
	"vendeur": "Vendeurs",
	"marque_concurrente": "Marque concurrente",
	"meteo": "Météo",
}
const PALIERS := {"grand": "Palier grand", "moyen": "Palier moyen", "petit": "Palier petit", "regles": "Agents à règles, sans LLM"}
const MODES := {"synchrone": "Synchrone : tous décident ensemble", "asynchrone": "Asynchrone : chacun à son rythme"}
const MOTEURS := {"llm": "Agents LLM (Ollama)", "regles": "Règles seules (baseline C0)"}

var noms_zones := {}
var _parametres := {}
var _panneau: PanelContainer
var _contenu: HBoxContainer
var _base: OptionButton
var _mode: OptionButton
var _moteur: OptionButton
var _duree: SpinBox
var _graine: SpinBox
var _total: SpinBox
var _roles := {}  # rôle -> SpinBox
var _zones := {}  # type de zone -> SpinBox
var _resume: Label
var _retour: Label
var _lancer: Button
var _en_cours := false  # répartition ou remplissage : les signaux des champs sont ignorés


func construire(noms: Dictionary) -> void:
	noms_zones = noms
	layer = 2  # au-dessus de l'interface de la scène
	_panneau = PanelContainer.new()
	_panneau.position = Vector2(200, 50)
	_panneau.custom_minimum_size = Vector2(1200, 0)
	var style := StyleBoxFlat.new()
	style.bg_color = Color(0.07, 0.09, 0.14, 0.96)
	style.set_corner_radius_all(10)
	style.set_content_margin_all(18)
	_panneau.add_theme_stylebox_override("panel", style)
	add_child(_panneau)
	var col := VBoxContainer.new()
	col.add_theme_constant_override("separation", 10)
	_panneau.add_child(col)

	var tete := HBoxContainer.new()
	tete.add_theme_constant_override("separation", 12)
	col.add_child(tete)
	var titre := _label("Nouvelle simulation", 22)
	titre.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	tete.add_child(titre)
	tete.add_child(_label("Configuration de base", 15))
	_base = OptionButton.new()
	_base.item_selected.connect(func(i): base_demandee.emit(_base.get_item_text(i)))
	tete.add_child(_base)
	tete.add_child(_bouton("✕", fermer))

	_contenu = HBoxContainer.new()
	_contenu.add_theme_constant_override("separation", 28)
	col.add_child(_contenu)
	_resume = _label("", 15)
	col.add_child(_resume)
	_retour = _label("Chargement des paramètres…", 14)
	_retour.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	_retour.custom_minimum_size.x = 1160
	col.add_child(_retour)
	var bas := HBoxContainer.new()
	bas.add_theme_constant_override("separation", 12)
	col.add_child(bas)
	_lancer = _bouton("Lancer la simulation", _demander_lancement)
	_lancer.disabled = true
	bas.add_child(_lancer)
	bas.add_child(_bouton("Valeurs de la base", func(): remplir(_parametres)))
	bas.add_child(_bouton("Fermer", fermer))
	_panneau.visible = false


# --- ouverture ----------------------------------------------------------------------------------


func ouvrir() -> void:
	_panneau.visible = true


func fermer() -> void:
	_panneau.visible = false


func est_ouvert() -> bool:
	return _panneau.visible


func base_choisie() -> String:
	return _parametres.get("base", "default.yaml")


func afficher_retour(texte: String, erreur := false) -> void:
	_retour.text = texte
	_retour.modulate = Color("ff8a80") if erreur else Color("c8d3e0")


func lancement_refuse(texte: String) -> void:
	_lancer.disabled = false
	afficher_retour(texte, true)


# --- formulaire ---------------------------------------------------------------------------------


func remplir(p: Dictionary) -> void:
	## Construit le formulaire à partir de la description du pont.
	_en_cours = true
	_parametres = p
	_roles.clear()
	_zones.clear()
	for enfant in _contenu.get_children():
		_contenu.remove_child(enfant)
		enfant.queue_free()
	_base.clear()
	for b in p.get("bases", []):
		_base.add_item(b)
		if b == p.get("base"):
			_base.select(_base.item_count - 1)

	var gauche := _colonne(360)
	gauche.add_child(_section("Exécution"))
	_mode = _choix(MODES, p.get("modes", []), p.get("mode", "synchrone"))
	gauche.add_child(_ligne("Mode", _mode))
	_moteur = _choix(MOTEURS, p.get("moteurs", []), p.get("moteur", "llm"))
	gauche.add_child(_ligne("Agents", _moteur))
	_duree = _nombre(1, int(p.get("duree_max", 90)), int(p.get("duree_jours", 90)))
	_duree.suffix = "jours"
	gauche.add_child(_ligne("Durée", _duree))
	_graine = _nombre(0, 999999, int(p.get("graine", 42)))
	gauche.add_child(_ligne("Graine", _graine))
	gauche.add_child(_section("Zones (nombre de chaque type)"))
	for z in p.get("zones", []):
		var champ := _nombre(1, int(z["max"]), int(z["nombre"]))
		_zones[z["zone"]] = champ
		gauche.add_child(_ligne(noms_zones.get(z["zone"], z["zone"]), champ))

	var droite := _colonne(760)
	droite.add_child(_section("Agents"))
	_total = _nombre(0, 1000, 0)
	_total.value_changed.connect(func(v): if not _en_cours: repartir(int(v)))
	var ligne_total := _ligne("Nombre total (réparti selon la base)", _total)
	droite.add_child(ligne_total)
	var grille := GridContainer.new()
	grille.columns = 2
	grille.add_theme_constant_override("h_separation", 26)
	grille.add_theme_constant_override("v_separation", 4)
	droite.add_child(grille)
	var palier_courant := ""
	var groupe: VBoxContainer
	for r in p.get("roles", []):
		if r["palier"] != palier_courant:
			palier_courant = r["palier"]
			groupe = VBoxContainer.new()
			groupe.custom_minimum_size.x = 360
			grille.add_child(groupe)
			var titre_groupe: String = PALIERS.get(palier_courant, palier_courant)
			if r.get("modele"):
				titre_groupe += " · %s" % r["modele"]
			var l := _label(titre_groupe, 14)
			l.modulate = Color("ffd166")
			groupe.add_child(l)
		var champ := _nombre(0, int(r["max"]), int(r["nombre"]))
		champ.value_changed.connect(func(_v): if not _en_cours: _actualiser())
		_roles[r["role"]] = champ
		groupe.add_child(_ligne(ROLES_LISIBLES.get(r["role"], r["role"]), champ, 230))
	for champ in _zones.values():
		champ.value_changed.connect(func(_v): if not _en_cours: _actualiser())
	_mode.item_selected.connect(func(_i): _actualiser())
	_moteur.item_selected.connect(func(_i): _actualiser())
	_en_cours = false
	_actualiser()
	_lancer.disabled = false
	afficher_retour("Base %s : ajuster puis lancer. Le pont valide la configuration avant de lancer le run." % p.get("base", ""))


func repartir(total: int) -> void:
	## Répartit `total` agents entre les rôles au prorata de la configuration de base (plus forts
	## restes) ; les rôles uniques (météo, marque concurrente) gardent leur valeur.
	var fixes := 0
	var ajustables: Array = []
	var poids := 0
	for r in _parametres.get("roles", []):
		if int(r["max"]) <= 1:
			fixes += int(_roles[r["role"]].value)
		else:
			ajustables.append(r)
			poids += int(r["nombre"])
	var reste := maxi(total - fixes, 0)
	var parts: Array = []
	var attribues := 0
	for r in ajustables:
		var w: float = float(r["nombre"]) if poids > 0 else 1.0
		var exact: float = reste * w / (poids if poids > 0 else ajustables.size())
		var n := mini(int(floor(exact)), int(r["max"]))
		parts.append([r["role"], n, exact - floor(exact), int(r["max"])])
		attribues += n
	parts.sort_custom(func(a, b): return a[2] > b[2])
	for part in parts:
		if attribues >= reste:
			break
		if part[1] < part[3]:
			part[1] += 1
			attribues += 1
	_en_cours = true
	for part in parts:
		_roles[part[0]].value = part[1]
	_en_cours = false
	_actualiser()


func total() -> int:
	var n := 0
	for champ in _roles.values():
		n += int(champ.value)
	return n


func demande() -> Dictionary:
	## Corps de `POST /lancements`.
	var population := {}
	for role in _roles:
		population[role] = int(_roles[role].value)
	var zones := {}
	for z in _zones:
		zones[z] = int(_zones[z].value)
	return {
		"base": base_choisie(),
		"population": population,
		"zones": zones,
		"mode": _mode.get_item_metadata(_mode.selected),
		"moteur": _moteur.get_item_metadata(_moteur.selected),
		"duree_jours": int(_duree.value),
		"graine": int(_graine.value),
	}


func _actualiser() -> void:
	## Total et résumé : agents par modèle, zones.
	_en_cours = true
	_total.value = total()
	_en_cours = false
	var regles_seules: bool = _moteur.get_item_metadata(_moteur.selected) == "regles"
	var par_modele := {}
	var a_regles := 0
	for r in _parametres.get("roles", []):
		var n := int(_roles[r["role"]].value)
		if r.get("modele") and not regles_seules:
			par_modele[r["modele"]] = par_modele.get(r["modele"], 0) + n
		else:
			a_regles += n
	var morceaux: Array[String] = []
	for m in par_modele:
		morceaux.append("%d en %s" % [par_modele[m], m])
	morceaux.append("%d à règles" % a_regles)
	var nb_zones := 0
	for champ in _zones.values():
		nb_zones += int(champ.value)
	_resume.text = "%d agents : %s   ·   %d zones   ·   mode %s" % [total(), ", ".join(morceaux), nb_zones, _mode.get_item_metadata(_mode.selected)]


func _demander_lancement() -> void:
	if total() == 0:
		afficher_retour("Il faut au moins un agent.", true)
		return
	_lancer.disabled = true
	afficher_retour("Lancement demandé au pont…")
	lancement_demande.emit(demande())


# --- briques ------------------------------------------------------------------------------------


func _label(texte: String, taille := 16) -> Label:
	var l := Label.new()
	l.text = texte
	l.add_theme_font_size_override("font_size", taille)
	return l


func _bouton(texte: String, action: Callable) -> Button:
	var b := Button.new()
	b.text = texte
	b.pressed.connect(action)
	return b


func _colonne(largeur: float) -> VBoxContainer:
	var c := VBoxContainer.new()
	c.custom_minimum_size.x = largeur
	c.add_theme_constant_override("separation", 5)
	_contenu.add_child(c)
	return c


func _section(texte: String) -> Label:
	var l := _label(texte, 17)
	l.modulate = Color("7fd1ff")
	return l


func _ligne(texte: String, champ: Control, largeur := 150.0) -> HBoxContainer:
	var h := HBoxContainer.new()
	var l := _label(texte, 15)
	l.custom_minimum_size.x = largeur
	h.add_child(l)
	h.add_child(champ)
	return h


func _nombre(minimum: int, maximum: int, valeur: int) -> SpinBox:
	var s := SpinBox.new()
	s.min_value = minimum
	s.max_value = maximum
	s.step = 1
	s.value = valeur
	s.custom_minimum_size.x = 110
	return s


func _choix(libelles: Dictionary, valeurs: Array, choisi: String) -> OptionButton:
	var o := OptionButton.new()
	for v in valeurs:
		o.add_item(libelles.get(v, v))
		o.set_item_metadata(o.item_count - 1, v)
		if v == choisi:
			o.select(o.item_count - 1)
	return o
