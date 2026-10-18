# Maquettes de la scène 3D (étape 10) : trois directions visuelles à comparer, même contenu.
#
# Chaque variante montre les 7 zones, une trentaine d'habitants animés dont la pastille donne le
# sens qu'ils attribuent au produit, et le même bandeau d'indicateurs. Les données sont fictives :
# la scène définitive sera alimentée par le pont (WebSocket) une fois la variante choisie.
#
# Lancer : godot --path godot res://maquettes/maquette.tscn -- --variante A [--capture fichier.png]
#   A : station balnéaire (modèles Kenney, ville organique au bord de l'eau)
#   B : îlots (une plateforme par zone autour des réseaux sociaux, formes simples et couleurs pastel)
#   C : quartier (rues et bâtiments KayKit en damier, plage au sud)
extends Node3D

const COULEURS_SENS := {
	"efficace": Color("2e9e5b"),
	"tendance": Color("e8558e"),
	"banal": Color("9aa3ae"),
	"depasse": Color("7c5cc4"),
}
const PERSONNAGES := [
	"female-a", "male-a", "female-b", "male-b", "female-c", "male-c",
	"female-d", "male-d", "female-e", "male-e", "female-f", "male-f",
]
const NOMS_ZONES := {
	"pharmacie": "Pharmacie",
	"plage": "Plage",
	"piscine": "Piscine",
	"reseaux_sociaux": "Réseaux sociaux",
	"centre_commercial": "Centre commercial",
	"terrain_sport": "Terrain de sport",
	"domicile": "Domicile",
}

var variante := "A"
var capture := ""
var mesure := false
var _hasard := RandomNumberGenerator.new()
var _materiaux := {}
var _marcheurs: Array = []  # [noeud, centre, rayon, vitesse, angle]


func _ready() -> void:
	var args := OS.get_cmdline_user_args()
	for i in args.size():
		if args[i] == "--variante" and i + 1 < args.size():
			variante = args[i + 1]
		elif args[i] == "--capture" and i + 1 < args.size():
			capture = args[i + 1]
		elif args[i] == "--mesure":
			mesure = true
	_hasard.seed = 42
	_environnement()
	match variante:
		"B":
			_ilots()
		"C":
			_quartier()
		_:
			_station()
	_bandeau()
	if mesure:
		await _mesurer()
	if capture != "":
		await get_tree().create_timer(1.2).timeout
		await RenderingServer.frame_post_draw
		get_viewport().get_texture().get_image().save_png(capture)
		get_tree().quit()


func _mesurer() -> void:
	# Coût de rendu après stabilisation : images par seconde, mémoire vidéo, appels de dessin.
	await get_tree().create_timer(3.0).timeout
	var images := Engine.get_frames_drawn()
	await get_tree().create_timer(5.0).timeout
	var ips := (Engine.get_frames_drawn() - images) / 5.0
	var vram := RenderingServer.get_rendering_info(RenderingServer.RENDERING_INFO_VIDEO_MEM_USED)
	var appels := RenderingServer.get_rendering_info(RenderingServer.RENDERING_INFO_TOTAL_DRAW_CALLS_IN_FRAME)
	var objets := RenderingServer.get_rendering_info(RenderingServer.RENDERING_INFO_TOTAL_OBJECTS_IN_FRAME)
	print("MESURE variante=%s ips=%.0f vram_mo=%.0f appels_dessin=%d objets=%d" % [variante, ips, vram / 1048576.0, appels, objets])
	if capture == "":
		get_tree().quit()


func _process(delta: float) -> void:
	for m in _marcheurs:
		m[4] += delta * m[3] / m[2]
		var n: Node3D = m[0]
		var centre: Vector3 = m[1]
		n.position = centre + Vector3(cos(m[4]), 0, sin(m[4])) * m[2]
		n.rotation.y = -m[4] + (PI if m[3] > 0 else 0.0)


# --- briques ------------------------------------------------------------------------------------


func materiau(couleur: Color, emission := 0.0, rugosite := 0.85) -> StandardMaterial3D:
	var cle := "%s-%s-%s" % [couleur.to_html(), emission, rugosite]
	if not _materiaux.has(cle):
		var m := StandardMaterial3D.new()
		m.albedo_color = couleur
		m.roughness = rugosite
		if emission > 0.0:
			m.emission_enabled = true
			m.emission = couleur
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


