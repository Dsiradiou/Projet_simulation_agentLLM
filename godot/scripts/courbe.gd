# Petite courbe jour par jour des indicateurs d'émergence (étape 14, B2), dessinée à la main.
#
# Chaque série garde une valeur par jour, entre 0 et 1 ; une nouvelle valeur pour un jour déjà
# connu la remplace (rejeu, instantané).
class_name Courbe
extends Control

const JOURS_MIN := 7

var _series := {}  # nom -> {"couleur": Color, "valeurs": {jour: valeur}}


func definir(nom: String, couleur: Color) -> void:
	_series[nom] = {"couleur": couleur, "valeurs": {}}


func ajouter(nom: String, jour: int, valeur: float) -> void:
	if not _series.has(nom):
		return
	_series[nom]["valeurs"][jour] = clampf(valeur, 0.0, 1.0)
	queue_redraw()


func points(nom: String) -> int:
	return _series[nom]["valeurs"].size() if _series.has(nom) else 0


func vider() -> void:
	for nom in _series:
		_series[nom]["valeurs"].clear()
	queue_redraw()


func _draw() -> void:
	var taille := size
	draw_rect(Rect2(Vector2.ZERO, taille), Color(1, 1, 1, 0.05))
	for y in [0.0, 0.5, 1.0]:
		var hauteur: float = taille.y * (1.0 - y)
		draw_line(Vector2(0, hauteur), Vector2(taille.x, hauteur), Color(1, 1, 1, 0.12), 1.0)
	var dernier := JOURS_MIN
	for nom in _series:
		for j in _series[nom]["valeurs"]:
			dernier = maxi(dernier, j)
	for nom in _series:
		var valeurs: Dictionary = _series[nom]["valeurs"]
		var jours := valeurs.keys()
		jours.sort()
		var trace := PackedVector2Array()
		for j in jours:
			var x := taille.x * float(j - 1) / float(maxi(dernier - 1, 1))
			trace.append(Vector2(x, taille.y * (1.0 - valeurs[j])))
		if trace.size() >= 2:
			draw_polyline(trace, _series[nom]["couleur"], 2.0, true)
		elif trace.size() == 1:
			draw_circle(trace[0], 2.5, _series[nom]["couleur"])
