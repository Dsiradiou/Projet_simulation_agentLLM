# Un habitant de la station : la représentation d'un agent de la simulation.
#
# - la pastille sous ses pieds donne le sens qu'il attribue au produit (couleurs de Decor) ;
# - un petit flacon flotte au-dessus de sa tête s'il utilise le produit (porteur) ;
# - une bulle montre ses messages ; un geste « oui » ou « non » accompagne ses décisions ;
# - il marche jusqu'à sa nouvelle zone quand il se déplace ;
# - un clic le sélectionne pour l'inspection (signal `clique`).
class_name Habitant
extends Node3D

signal clique(habitant: Habitant)

const MODELES := [
	"female-a", "male-a", "female-b", "male-b", "female-c", "male-c",
	"female-d", "male-d", "female-e", "male-e", "female-f", "male-f",
]
const VITESSE := 4.0
const SENS_INCONNU := Color("ffffff")

var agent_id := ""
var role := ""
var palier := ""
var zone := ""
var sens := ""
var porteur := false
var decisions: Array[Dictionary] = []  # dernières décisions reçues (inspection, y compris en rejeu)
var messages: Array[Dictionary] = []  # derniers messages envoyés

var _modele: Node3D
var _lecteur: AnimationPlayer
var _pastille: MeshInstance3D
var _flacon: Node3D
var _bulle: Label3D
var _selection: MeshInstance3D
var _deplacement: Tween
var _bulle_tween: Tween


func configurer(id: String, materiau_de: Callable) -> void:
	agent_id = id
	name = id
	var nom: String = MODELES[abs(hash(id)) % MODELES.size()]
	_modele = load("res://assets/kenney_personnages/character-%s.glb" % nom).instantiate()
	_modele.scale = Vector3.ONE * 2.0
	add_child(_modele)
	_lecteur = _modele.find_child("AnimationPlayer", true, false)
	for anim in ["idle", "walk", "sit", "emote-yes", "emote-no"]:
		if _lecteur and _lecteur.has_animation(anim):
			_lecteur.get_animation(anim).loop_mode = Animation.LOOP_LINEAR if anim in ["idle", "walk", "sit"] else Animation.LOOP_NONE
	_jouer("idle")

	_pastille = _disque(0.6, 0.04, materiau_de.call(SENS_INCONNU, 0.4))
	_selection = _disque(0.85, 0.02, materiau_de.call(Color(1, 1, 1, 0.8), 1.5))
	_selection.visible = false

	_flacon = Node3D.new()
	_flacon.position.y = 2.35
	add_child(_flacon)
	var corps := MeshInstance3D.new()
	var capsule := CapsuleMesh.new()
	capsule.radius = 0.12
	capsule.height = 0.42
	corps.mesh = capsule
	corps.material_override = materiau_de.call(Color("ffb627"), 0.8)
	_flacon.add_child(corps)
	var bouchon := MeshInstance3D.new()
	var cyl := CylinderMesh.new()
	cyl.top_radius = 0.07
	cyl.bottom_radius = 0.07
	cyl.height = 0.12
	bouchon.mesh = cyl
	bouchon.position.y = 0.26
	bouchon.material_override = materiau_de.call(Color("2f86c6"), 0.4)
	_flacon.add_child(bouchon)
	_flacon.visible = false

	_bulle = Label3D.new()
	_bulle.position.y = 3.0
	_bulle.font_size = 64
	_bulle.outline_size = 22
	_bulle.pixel_size = 0.01
	_bulle.width = 700
	_bulle.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	_bulle.billboard = BaseMaterial3D.BILLBOARD_ENABLED
	_bulle.no_depth_test = true
	_bulle.modulate = Color("ffffff")
	_bulle.outline_modulate = Color("1d2333")
	_bulle.visible = false
	add_child(_bulle)

	# Zone cliquable pour l'inspection (D2).
	var aire := Area3D.new()
	aire.input_ray_pickable = true
	var forme := CollisionShape3D.new()
	var cap := CapsuleShape3D.new()
	cap.radius = 0.55
	cap.height = 2.0
	forme.shape = cap
	forme.position.y = 1.0
	aire.add_child(forme)
	aire.input_event.connect(_sur_entree)
	add_child(aire)


func _process(delta: float) -> void:
	if _flacon.visible:
		_flacon.rotate_y(delta * 1.8)


# --- mises à jour venues du flux ----------------------------------------------------------------


func definir_sens(nouveau: String, couleur: Color, materiau_de: Callable) -> void:
	sens = nouveau
	_pastille.material_override = materiau_de.call(couleur, 0.6)


func definir_porteur(valeur: bool) -> void:
	porteur = valeur
	_flacon.visible = valeur


func reagir(action: String) -> void:
	## Geste qui accompagne une décision : adopter, rejeter, ou rien de visible.
	if action in ["acheter", "appliquer", "recommander"]:
		_geste("emote-yes")
	elif action in ["abandonner", "critiquer"]:
		_geste("emote-no")


func parler(texte: String, couleur := Color.WHITE, duree := 5.0) -> void:
	_bulle.text = texte.left(90)
	_bulle.modulate = couleur
	_bulle.visible = true
	if _bulle_tween:
		_bulle_tween.kill()
	_bulle_tween = create_tween()
	_bulle_tween.tween_interval(duree)
	_bulle_tween.tween_callback(func(): _bulle.visible = false)


func aller_vers(cible: Vector3, instantane := false) -> void:
	if _deplacement:
		_deplacement.kill()
	if instantane:
		position = cible
		return
	var distance := position.distance_to(cible)
	if distance < 0.05:
		return
	look_at(Vector3(cible.x, position.y, cible.z), Vector3.UP, true)
	_jouer("walk")
	_deplacement = create_tween()
	_deplacement.tween_property(self, "position", cible, distance / VITESSE)
	_deplacement.tween_callback(func(): _jouer("idle"))


func selectionner(oui: bool) -> void:
	_selection.visible = oui


# --- interne ------------------------------------------------------------------------------------


func _disque(rayon: float, hauteur: float, mat: Material) -> MeshInstance3D:
	var mi := MeshInstance3D.new()
	var mesh := CylinderMesh.new()
	mesh.top_radius = rayon
	mesh.bottom_radius = rayon
	mesh.height = hauteur
	mi.mesh = mesh
	mi.material_override = mat
	mi.position.y = 0.03
	add_child(mi)
	return mi


func _jouer(anim: String) -> void:
	if _lecteur and _lecteur.has_animation(anim):
		_lecteur.play(anim)


func _geste(anim: String) -> void:
	if _deplacement and _deplacement.is_running():
		return  # ne pas interrompre une marche
	_jouer(anim)
	if _lecteur and _lecteur.has_animation(anim):
		var t := create_tween()
		t.tween_interval(_lecteur.get_animation(anim).length)
		t.tween_callback(func(): _jouer("idle"))


func _sur_entree(_camera: Node, evenement: InputEvent, _pos: Vector3, _normale: Vector3, _forme: int) -> void:
	if evenement is InputEventMouseButton and evenement.pressed and evenement.button_index == MOUSE_BUTTON_LEFT:
		clique.emit(self)