func cylindre(rayon: float, hauteur: float, pos: Vector3, couleur: Color, cotes := 32, emission := 0.0) -> MeshInstance3D:
	var mi := MeshInstance3D.new()
	var mesh := CylinderMesh.new()
	mesh.top_radius = rayon
	mesh.bottom_radius = rayon
	mesh.height = hauteur
	mesh.radial_segments = cotes
	mi.mesh = mesh
	mi.material_override = materiau(couleur, emission)
	mi.position = pos + Vector3(0, hauteur / 2.0, 0)
	add_child(mi)
	return mi


func toit(largeur: float, profondeur: float, hauteur: float, pos: Vector3, couleur: Color) -> void:
	var mi := MeshInstance3D.new()
	var mesh := PrismMesh.new()
	mesh.size = Vector3(largeur, hauteur, profondeur)
	mi.mesh = mesh
	mi.material_override = materiau(couleur)
	mi.position = pos + Vector3(0, hauteur / 2.0, 0)
	add_child(mi)


func sphere(rayon: float, pos: Vector3, couleur: Color, emission := 0.0) -> void:
	var mi := MeshInstance3D.new()
	var mesh := SphereMesh.new()
	mesh.radius = rayon
	mesh.height = rayon * 2.0
	mi.mesh = mesh
	mi.material_override = materiau(couleur, emission)
	mi.position = pos
	add_child(mi)


func modele(chemin: String, pos: Vector3, rot_deg := 0.0, echelle := 1.0) -> Node3D:
	var n: Node3D = load(chemin).instantiate()
	n.position = pos
	n.rotation_degrees.y = rot_deg
	n.scale = Vector3.ONE * echelle
	add_child(n)
	return n


func etiquette(texte: String, pos: Vector3, taille := 96) -> void:
	var l := Label3D.new()
	l.text = texte
	l.font_size = taille
	l.outline_size = 24
	l.modulate = Color.WHITE
	l.outline_modulate = Color(0.1, 0.12, 0.2)
	l.pixel_size = 0.01
	l.billboard = BaseMaterial3D.BILLBOARD_ENABLED
	l.no_depth_test = true
	l.position = pos
	add_child(l)


func personnage(pos: Vector3, sens: String, anim := "idle", rot_deg := 0.0, marche_rayon := 0.0) -> Node3D:
	var corps := Node3D.new()
	corps.position = pos
	corps.rotation_degrees.y = rot_deg
	add_child(corps)
	var nom: String = PERSONNAGES[_hasard.randi() % PERSONNAGES.size()]
	var n: Node3D = load("res://assets/kenney_personnages/character-%s.glb" % nom).instantiate()
	n.scale = Vector3.ONE * 2.0
	corps.add_child(n)
	# Pastille de sens sous les pieds : la couleur se lit de loin, même à l'échelle de la ville.
	var disque := MeshInstance3D.new()
	var mesh := CylinderMesh.new()
	mesh.top_radius = 0.6
	mesh.bottom_radius = 0.6
	mesh.height = 0.04
	disque.mesh = mesh
	disque.material_override = materiau(COULEURS_SENS[sens], 0.6)
	disque.position.y = 0.03
	corps.add_child(disque)
	var lecteur: AnimationPlayer = n.find_child("AnimationPlayer", true, false)
	if lecteur and lecteur.has_animation(anim):
		lecteur.get_animation(anim).loop_mode = Animation.LOOP_LINEAR
		lecteur.play(anim)
		lecteur.seek(_hasard.randf() * lecteur.current_animation_length, true)
	if marche_rayon > 0.0:
		_marcheurs.append([corps, pos, marche_rayon, _hasard.randf_range(0.6, 1.0), _hasard.randf() * TAU])
	return corps


func foule(centre: Vector3, rayon: float, sens: Array, anims: Array = ["idle"]) -> void:
	for i in sens.size():
		var a := TAU * i / sens.size() + _hasard.randf_range(-0.3, 0.3)
		var r := rayon * _hasard.randf_range(0.35, 1.0)
		var anim: String = anims[i % anims.size()]
		var pos := centre + Vector3(cos(a) * r, 0, sin(a) * r)
		personnage(pos, sens[i], anim, _hasard.randf_range(0, 360), 1.5 if anim == "walk" else 0.0)


