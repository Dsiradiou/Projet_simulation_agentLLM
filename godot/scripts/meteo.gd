# Effets visuels de la météo (étape 15 bis) : ciel, soleil, brume, nuages, pluie, orage, chaleur.
#
# La scène reçoit chaque jour `monde.meteo` (ensoleillé, nuageux, pluie, canicule). Un orage injecté
# se reconnaît à sa perturbation du jour (`monde.perturbation` « orage »), suivie d'un bulletin de
# pluie : pluie battante, ciel sombre et éclairs. Les effets restent légers (rendu Mobile) :
# particules CPU, banc de nuages à l'horizon (derrière la ville, pour ne pas masquer la vue
# plongeante), brouillard de distance et réglage des couleurs ; chaque changement de temps se
# fait en une seconde et demie. Aucune donnée de la simulation n'est calculée ici.
class_name EffetsMeteo
extends Node3D

const DUREE_TRANSITION := 1.5
const NUAGES_MAX := 22
const AMBIANCES := {
	"ensoleille": {"soleil": 1.25, "lumiere": Color("fff3dc"), "ciel": Color("4f8fd8"), "horizon": Color("bfe0f5"), "brume": 0.0, "teinte": Color("bfe0f5"), "nuages": 5, "gris": 0.0, "saturation": 1.0},
	"nuageux": {"soleil": 0.75, "lumiere": Color("e8eef5"), "ciel": Color("8aa3bd"), "horizon": Color("c9d3dd"), "brume": 0.002, "teinte": Color("c9d3dd"), "nuages": 14, "gris": 0.25, "saturation": 0.9},
	"pluie": {"soleil": 0.5, "lumiere": Color("d0d8e2"), "ciel": Color("6c7a8a"), "horizon": Color("98a4b0"), "brume": 0.006, "teinte": Color("98a4b0"), "nuages": 18, "gris": 0.55, "saturation": 0.75},
	"orage": {"soleil": 0.3, "lumiere": Color("b8c2d6"), "ciel": Color("3c4656"), "horizon": Color("68727f"), "brume": 0.009, "teinte": Color("68727f"), "nuages": NUAGES_MAX, "gris": 0.75, "saturation": 0.6},
	"canicule": {"soleil": 1.45, "lumiere": Color("ffe1b0"), "ciel": Color("3f86d6"), "horizon": Color("f3d9b0"), "brume": 0.003, "teinte": Color("f0c890"), "nuages": 0, "gris": 0.0, "saturation": 1.25},
}

var etat := ""  # ambiance affichée : une clé de AMBIANCES
var _env: Environment
var _soleil: DirectionalLight3D
var _ciel: ProceduralSkyMaterial
var _zone := Rect2(-60, -60, 120, 80)  # étendue du monde construit (x, z)
var _cible := Vector3(-2, 0, -2)
var _nuages: Array[Node3D] = []
var _mat_nuage: StandardMaterial3D
var _pluie: CPUParticles3D
var _chaleur: CPUParticles3D
var _transition: Tween
var _hasard := RandomNumberGenerator.new()
var _orage := false
var _avant_eclair := 2.0
var _soleil_base := 1.25


func configurer(env: Environment, soleil: DirectionalLight3D, ciel: ProceduralSkyMaterial, bornes: Rect2) -> void:
	_env = env
	_soleil = soleil
	_ciel = ciel
	_zone = bornes.grow(30.0)
	_hasard.seed = 11
	_env.fog_enabled = true
	_env.fog_sky_affect = 0.0
	_env.fog_density = 0.0
	_env.adjustment_enabled = true
	_construire_nuages()
	_pluie = _particules_pluie()
	_chaleur = _particules_chaleur()


