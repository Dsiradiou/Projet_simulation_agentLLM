# Mesure la boîte englobante des modèles importés (aide au placement des maquettes).
# Usage : godot --headless --path godot --script res://outils/mesurer.gd -- <dossier> [filtre]
extends SceneTree


func _aabb(node: Node, xform: Transform3D) -> AABB:
	var box := AABB()
	var first := true
	var stack := [[node, xform]]
	while stack.size() > 0:
		var item = stack.pop_back()
		var n: Node = item[0]
		var t: Transform3D = item[1]
		if n is Node3D:
			t = t * (n as Node3D).transform
		if n is MeshInstance3D and (n as MeshInstance3D).mesh:
			var b: AABB = t * (n as MeshInstance3D).mesh.get_aabb()
			box = b if first else box.merge(b)
			first = false
		for c in n.get_children():
			stack.append([c, t])
	return box


func _init() -> void:
	var args := OS.get_cmdline_user_args()
	var dossier: String = args[0]
	var filtre: String = args[1] if args.size() > 1 else ""
	for f in DirAccess.get_files_at(dossier):
		if not (f.ends_with(".glb") or f.ends_with(".gltf")) or (filtre != "" and not f.contains(filtre)):
			continue
		var scene: PackedScene = load(dossier.path_join(f))
		var inst := scene.instantiate()
		var b := _aabb(inst, Transform3D.IDENTITY)
		print("%-34s taille %5.2f x %5.2f x %5.2f  origine-min (%5.2f, %5.2f, %5.2f)" % [f, b.size.x, b.size.y, b.size.z, b.position.x, b.position.y, b.position.z])
		inst.free()
	quit()