# --- environnement commun -----------------------------------------------------------------------


func _environnement() -> void:
	var env := Environment.new()
	var ciel := Sky.new()
	var ciel_mat := ProceduralSkyMaterial.new()
	ciel_mat.sky_top_color = Color("4f8fd8")
	ciel_mat.sky_horizon_color = Color("bfe0f5")
	ciel_mat.ground_horizon_color = Color("bfe0f5")
	ciel.sky_material = ciel_mat
	env.background_mode = Environment.BG_SKY
	env.sky = ciel
	env.ambient_light_source = Environment.AMBIENT_SOURCE_SKY
	env.ambient_light_energy = 0.9
	env.tonemap_mode = Environment.TONE_MAPPER_FILMIC
	var monde := WorldEnvironment.new()
	monde.environment = env
	add_child(monde)
	var soleil := DirectionalLight3D.new()
	soleil.rotation_degrees = Vector3(-52, -38, 0)
	soleil.light_energy = 1.25
	soleil.light_color = Color("fff3dc")
	soleil.shadow_enabled = true
	soleil.directional_shadow_max_distance = 120.0
	add_child(soleil)


func _camera(pos: Vector3, cible: Vector3, fov := 38.0, ortho := 0.0) -> void:
	var cam := Camera3D.new()
	add_child(cam)
	cam.position = pos
	cam.look_at(cible)
	if ortho > 0.0:
		cam.projection = Camera3D.PROJECTION_ORTHOGONAL
		cam.size = ortho
	else:
		cam.fov = fov
	cam.far = 400.0
	cam.current = true


func _mer(z_debut: float, largeur := 240.0) -> void:
	boite(Vector3(largeur, 0.05, 120), Vector3(0, -0.06, z_debut + 60), Color("2f86c6"), 0.0)
	boite(Vector3(largeur, 0.04, 2.0), Vector3(0, -0.04, z_debut + 0.6), Color("8fd3ef"), 0.2)  # écume


func _piscine(centre: Vector3, rot := 0.0) -> void:
	boite(Vector3(11, 0.25, 8), centre, Color("eeeae2"))
	boite(Vector3(8, 0.04, 5), centre + Vector3(0, 0.25, 0), Color("39b8e6"), 0.25)
	for i in 3:
		boite(Vector3(1.0, 0.25, 2.0), centre + Vector3(-3 + i * 3, 0.25, 3.3), Color("f4a259"))
		modele("res://assets/kenney_ville_commerces/detail-parasol-a.glb", centre + Vector3(-3.8 + i * 3, 0.25, 3.3), 0, 4.5)


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


func _ecran_reseaux(centre: Vector3, rot := 0.0) -> void:
	var pivot := Node3D.new()
	pivot.position = centre
	pivot.rotation_degrees.y = rot
	add_child(pivot)
	boite(Vector3(0.25, 3.0, 0.25), Vector3(-2.6, 0, 0), Color("39404d"), 0.0, pivot)
	boite(Vector3(0.25, 3.0, 0.25), Vector3(2.6, 0, 0), Color("39404d"), 0.0, pivot)
	boite(Vector3(6.2, 3.4, 0.3), Vector3(0, 2.6, 0), Color("1f2430"), 0.0, pivot)
	boite(Vector3(5.8, 3.0, 0.05), Vector3(0, 2.8, 0.17), Color("4c6fff"), 1.4, pivot)
	for i in 3:  # publications affichées sur l'écran
		boite(Vector3(4.6, 0.55, 0.04), Vector3(0, 3.0 + i * 0.85, 0.21), Color("ffffff"), 0.6, pivot)
		boite(Vector3(0.45, 0.45, 0.04), Vector3(-2.0, 3.05 + i * 0.85, 0.24), COULEURS_SENS.values()[i], 1.0, pivot)
	for i in 6:  # réactions qui flottent au-dessus de la place
		var a := TAU * i / 6.0
		sphere(0.22, centre + Vector3(cos(a) * 3.2, 4.2 + 0.6 * sin(i * 1.7), sin(a) * 3.2), COULEURS_SENS.values()[i % 4], 1.2)


