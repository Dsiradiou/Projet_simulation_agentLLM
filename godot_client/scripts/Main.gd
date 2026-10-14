extends Node2D

# Références UI
@onready var network_client: NetworkClient = $NetworkClient
@onready var sim_clock_label: Label = $HUD/ClockSim
@onready var real_clock_label: Label = $HUD/ClockReal
@onready var status_label: Label = $HUD/StatusLabel

# Panneau d'inspection (D2)
@onready var inspector_panel: Panel = $InspectorPanel
@onready var ins_agent_id: Label = $InspectorPanel/VBox/AgentId
@onready var ins_engine: Label = $InspectorPanel/VBox/Engine
@onready var ins_location: Label = $InspectorPanel/VBox/Location
@onready var ins_opinion: Label = $InspectorPanel/VBox/Opinion
@onready var ins_product: Label = $InspectorPanel/VBox/Product
@onready var ins_reasoning: TextEdit = $InspectorPanel/VBox/Reasoning
@onready var ins_mem_log: ItemList = $InspectorPanel/VBox/MemLog

# Dictionnaire des coordonnées des 7 zones
const ZONE_POSITIONS = {
	"pharmacie": Vector2(180, 150),
	"terrain_sport": Vector2(450, 150),
	"reseaux_sociaux": Vector2(720, 150),
	"domicile": Vector2(450, 340),
	"plage": Vector2(180, 530),
	"piscine": Vector2(450, 530),
	"centre_commercial": Vector2(720, 530)
}

const OPINION_COLORS = {
	"efficace": Color(0.18, 0.50, 0.92),  # Bleu
	"tendance": Color(0.95, 0.65, 0.10),  # Ambre / Or
	"banal": Color(0.55, 0.58, 0.65),     # Gris neutre
	"depasse": Color(0.85, 0.20, 0.20),   # Rouge / Dépassé
	"neutre": Color(0.3, 0.3, 0.3)
}

# Agents suivis {agent_id: {node, data}}
var agents: Dictionary = {}
var selected_agent_id: String = ""
var real_start_time: float = 0.0

func _ready() -> void:
	real_start_time = Time.get_ticks_msec() / 1000.0
	network_client.connected_to_server.connect(_on_connected)
	network_client.connection_closed.connect(_on_disconnected)
	network_client.event_received.connect(_on_simulation_event)
	_setup_injection_buttons()

func _process(_delta: float) -> void:
	var elapsed = (Time.get_ticks_msec() / 1000.0) - real_start_time
	var mins = int(elapsed / 60)
	var secs = int(elapsed) % 60
	real_clock_label.text = "Réel: %02d:%02d" % [mins, secs]

func _on_connected() -> void:
	status_label.text = "● Connecté (Kafka Stream)"
	status_label.modulate = Color.GREEN

func _on_disconnected() -> void:
	status_label.text = "○ Déconnecté (Recherche passerelle...)"
	status_label.modulate = Color.RED

func _on_simulation_event(data: Dictionary) -> void:
	var msg_type = data.get("type", "")
	if msg_type != "SIMULATION_EVENT":
		return
		
	var event_type = data.get("event_type", "")
	var source_id = data.get("source_id", "")
	var location = data.get("location", "domicile")
	var time_str = data.get("simulated_time_str", "")
	var payload = data.get("payload", {})
	
	if time_str != "":
		sim_clock_label.text = "Simulé: " + time_str

	# Gestion des agents
	if source_id.begins_with("agent_"):
		_update_or_create_agent(source_id, location, payload)