func appliquer(conditions: String, orage := false, immediat := false) -> void:
	## Ambiance du jour ; `orage` : la pluie du jour vient d'un orage injecté.
	var cle := "orage" if orage and conditions == "pluie" else conditions
	if not AMBIANCES.has(cle):
		cle = "ensoleille"
	etat = cle
	var a: Dictionary = AMBIANCES[cle]
	_soleil_base = a["soleil"]
	_orage = cle == "orage"
	_avant_eclair = 0.8
	var gris: Color = Color.WHITE.lerp(Color("5d6672"), a["gris"])
	if _transition:
		_transition.kill()
	if immediat or not is_inside_tree():
		_soleil.light_energy = a["soleil"]
		_soleil.light_color = a["lumiere"]
		_ciel.sky_top_color = a["ciel"]
		_ciel.sky_horizon_color = a["horizon"]
		_ciel.ground_horizon_color = a["horizon"]
		_env.fog_density = a["brume"]
		_env.fog_light_color = a["teinte"]
		_env.adjustment_saturation = a["saturation"]
		_mat_nuage.albedo_color = gris
	else:
		_transition = create_tween().set_parallel(true).set_trans(Tween.TRANS_SINE)
		_transition.tween_property(_soleil, "light_energy", a["soleil"], DUREE_TRANSITION)
		_transition.tween_property(_soleil, "light_color", a["lumiere"], DUREE_TRANSITION)
		_transition.tween_property(_ciel, "sky_top_color", a["ciel"], DUREE_TRANSITION)
		_transition.tween_property(_ciel, "sky_horizon_color", a["horizon"], DUREE_TRANSITION)
		_transition.tween_property(_ciel, "ground_horizon_color", a["horizon"], DUREE_TRANSITION)
		_transition.tween_property(_env, "fog_density", a["brume"], DUREE_TRANSITION)
		_transition.tween_property(_env, "fog_light_color", a["teinte"], DUREE_TRANSITION)
		_transition.tween_property(_env, "adjustment_saturation", a["saturation"], DUREE_TRANSITION)
		_transition.tween_property(_mat_nuage, "albedo_color", gris, DUREE_TRANSITION)
	for i in _nuages.size():
		_nuages[i].visible = i < int(a["nuages"])
	_pluie.emitting = cle in ["pluie", "orage"]
	_pluie.direction = Vector3(0.35 if _orage else 0.1, -1, 0)
	_chaleur.emitting = cle == "canicule"


func centrer(cible: Vector3) -> void:
	## La pluie, la chaleur et les éclairs suivent ce que regarde la caméra.
	_cible = cible
	_pluie.position = Vector3(cible.x, 28, cible.z)
	_chaleur.position = Vector3(cible.x, 0.3, cible.z)


func nuages_visibles() -> int:
	return _nuages.filter(func(n): return n.visible).size()


func _process(delta: float) -> void:
	var vent := 5.0 if _orage else 1.6
	for n in _nuages:
		if n.visible:
			n.position.x += vent * delta
			if n.position.x > _zone.end.x + 40:
				n.position.x = _zone.position.x - 40
	if _orage:
		_avant_eclair -= delta
		if _avant_eclair <= 0.0:
			_avant_eclair = _hasard.randf_range(1.5, 5.0)
			_eclair()


func _eclair() -> Node3D:
	## Un éclair : un trait lumineux en zigzag vers le sol et un flash de lumière.
	var zigzag := Node3D.new()
	add_child(zigzag)
	var mat := StandardMaterial3D.new()
	mat.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
	mat.albedo_color = Color("eef4ff")
	mat.emission_enabled = true
	mat.emission = Color("cfe0ff")
	mat.emission_energy_multiplier = 3.0
	var depart := _cible + Vector3(_hasard.randf_range(-35, 35), 30, _hasard.randf_range(-25, 5))
	var point := depart
	for i in 6:
		var suivant := point + Vector3(_hasard.randf_range(-4.0, 4.0), -5, _hasard.randf_range(-2.5, 2.5))
		var segment := MeshInstance3D.new()
		var mesh := BoxMesh.new()
		mesh.size = Vector3(0.3, point.distance_to(suivant), 0.3)
		segment.mesh = mesh
		segment.material_override = mat
		segment.position = (point + suivant) / 2.0
		segment.quaternion = Quaternion(Vector3.UP, (suivant - point).normalized())
		zigzag.add_child(segment)
		point = suivant
	_soleil.light_energy = 3.2
	var flash := create_tween()
	flash.tween_property(_soleil, "light_energy", _soleil_base, 0.35)
	flash.parallel().tween_callback(zigzag.queue_free).set_delay(0.18)
	return zigzag