func _croix_pharmacie(pos: Vector3, rot := 0.0, taille := 1.0) -> void:
	var pivot := Node3D.new()
	pivot.position = pos
	pivot.rotation_degrees.y = rot
	add_child(pivot)
	boite(Vector3(0.35, 1.1, 0.12) * taille, Vector3(0, -0.55 * taille, 0), Color("19c46b"), 2.0, pivot)
	boite(Vector3(1.1, 0.35, 0.12) * taille, Vector3(0, -0.175 * taille, 0), Color("19c46b"), 2.0, pivot)


# --- variante A : station balnéaire ------------------------------------------------------------


func _station() -> void:
	boite(Vector3(240, 0.1, 117), Vector3(0, -0.1, -51.5), Color("8cc56b"))  # herbe, jusqu'au sable
	boite(Vector3(240, 0.08, 9), Vector3(0, -0.06, 11), Color("f2dca4"))  # sable
	_mer(15.5)
	var plage := "res://assets/kenney_plage/"
	for x in [-26, -15, -4, 9, 21, 30]:
		modele(plage + "palm-detailed-bend.glb", Vector3(x, 0, 8.2), _hasard.randf_range(0, 360), 0.8)
	modele(plage + "boat-row-large.glb", Vector3(-10, -0.1, 18), 30, 1.0)
	modele(plage + "boat-row-small.glb", Vector3(14, -0.1, 20), -20, 1.0)
	modele(plage + "structure-platform-dock.glb", Vector3(24, 0, 16), 0, 1.0)
	for x in [-20, -12, -2, 6, 16]:
		modele("res://assets/kenney_ville_commerces/detail-parasol-b.glb", Vector3(x, 0, 12), 0, 3.5)
	etiquette(NOMS_ZONES["plage"], Vector3(-2, 4, 12))
	foule(Vector3(-4, 0, 11.0), 5.0, ["tendance", "tendance", "efficace", "banal", "tendance", "efficace", "depasse", "tendance"], ["sit", "idle", "walk", "idle"])

	var maisons := "res://assets/kenney_ville_maisons/"
	var types := ["a", "c", "e", "g", "k", "m"]
	for i in types.size():
		var p := Vector3(-30 + (i % 3) * 5.5, 0, -16 + int(i / 3) * 6)
		modele(maisons + "building-type-%s.glb" % types[i], p, 180, 3.0)
		modele(maisons + "tree-large.glb", p + Vector3(2.6, 0, 1.5), 0, 3.0)
	modele(maisons + "fence-3x3.glb", Vector3(-24, 0, -7), 0, 3.0)
	etiquette(NOMS_ZONES["domicile"], Vector3(-25, 6, -10))
	foule(Vector3(-24, 0, -7.5), 2.0, ["banal", "efficace"])

	var commerces := "res://assets/kenney_ville_commerces/"
	modele(commerces + "building-n.glb", Vector3(14, 0, -14), 180, 3.0)
	modele(commerces + "building-j.glb", Vector3(21.5, 0, -13), 180, 3.0)
	modele(commerces + "building-skyscraper-b.glb", Vector3(27, 0, -15), 180, 2.5)
	modele(commerces + "building-k.glb", Vector3(7, 0, -13), 180, 3.0)
	etiquette(NOMS_ZONES["centre_commercial"], Vector3(16, 9.5, -12))
	foule(Vector3(15, 0, -7.5), 3.5, ["tendance", "banal", "depasse", "tendance"], ["walk", "idle"])

	modele(commerces + "building-e.glb", Vector3(-2, 0, -13), 180, 3.0)
	_croix_pharmacie(Vector3(-2, 3.6, -11.4), 0, 1.4)
	etiquette(NOMS_ZONES["pharmacie"], Vector3(-2, 6.5, -11))
	foule(Vector3(-2, 0, -8.0), 2.0, ["efficace", "efficace", "banal"])

	cylindre(4.2, 0.3, Vector3(-11, 0, -3), Color("e4e7ee"), 48)
	_ecran_reseaux(Vector3(-11, 0.3, -5.5))
	etiquette(NOMS_ZONES["reseaux_sociaux"], Vector3(-11, 7.6, -5.5))
	foule(Vector3(-11, 0.3, -2), 2.8, ["tendance", "tendance", "tendance", "depasse", "efficace"], ["emote-yes", "idle", "emote-no"])

	_piscine(Vector3(14, 0, 1))
	etiquette(NOMS_ZONES["piscine"], Vector3(14, 3.5, 1))
	foule(Vector3(14, 0.25, 1), 3.0, ["efficace", "tendance", "banal", "efficace"], ["idle", "sit"])

	_terrain(Vector3(-27, 0, 1))
	etiquette(NOMS_ZONES["terrain_sport"], Vector3(-27, 3.5, 1))
	foule(Vector3(-27, 0.06, 1), 3.5, ["banal", "depasse", "banal", "efficace"], ["sprint", "walk"])

	var nature := "res://assets/kenney_nature/"
	for i in 26:
		var p := Vector3(_hasard.randf_range(-38, 36), 0, _hasard.randf_range(-30, -20))
		modele(nature + ["tree_default.glb", "tree_oak.glb", "tree_detailed.glb"][i % 3], p, _hasard.randf_range(0, 360), 3.0)
	_camera(Vector3(6, 34, 42), Vector3(-2, 0, -3), 40.0)