func _update_or_create_agent(agent_id: String, location: String, payload: Dictionary) -> void:
	if not agents.has(agent_id):
		# Création visuelle d'un nouvel agent
		var agent_node = _create_agent_visual(agent_id)
		$WorldArea/AgentsContainer.add_child(agent_node)
		agents[agent_id] = {
			"node": agent_node,
			"location": location,
			"opinion": payload.get("current_opinion", "neutre"),
			"has_product": payload.get("has_product", false),
			"engine": payload.get("engine_used", "regles"),
			"reasoning": payload.get("reasoning", "Aucun pour l'instant."),
			"history": []
		}
	
	var ag = agents[agent_id]
	ag["location"] = location
	if payload.has("current_opinion"):
		ag["opinion"] = payload["current_opinion"]
	if payload.has("has_product"):
		ag["has_product"] = payload["has_product"]
	if payload.has("reasoning"):
		ag["reasoning"] = payload["reasoning"]
	if payload.has("engine_used"):
		ag["engine"] = payload["engine_used"]
		
	# Ajout à l'historique de mémoire
	if payload.has("action_taken"):
		var mem_entry = "[%s] %s (%s)" % [location, payload["action_taken"], ag["opinion"]]
		ag["history"].push_front(mem_entry)
		if ag["history"].size() > 8:
			ag["history"].pop_back()

	# Mise à jour graphique
	_update_agent_graphics(ag)
	
	# Si cet agent est inspecté, actualiser le panneau (D2)
	if selected_agent_id == agent_id:
		_display_inspected_agent(agent_id)

func _create_agent_visual(agent_id: String) -> Area2D:
	var area = Area2D.new()
	area.name = agent_id
	area.set_script(load("res://scripts/AgentView.gd"))
	area.set("agent_id", agent_id)
	
	# Cercle de collision pour clic/sélection (D2)
	var col = CollisionShape2D.new()
	var shape = CircleShape2D.new()
	shape.radius = 20.0
	col.shape = shape
	area.add_child(col)
	
	# Événement de clic pour inspection (D2)
	area.input_event.connect(func(_vp, event, _shape):
		if event is InputEventMouseButton and event.pressed and event.button_index == MOUSE_BUTTON_LEFT:
			_select_agent(agent_id)
	)
	return area

func _update_agent_graphics(ag: Dictionary) -> void:
	var node: Area2D = ag["node"]
	var target_base_pos = ZONE_POSITIONS.get(ag["location"], Vector2(450, 340))
	# Léger décalage aléatoire fixe par hash pour ne pas superposer les agents dans la zone
	var offset = Vector2(sin(ag["node"].name.hash()) * 50.0, cos(ag["node"].name.hash()) * 40.0)
	node.position = target_base_pos + offset
	node.set("opinion_color", OPINION_COLORS.get(ag["opinion"], Color.GRAY))
	node.set("has_product", ag["has_product"])
	node.queue_redraw()

func _select_agent(agent_id: String) -> void:
	selected_agent_id = agent_id
	_display_inspected_agent(agent_id)

func _display_inspected_agent(agent_id: String) -> void:
	if not agents.has(agent_id):
		return
	var ag = agents[agent_id]
	ins_agent_id.text = "Agent : " + agent_id
	ins_engine.text = "Moteur : " + ag["engine"].to_upper()
	ins_location.text = "Zone : " + ag["location"]
	ins_opinion.text = "Avis : " + ag["opinion"].to_upper()
	ins_opinion.modulate = OPINION_COLORS.get(ag["opinion"], Color.WHITE)
	ins_product.text = "Flacon Novalis : " + ("OUI (porteur)" if ag["has_product"] else "NON")
	ins_reasoning.text = ag["reasoning"]
	
	ins_mem_log.clear()
	for mem in ag["history"]:
		ins_mem_log.add_item(mem)

func _setup_injection_buttons() -> void:
	$HUD/BtnRupture.pressed.connect(func():
		network_client.send_injection("MARKET_SHORTAGE", "pharmacie", {"message": "Rupture de stock sur Novalis SPF 42"})
	)
	$HUD/BtnReseaux.pressed.connect(func():
		network_client.send_injection("ANNOUNCEMENT_INJECTED", "reseaux_sociaux", {"message": "Vidéo virale : Novalis est le produit de l'été !"})
	)
	$HUD/BtnCanicule.pressed.connect(func():
		network_client.send_injection("WEATHER_CHANGE", "plage", {"message": "Pic d'UV 10+, affluence maximale à la plage"})
	)
