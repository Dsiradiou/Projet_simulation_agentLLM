# Décor de la station balnéaire (maquette A, choisie par le binôme le 18 octobre).
#
# Construit le terrain, les bâtiments de chaque zone et l'environnement, et fournit pour chaque
# zone une liste d'emplacements où les habitants se tiennent. Le décor ne dépend d'aucune donnée
# de la simulation : `monde.gd` y place les habitants et l'anime à partir du flux du pont.
class_name Decor
extends Node3D

const COULEURS_SENS := {
	"efficace": Color("2e9e5b"),
	"tendance": Color("e8558e"),
	"banal": Color("9aa3ae"),
	"depasse": Color("7c5cc4"),
}
const NOMS_ZONES := {
	"pharmacie": "Pharmacie",
	"plage": "Plage",
	"piscine": "Piscine",
	"reseaux_sociaux": "Réseaux sociaux",
	"centre_commercial": "Centre commercial",
	"terrain_sport": "Terrain de sport",
	"domicile": "Domicile",
}
# Zone où se tiennent les habitants : centre, taille (x, z) et hauteur du sol.
const AIRES := {
	"plage": [Vector3(-3, 0.0, 11.2), Vector2(40, 4.5)],
	"domicile": [Vector3(-24, 0.0, -6.8), Vector2(12, 4.5)],
	"centre_commercial": [Vector3(16, 0.0, -7.2), Vector2(16, 4.5)],
	"pharmacie": [Vector3(-2, 0.0, -7.6), Vector2(8, 4.0)],
	"reseaux_sociaux": [Vector3(-11, 0.3, -2.0), Vector2(6.5, 4.5)],
	"piscine": [Vector3(14, 0.25, 0.8), Vector2(9.5, 6.0)],
	"terrain_sport": [Vector3(-27, 0.06, 1.0), Vector2(12, 7.0)],
}
const ESPACEMENT := 1.35

var _materiaux := {}
var _soleil: DirectionalLight3D
var _ciel: ProceduralSkyMaterial
var _ecran: Label3D
var _halos := {}  # zone -> anneau de mise en évidence (perturbation)
var _stocks := {}  # point de vente -> étiquette du stock de la marque (étape 13)
var _jauges := {}  # zone -> [racine, {sens: segment}, étiquette, largeur] (étape 14)


func construire() -> void:
	_environnement()
	_terrain_et_mer()
	_plage()
	_domicile()
	_commerces()
	_pharmacie()
	_reseaux()
	_piscine(Vector3(14, 0, 1))
	_terrain(Vector3(-27, 0, 1))
	_arbres()
	for zone in AIRES:
		_halos[zone] = _halo(zone)
		_jauge(zone)
	_stocks["pharmacie"] = etiquette("", Vector3(-2, 5.4, -11), 64)
	_stocks["centre_commercial"] = etiquette("", Vector3(16, 8.4, -12), 64)


# --- emplacements -------------------------------------------------------------------------------


func emplacements(zone: String) -> Array[Vector3]:
	## Points où se tenir dans une zone, en grille, du centre vers les bords.
	var aire: Array = AIRES[zone]
	var centre: Vector3 = aire[0]
	var taille: Vector2 = aire[1]
	var points: Array[Vector3] = []
	var nx := int(taille.x / ESPACEMENT)
	var nz := int(taille.y / ESPACEMENT)
	for i in nx:
		for j in nz:
			var x := -taille.x / 2.0 + ESPACEMENT * (i + 0.5)
			var z := -taille.y / 2.0 + ESPACEMENT * (j + 0.5)
			points.append(centre + Vector3(x, 0, z))
	points.sort_custom(func(a, b): return a.distance_to(centre) < b.distance_to(centre))
	return points


func centre_zone(zone: String) -> Vector3:
	return AIRES[zone][0]


# --- effets pilotés par la simulation -----------------------------------------------------------


func appliquer_meteo(conditions: String) -> void:
	match conditions:
		"canicule":
			_soleil.light_energy = 1.45
			_soleil.light_color = Color("ffe1b0")
			_ciel.sky_top_color = Color("3f86d6")
		"nuageux":
			_soleil.light_energy = 0.75
			_soleil.light_color = Color("e8eef5")
			_ciel.sky_top_color = Color("8aa3bd")
		"pluie":
			_soleil.light_energy = 0.5
			_soleil.light_color = Color("d0d8e2")
			_ciel.sky_top_color = Color("6c7a8a")
		_:
			_soleil.light_energy = 1.25
			_soleil.light_color = Color("fff3dc")
			_ciel.sky_top_color = Color("4f8fd8")