# --- variante B : îlots ------------------------------------------------------------------------


func _ilot(centre: Vector3, rayon: float, couleur_dessus: Color) -> void:
	cylindre(rayon, 1.6, centre + Vector3(0, -1.6, 0), Color("d9cbb0"), 6)
	cylindre(rayon - 0.15, 0.25, centre + Vector3(0, -0.25, 0), couleur_dessus, 6)


func _maison_simple(pos: Vector3, couleur: Color, toit_couleur: Color, l := 2.2, h := 1.8) -> void:
	boite(Vector3(l, h, l), pos, couleur)
	toit(l + 0.3, l + 0.3, 1.1, pos + Vector3(0, h, 0), toit_couleur)
	boite(Vector3(0.5, 0.9, 0.05), pos + Vector3(0, 0, l / 2.0 + 0.01), Color("7b4f32"))


func _ilots() -> void:
	boite(Vector3(300, 0.05, 300), Vector3(0, -2.2, 0), Color("6fc3df"))
	var centres := {
		"reseaux_sociaux": Vector3(0, 0, 0),
		"plage": Vector3(0, 0, 15),
		"piscine": Vector3(14, 0, 7.5),
		"centre_commercial": Vector3(14, 0, -7.5),
		"pharmacie": Vector3(0, 0, -15),
		"domicile": Vector3(-14, 0, -7.5),
		"terrain_sport": Vector3(-14, 0, 7.5),
	}
	var dessus := {
		"reseaux_sociaux": Color("e9e4f7"),
		"plage": Color("f3dfa8"),
		"piscine": Color("eef2f2"),
		"centre_commercial": Color("efe6dc"),
		"pharmacie": Color("e3f3e8"),
		"domicile": Color("cfe8b4"),
		"terrain_sport": Color("a9d98e"),
	}
	for z in centres:
		_ilot(centres[z], 7.2, dessus[z])
		etiquette(NOMS_ZONES[z], centres[z] + Vector3(0, 5.2, -3.5), 110)
	# Passerelles entre la place des réseaux et chaque îlot
	for z in centres:
		if z != "reseaux_sociaux":
			var c: Vector3 = centres[z]
			var pont := boite(Vector3(1.4, 0.15, c.length() - 13.0), c / 2.0 + Vector3(0, -0.3, 0), Color("c9b48f"))
			pont.rotation.y = atan2(c.x, c.z)  # la longueur de la passerelle suit l'axe z local

	_ecran_reseaux(Vector3(0, 0, -2.5))
	foule(Vector3(0, 0, 1.2), 3.0, ["tendance", "tendance", "tendance", "depasse", "efficace"], ["emote-yes", "idle", "emote-no"])

	var plage: Vector3 = centres["plage"]
	boite(Vector3(13, 0.05, 4), plage + Vector3(0, -0.02, 3.8), Color("49b3e0"), 0.2)
	for x in [-4, 0, 4]:
		cylindre(0.06, 2.0, plage + Vector3(x, 0, 0), Color("ffffff"))
		cylindre(1.3, 0.12, plage + Vector3(x, 2.0, 0), [Color("ff6b6b"), Color("ffd166"), Color("4ecdc4")][(x + 4) / 4], 12)
	foule(plage, 5.0, ["tendance", "efficace", "tendance", "banal", "tendance", "depasse"], ["sit", "idle"])

	var piscine: Vector3 = centres["piscine"]
	boite(Vector3(7, 0.04, 4.5), piscine + Vector3(0, 0, 0), Color("39b8e6"), 0.3)
	foule(piscine + Vector3(0, 0, 3.4), 2.0, ["efficace", "tendance", "banal"], ["sit", "idle"])

	var commerce: Vector3 = centres["centre_commercial"]
	boite(Vector3(8, 3.2, 4), commerce + Vector3(0, 0, -1.5), Color("f6c28b"))
	boite(Vector3(8.4, 0.3, 4.4), commerce + Vector3(0, 3.2, -1.5), Color("d27d4e"))
	boite(Vector3(6, 1.2, 0.06), commerce + Vector3(0, 0.2, 0.52), Color("a7d7f0"), 0.3)
	foule(commerce + Vector3(0, 0, 3), 2.5, ["tendance", "banal", "depasse", "tendance"], ["walk", "idle"])

	var pharmacie: Vector3 = centres["pharmacie"]
	boite(Vector3(5, 2.6, 4), pharmacie + Vector3(0, 0, -1), Color("ffffff"))
	boite(Vector3(5.4, 0.3, 4.4), pharmacie + Vector3(0, 2.6, -1), Color("19c46b"))
	_croix_pharmacie(pharmacie + Vector3(0, 2.3, 1.05), 0, 1.2)
	foule(pharmacie + Vector3(0, 0, 3), 1.8, ["efficace", "efficace", "banal"])

	var maisons: Vector3 = centres["domicile"]
	for i in 4:
		_maison_simple(maisons + Vector3(-2.6 + (i % 2) * 5.2, 0, -2.6 + int(i / 2) * 4.5), [Color("fbe7c6"), Color("cde7f0"), Color("f8d1d1"), Color("e2f0cb")][i], Color("b5654a"))
	foule(maisons + Vector3(0, 0, 1), 1.0, ["banal", "efficace"])

	var terrain: Vector3 = centres["terrain_sport"]
	boite(Vector3(9, 0.04, 5.5), terrain, Color("4caf50"))
	boite(Vector3(0.1, 0.02, 5.3), terrain + Vector3(0, 0.04, 0), Color("ffffff"))
	foule(terrain, 3.0, ["banal", "depasse", "banal", "efficace"], ["sprint", "walk"])

	_camera(Vector3(0, 38, 38), Vector3(0, 0, 0), 38.0, 46.0)


