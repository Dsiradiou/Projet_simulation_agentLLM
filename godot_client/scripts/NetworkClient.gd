extends Node
class_name NetworkClient

signal connected_to_server
signal connection_closed
signal event_received(event_data: Dictionary)

var ws: WebSocketPeer = WebSocketPeer.new()
var is_connected: bool = false
var server_url: String = "ws://127.0.0.1:8765"
var reconnect_timer: float = 0.0

func _ready() -> void:
	connect_to_gateway()

func connect_to_gateway() -> void:
	var err = ws.connect_to_url(server_url)
	if err != OK:
		print("[NetworkClient] Erreur d'initialisation WebSocket: ", err)
	else:
		print("[NetworkClient] Tentative de connexion vers: ", server_url)

func _process(delta: float) -> void:
	ws.poll()
	var state = ws.get_ready_state()
	
	if state == WebSocketPeer.STATE_OPEN:
		if not is_connected:
			is_connected = true
			connected_to_server.emit()
			print("[NetworkClient] Connecté au serveur WebSocket !")
		
		# Traitement de tous les paquets en attente
		while ws.get_available_packet_count() > 0:
			var packet = ws.get_packet()
			var text = packet.get_string_from_utf8()
			var json = JSON.new()
			var parse_err = json.parse(text)
			if parse_err == OK and typeof(json.data) == TYPE_DICTIONARY:
				event_received.emit(json.data)
			else:
				print("[NetworkClient] Paquet non-JSON ignoré.")

	elif state == WebSocketPeer.STATE_CLOSED:
		if is_connected:
			is_connected = false
			connection_closed.emit()
			print("[NetworkClient] Connexion fermée.")
		# Tentative de reconnexion automatique toutes les 3 secondes
		reconnect_timer += delta
		if reconnect_timer >= 3.0:
			reconnect_timer = 0.0
			connect_to_gateway()

func send_injection(event_type: String, location: String, payload: Dictionary) -> void:
	"""Envoie un événement de perturbation utilisateur (D3) vers Kafka."""
	if not is_connected:
		print("[NetworkClient] Impossible d'injecter : non connecté à la passerelle.")
		return
	var msg = {
		"type": "INJECT_EVENT",
		"event_type": event_type,
		"location": location,
		"payload": payload,
		"simulated_step": 0,
		"simulated_time_str": "Direct UI"
	}
	ws.send_text(JSON.stringify(msg))
	print("[NetworkClient] Perturbation envoyée : ", event_type)