# --- construction -------------------------------------------------------------------------------


func _construire_nuages() -> void:
	_mat_nuage = StandardMaterial3D.new()
	_mat_nuage.albedo_color = Color.WHITE
	_mat_nuage.roughness = 1.0
	for i in NUAGES_MAX:
		var nuage := Node3D.new()
		# En banc derrière la ville : vus de haut, des nuages au-dessus d'elle la masqueraient.
		nuage.position = Vector3(_hasard.randf_range(_zone.position.x - 40, _zone.end.x + 40), _hasard.randf_range(10, 20), _hasard.randf_range(_zone.position.y - 40, _zone.position.y + 8))
		for j in _hasard.randi_range(3, 5):
			var boule := MeshInstance3D.new()
			var mesh := SphereMesh.new()
			mesh.radial_segments = 8
			mesh.rings = 4
			var r := _hasard.randf_range(3.0, 5.5)
			mesh.radius = r
			mesh.height = r * 2.0
			boule.mesh = mesh
			boule.material_override = _mat_nuage
			boule.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
			boule.position = Vector3(j * 3.6 - 6.0, _hasard.randf_range(-0.5, 0.8), _hasard.randf_range(-2.0, 2.0))
			boule.scale = Vector3(1, 0.5, 1)
			nuage.add_child(boule)
		nuage.visible = false
		add_child(nuage)
		_nuages.append(nuage)


func _particules_pluie() -> CPUParticles3D:
	var p := CPUParticles3D.new()
	p.amount = 2200
	p.lifetime = 0.8
	p.emitting = false
	p.emission_shape = CPUParticles3D.EMISSION_SHAPE_BOX
	p.emission_box_extents = Vector3(65, 0.5, 45)
	p.direction = Vector3(0.1, -1, 0)
	p.spread = 3.0
	p.gravity = Vector3(0, -25, 0)
	p.initial_velocity_min = 30.0
	p.initial_velocity_max = 38.0
	var goutte := BoxMesh.new()
	goutte.size = Vector3(0.04, 0.9, 0.04)
	var mat := StandardMaterial3D.new()
	mat.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
	mat.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA
	mat.albedo_color = Color(0.82, 0.9, 1.0, 0.55)
	goutte.material = mat
	p.mesh = goutte
	p.position = Vector3(-2, 28, -2)
	add_child(p)
	return p


func _particules_chaleur() -> CPUParticles3D:
	## Air qui ondule au-dessus du sol : de rares lueurs orangées qui montent et s'effacent.
	var p := CPUParticles3D.new()
	p.amount = 110
	p.lifetime = 4.0
	p.emitting = false
	p.emission_shape = CPUParticles3D.EMISSION_SHAPE_BOX
	p.emission_box_extents = Vector3(60, 0.2, 40)
	p.direction = Vector3(0, 1, 0)
	p.spread = 15.0
	p.gravity = Vector3.ZERO
	p.initial_velocity_min = 0.5
	p.initial_velocity_max = 1.1
	var rampe := Gradient.new()
	rampe.set_color(0, Color(1, 0.62, 0.3, 0.0))
	rampe.set_color(1, Color(1, 0.62, 0.3, 0.0))
	rampe.add_point(0.4, Color(1, 0.7, 0.35, 0.22))
	p.color_ramp = rampe
	var lueur := QuadMesh.new()
	lueur.size = Vector2(0.2, 0.2)
	var mat := StandardMaterial3D.new()
	mat.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
	mat.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA
	mat.blend_mode = BaseMaterial3D.BLEND_MODE_ADD
	mat.billboard_mode = BaseMaterial3D.BILLBOARD_PARTICLES
	mat.vertex_color_use_as_albedo = true
	lueur.material = mat
	p.mesh = lueur
	p.position = Vector3(-2, 0.3, -2)
	add_child(p)
	return p