# --- variante C : quartier ---------------------------------------------------------------------


func _route(pos: Vector3, nom: String, rot := 0.0) -> void:
	modele("res://assets/kaykit_ville/%s.gltf" % nom, pos, rot, 2.5)


func _quartier() -> void:
	boite(Vector3(240, 0.1, 121), Vector3(0, -0.1, -49.5), Color("9ccf74"))
	boite(Vector3(240, 0.08, 8), Vector3(0, -0.06, 15), Color("f2dca4"))
	_mer(19.0)
	# Deux avenues qui se croisent ; une tuile de route KayKit fait 2 unités, ici 5.
	for i in range(-6, 7):
		if i != 0:
			_route(Vector3(i * 5, 0, 5), "road_straight", 90)
	_route(Vector3(0, 0, 5), "road_junction")
	for i in range(1, 6):
		_route(Vector3(0, 0, 5 - i * 5), "road_straight", 0)
	var kk := "res://assets/kaykit_ville/"
	modele(kk + "building_H.gltf", Vector3(9, 0, -3), 0, 2.5)
	modele(kk + "building_G.gltf", Vector3(15, 0, -3), 0, 2.5)
	modele(kk + "building_F.gltf", Vector3(21, 0, -3), 0, 2.5)
	etiquette(NOMS_ZONES["centre_commercial"], Vector3(15, 8.5, -3))
	foule(Vector3(15, 0, 1.5), 3.0, ["tendance", "banal", "depasse", "tendance"], ["walk", "idle"])
	modele(kk + "building_B.gltf", Vector3(-8, 0, -3), 0, 2.5)
	_croix_pharmacie(Vector3(-8, 3.8, -0.4), 0, 1.3)
	etiquette(NOMS_ZONES["pharmacie"], Vector3(-8, 5.2, 0))
	foule(Vector3(-8, 0, 1.6), 1.5, ["efficace", "efficace", "banal"])
	for i in 4:
		var p := Vector3(-30 + i * 5.5, 0, -4)
		modele("res://assets/kenney_ville_maisons/building-type-%s.glb" % ["b", "d", "f", "h"][i], p, 180, 3.0)
	etiquette(NOMS_ZONES["domicile"], Vector3(-22, 6, -4))
	foule(Vector3(-22, 0, 1.4), 2.0, ["banal", "efficace"])
	cylindre(4.0, 0.25, Vector3(8, 0, -16), Color("e4e7ee"), 48)
	_ecran_reseaux(Vector3(8, 0.25, -18.5))
	etiquette(NOMS_ZONES["reseaux_sociaux"], Vector3(8, 7.6, -18.5))
	foule(Vector3(8, 0.25, -15), 2.6, ["tendance", "tendance", "tendance", "depasse", "efficace"], ["emote-yes", "idle", "emote-no"])
	_piscine(Vector3(16, 0, 10.5))
	etiquette(NOMS_ZONES["piscine"], Vector3(16, 3.5, 10.5))
	foule(Vector3(16, 0.25, 10.5), 3.0, ["efficace", "tendance", "banal", "efficace"], ["idle", "sit"])
	_terrain(Vector3(-14, 0, -16))
	etiquette(NOMS_ZONES["terrain_sport"], Vector3(-14, 3.5, -16))
	foule(Vector3(-14, 0.06, -16), 3.5, ["banal", "depasse", "banal", "efficace"], ["sprint", "walk"])
	for x in [-24, -12, 4, 26]:
		modele("res://assets/kenney_plage/palm-straight.glb", Vector3(x, 0, 13), 0, 0.8)
	etiquette(NOMS_ZONES["plage"], Vector3(-6, 4, 15))
	foule(Vector3(-8, 0, 15), 7.0, ["tendance", "tendance", "efficace", "banal", "tendance", "efficace", "depasse", "tendance"], ["sit", "idle", "walk"])
	for x in [-18, -6, 6, 20]:
		modele(kk + "streetlight.gltf", Vector3(x, 0, 2.2), 0, 2.5)
	modele(kk + "car_taxi.gltf", Vector3(-12, 0, 6.2), 90, 2.5)
	modele(kk + "car_sedan.gltf", Vector3(10, 0, 3.8), -90, 2.5)
	_camera(Vector3(4, 36, 44), Vector3(-1, 0, -2), 40.0)