func afficher_publication(auteur: String, texte: String) -> void:
	## Affiche la dernière publication des réseaux sociaux sur l'écran de la place.
	_ecran.text = "%s
« %s »" % [auteur, texte.left(140)]


func afficher_stock(zone: String, quantite: int) -> void:
	## Stock de flacons en rayon au point de vente ; rouge en rupture.
	if not _stocks.has(zone):
		return
	var l: Label3D = _stocks[zone]
	l.text = "En rayon : %d flacon%s" % [quantite, "s" if quantite > 1 else ""] if quantite > 0 else "Rupture de stock"
	l.modulate = Color("ffffff") if quantite > 0 else Color("ff8a80")


func afficher_jauge(zone: String, repartition: Dictionary, convergence: float) -> void:
	## Jauge posée au sol devant la zone : la part de chaque sens parmi les présents, et la
	## convergence de la zone, calculées par le service des indicateurs (étape 14).
	if not _jauges.has(zone):
		return
	var j: Array = _jauges[zone]
	var segments: Dictionary = j[1]
	var largeur: float = j[3]
	var total := 0
	for s in repartition:
		total += int(repartition[s])
	var x := -largeur / 2.0
	for s in COULEURS_SENS:
		var segment: MeshInstance3D = segments[s]
		var part := float(repartition.get(s, 0)) / maxi(total, 1)
		segment.visible = part > 0.0
		if segment.visible:
			var w := largeur * part
			(segment.mesh as BoxMesh).size = Vector3(w, 0.14, 0.5)
			segment.position = Vector3(x + w / 2.0, 0.1, 0)
			x += w
	(j[2] as Label3D).text = "%d · C %.2f" % [total, convergence] if total > 0 else ""


func vider_jauges() -> void:
	for zone in _jauges:
		afficher_jauge(zone, {}, 0.0)


func jauge(zone: String) -> Array:
	return _jauges.get(zone, [])


func signaler_zone(zone: String, couleur: Color, duree := 6.0) -> void:
	if not _halos.has(zone):
		return
	var halo: MeshInstance3D = _halos[zone]
	halo.material_override = materiau(Color(couleur, 0.45), 1.5)
	halo.visible = true
	var t := create_tween()
	t.tween_interval(duree)
	t.tween_callback(func(): halo.visible = false)


# --- briques ------------------------------------------------------------------------------------


func materiau(couleur: Color, emission := 0.0) -> StandardMaterial3D:
	var cle := "%s-%s" % [couleur.to_html(), emission]
	if not _materiaux.has(cle):
		var m := StandardMaterial3D.new()
		m.albedo_color = couleur
		m.roughness = 0.85
		if emission > 0.0:
			m.emission_enabled = true
			m.emission = Color(couleur, 1.0)
			m.emission_energy_multiplier = emission
		if couleur.a < 1.0:
			m.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA
		_materiaux[cle] = m
	return _materiaux[cle]


func boite(taille: Vector3, pos: Vector3, couleur: Color, emission := 0.0, parent: Node = self) -> MeshInstance3D:
	var mi := MeshInstance3D.new()
	var mesh := BoxMesh.new()
	mesh.size = taille
	mi.mesh = mesh
	mi.material_override = materiau(couleur, emission)
	mi.position = pos + Vector3(0, taille.y / 2.0, 0)
	parent.add_child(mi)
	return mi


func cylindre(rayon: float, hauteur: float, pos: Vector3, couleur: Color, cotes := 48) -> MeshInstance3D:
	var mi := MeshInstance3D.new()
	var mesh := CylinderMesh.new()
	mesh.top_radius = rayon
	mesh.bottom_radius = rayon
	mesh.height = hauteur
	mesh.radial_segments = cotes
	mi.mesh = mesh
	mi.material_override = materiau(couleur)
	mi.position = pos + Vector3(0, hauteur / 2.0, 0)
	add_child(mi)
	return mi


func modele(chemin: String, pos: Vector3, rot_deg := 0.0, echelle := 1.0) -> Node3D:
	var n: Node3D = load(chemin).instantiate()
	n.position = pos
	n.rotation_degrees.y = rot_deg
	n.scale = Vector3.ONE * echelle
	add_child(n)
	return n


func etiquette(texte: String, pos: Vector3, taille := 96) -> Label3D:
	var l := Label3D.new()
	l.text = texte
	l.font_size = taille
	l.outline_size = 24
	l.outline_modulate = Color(0.1, 0.12, 0.2)
	l.pixel_size = 0.01
	l.billboard = BaseMaterial3D.BILLBOARD_ENABLED
	l.no_depth_test = true
	l.position = pos
	add_child(l)
	return l


