extends Area2D

var opinion_color: Color = Color(0.5, 0.5, 0.5)
var has_product: bool = false
var agent_id: String = ""

func _draw() -> void:
	# 1. Corps principal de l'agent (coloré selon son avis / sens)
	draw_circle(Vector2.ZERO, 14.0, opinion_color)
	draw_arc(Vector2.ZERO, 14.0, 0, TAU, 32, Color.WHITE, 1.5, true)
	
	# 2. Exigence B2 : Marqueur visible si l'agent possède le flacon Novalis
	if has_product:
		# Halo / anneau doré lumineux
		draw_arc(Vector2.ZERO, 19.0, 0, TAU, 32, Color(1.0, 0.84, 0.0), 3.0, true)
		# Petit point central "flacon"
		draw_circle(Vector2.ZERO, 4.0, Color(1.0, 0.84, 0.0))