# --- bandeau d'indicateurs (identique pour les trois variantes) --------------------------------


func _bandeau() -> void:
	var calque := CanvasLayer.new()
	add_child(calque)
	var haut := PanelContainer.new()
	haut.position = Vector2(20, 18)
	calque.add_child(haut)
	var titre := Label.new()
	titre.text = "  Jour 12 / 90   ·   ensoleillé, 31 °C, UV 8   ·   Maquette %s  " % variante
	titre.add_theme_font_size_override("font_size", 22)
	haut.add_child(titre)

	var panneau := PanelContainer.new()
	panneau.position = Vector2(1300, 18)
	calque.add_child(panneau)
	var colonne := VBoxContainer.new()
	colonne.add_theme_constant_override("separation", 6)
	panneau.add_child(colonne)
	var t := Label.new()
	t.text = " Sens partagé   (adoption 38 %)"
	t.add_theme_font_size_override("font_size", 18)
	colonne.add_child(t)
	var parts := {"efficace": 0.27, "tendance": 0.41, "banal": 0.22, "depasse": 0.10}
	for s in parts:
		var ligne := HBoxContainer.new()
		colonne.add_child(ligne)
		var barre := ColorRect.new()
		barre.color = COULEURS_SENS[s]
		barre.custom_minimum_size = Vector2(150 * parts[s] / 0.41, 18)
		ligne.add_child(barre)
		var l := Label.new()
		l.text = " %s  %d %%" % [s, int(parts[s] * 100)]
		l.add_theme_font_size_override("font_size", 16)
		ligne.add_child(l)