func _jauge(zone: String) -> void:
	var aire: Array = AIRES[zone]
	var centre: Vector3 = aire[0]
	var taille: Vector2 = aire[1]
	var racine := Node3D.new()
	racine.position = centre + Vector3(0, 0.05, taille.y / 2.0 + 0.9)
	add_child(racine)
	var largeur := minf(taille.x * 0.8, 10.0)
	boite(Vector3(largeur + 0.2, 0.06, 0.7), Vector3.ZERO, Color(0.1, 0.12, 0.2), 0.0, racine)
	var segments := {}
	for s in COULEURS_SENS:
		var segment := boite(Vector3(0.01, 0.14, 0.5), Vector3.ZERO, COULEURS_SENS[s], 0.4, racine)
		segment.mesh = segment.mesh.duplicate()
		segment.visible = false
		segments[s] = segment
	var l := etiquette("", racine.position + Vector3(largeur / 2.0 + 1.6, 0.5, 0), 44)
	_jauges[zone] = [racine, segments, l, largeur]


func _halo(zone: String) -> MeshInstance3D:
	var aire: Array = AIRES[zone]
	var taille: Vector2 = aire[1]
	var mi := boite(Vector3(taille.x + 1.5, 0.05, taille.y + 1.5), aire[0] + Vector3(0, 0.02, 0), Color(1, 0.3, 0.2, 0.4), 1.0)
	mi.visible = false
	return mi


# --- décor ---------------------------------------------------------------------------------------


func _environnement() -> void:
	var env := Environment.new()
	var ciel := Sky.new()
	_ciel = ProceduralSkyMaterial.new()
	_ciel.sky_top_color = Color("4f8fd8")
	_ciel.sky_horizon_color = Color("bfe0f5")
	_ciel.ground_horizon_color = Color("bfe0f5")
	ciel.sky_material = _ciel
	env.background_mode = Environment.BG_SKY
	env.sky = ciel
	env.ambient_light_source = Environment.AMBIENT_SOURCE_SKY
	env.ambient_light_energy = 0.9
	env.tonemap_mode = Environment.TONE_MAPPER_FILMIC
	var monde := WorldEnvironment.new()
	monde.environment = env
	add_child(monde)
	_soleil = DirectionalLight3D.new()
	_soleil.rotation_degrees = Vector3(-52, -38, 0)
	_soleil.shadow_enabled = true
	_soleil.directional_shadow_max_distance = 120.0
	add_child(_soleil)
	appliquer_meteo("ensoleille")


func _terrain_et_mer() -> void:
	boite(Vector3(240, 0.1, 117), Vector3(0, -0.1, -51.5), Color("8cc56b"))
	boite(Vector3(240, 0.08, 9), Vector3(0, -0.06, 11), Color("f2dca4"))
	boite(Vector3(240, 0.05, 120), Vector3(0, -0.06, 75.5), Color("2f86c6"))
	boite(Vector3(240, 0.04, 2.0), Vector3(0, -0.04, 16.1), Color("8fd3ef"), 0.2)


func _plage() -> void:
	var p := "res://assets/kenney_plage/"
	var hasard := RandomNumberGenerator.new()
	hasard.seed = 7
	for x in [-26, -15, -4, 9, 21, 30]:
		modele(p + "palm-detailed-bend.glb", Vector3(x, 0, 8.0), hasard.randf_range(0, 360), 0.8)
	modele(p + "boat-row-large.glb", Vector3(-10, -0.1, 19), 30)
	modele(p + "boat-row-small.glb", Vector3(14, -0.1, 21), -20)
	modele(p + "structure-platform-dock.glb", Vector3(24, 0, 16))
	for x in [-20, -12, 2, 16]:
		modele("res://assets/kenney_ville_commerces/detail-parasol-b.glb", Vector3(x, 0, 13.6), 0, 3.5)
	etiquette(NOMS_ZONES["plage"], Vector3(-3, 4, 12))


func _domicile() -> void:
	var m := "res://assets/kenney_ville_maisons/"
	var types := ["a", "c", "e", "g", "k", "m"]
	for i in types.size():
		var pos := Vector3(-30 + (i % 3) * 5.5, 0, -17 + int(i / 3) * 6)
		modele(m + "building-type-%s.glb" % types[i], pos, 180, 3.0)
		modele(m + "tree-large.glb", pos + Vector3(2.6, 0, 1.5), 0, 3.0)
	etiquette(NOMS_ZONES["domicile"], Vector3(-25, 6, -10))


func _commerces() -> void:
	var c := "res://assets/kenney_ville_commerces/"
	modele(c + "building-n.glb", Vector3(14, 0, -14), 180, 3.0)
	modele(c + "building-j.glb", Vector3(21.5, 0, -13), 180, 3.0)
	modele(c + "building-skyscraper-b.glb", Vector3(27, 0, -15), 180, 2.5)
	modele(c + "building-k.glb", Vector3(7, 0, -13), 180, 3.0)
	etiquette(NOMS_ZONES["centre_commercial"], Vector3(16, 9.5, -12))


func _pharmacie() -> void:
	modele("res://assets/kenney_ville_commerces/building-e.glb", Vector3(-2, 0, -13), 180, 3.0)
	var pivot := Node3D.new()
	pivot.position = Vector3(-2, 3.6, -11.4)
	add_child(pivot)
	boite(Vector3(0.49, 1.54, 0.12), Vector3(0, -0.77, 0), Color("19c46b"), 2.0, pivot)
	boite(Vector3(1.54, 0.49, 0.12), Vector3(0, -0.245, 0), Color("19c46b"), 2.0, pivot)
	etiquette(NOMS_ZONES["pharmacie"], Vector3(-2, 6.5, -11))


func _reseaux() -> void:
	cylindre(4.4, 0.3, Vector3(-11, 0, -2.6), Color("e4e7ee"))
	var pivot := Node3D.new()
	pivot.position = Vector3(-11, 0.3, -5.8)
	add_child(pivot)
	boite(Vector3(0.25, 3.0, 0.25), Vector3(-2.9, 0, 0), Color("39404d"), 0.0, pivot)
	boite(Vector3(0.25, 3.0, 0.25), Vector3(2.9, 0, 0), Color("39404d"), 0.0, pivot)
	boite(Vector3(6.8, 3.6, 0.3), Vector3(0, 2.5, 0), Color("1f2430"), 0.0, pivot)
	boite(Vector3(6.4, 3.2, 0.05), Vector3(0, 2.7, 0.17), Color("2e4bd8"), 1.2, pivot)
	_ecran = Label3D.new()
	_ecran.text = "Les publications des réseaux sociaux apparaissent ici."
	_ecran.font_size = 40
	_ecran.pixel_size = 0.01
	_ecran.width = 600
	_ecran.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	_ecran.modulate = Color.WHITE
	_ecran.outline_size = 0
	_ecran.position = Vector3(0, 4.3, 0.22)
	pivot.add_child(_ecran)
	etiquette(NOMS_ZONES["reseaux_sociaux"], Vector3(-11, 7.8, -5.8))


func _piscine(centre: Vector3) -> void:
	boite(Vector3(11, 0.25, 8), centre, Color("eeeae2"))
	boite(Vector3(8, 0.04, 5), centre + Vector3(0, 0.25, 0), Color("39b8e6"), 0.25)
	for i in 3:
		boite(Vector3(1.0, 0.25, 2.0), centre + Vector3(-3 + i * 3, 0.25, 3.3), Color("f4a259"))
	etiquette(NOMS_ZONES["piscine"], Vector3(14, 3.8, 1))


func _terrain(centre: Vector3) -> void:
	boite(Vector3(14, 0.06, 8.5), centre, Color("3f9b4a"))
	var blanc := Color("f5f5f0")
	boite(Vector3(13, 0.02, 0.12), centre + Vector3(0, 0.06, 3.9), blanc)
	boite(Vector3(13, 0.02, 0.12), centre + Vector3(0, 0.06, -3.9), blanc)
	boite(Vector3(0.12, 0.02, 7.8), centre + Vector3(0, 0.06, 0), blanc)
	for s in [-1, 1]:
		boite(Vector3(0.12, 0.02, 7.8), centre + Vector3(6.5 * s, 0.06, 0), blanc)
		boite(Vector3(0.1, 1.4, 0.1), centre + Vector3(6.5 * s, 0, 1.4), blanc)
		boite(Vector3(0.1, 1.4, 0.1), centre + Vector3(6.5 * s, 0, -1.4), blanc)
		boite(Vector3(0.1, 0.1, 2.9), centre + Vector3(6.5 * s, 1.35, 0), blanc)
	etiquette(NOMS_ZONES["terrain_sport"], Vector3(-27, 3.8, 1))


func _arbres() -> void:
	var hasard := RandomNumberGenerator.new()
	hasard.seed = 42
	var nature := "res://assets/kenney_nature/"
	for i in 26:
		var pos := Vector3(hasard.randf_range(-38, 36), 0, hasard.randf_range(-30, -21))
		modele(nature + ["tree_default.glb", "tree_oak.glb", "tree_detailed.glb"][i % 3], pos, hasard.randf_range(0, 360), 3.0)
