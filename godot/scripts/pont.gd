# Client du pont FastAPI (docs/pont.md) : flux WebSocket et requêtes HTTP.
#
# Le flux est le même en direct et en rejeu : instantané ou annonce du rejeu, puis les événements
# du contrat, un message JSON chacun. En direct, la connexion est rétablie si elle se coupe.
class_name Pont
extends Node

signal message_recu(donnees: Dictionary)
signal etat_connexion(connecte: bool, detail: String)
signal reponse(requete: String, code: int, donnees: Variant)

var url_http := "http://127.0.0.1:8000"
var rejeu := ""
var vitesse := 0.0

var _ws := WebSocketPeer.new()
var _etat_precedent := WebSocketPeer.STATE_CLOSED
var _reconnexion := 0.0
var _fin_rejeu := false


func connecter() -> void:
	_fin_rejeu = false
	var url := url_http.replace("http", "ws") + "/ws"
	if rejeu != "":
		url += "?rejeu=%s" % rejeu.uri_encode()
		if vitesse > 0.0:
			url += "&vitesse=%s" % vitesse
	_ws = WebSocketPeer.new()
	_ws.inbound_buffer_size = 4 * 1024 * 1024
	var erreur := _ws.connect_to_url(url)
	if erreur != OK:
		etat_connexion.emit(false, "connexion impossible à %s" % url)


func _process(delta: float) -> void:
	_ws.poll()
	var etat := _ws.get_ready_state()
	if etat != WebSocketPeer.STATE_CONNECTING:  # une fermeture peut suivre de près les derniers messages
		while _ws.get_available_packet_count() > 0:
			var texte := _ws.get_packet().get_string_from_utf8()
			var donnees = JSON.parse_string(texte)
			if donnees is Dictionary:
				if donnees.get("type", "") == "pont.fin_rejeu":
					_fin_rejeu = true
				message_recu.emit(donnees)
	if etat != _etat_precedent:
		_etat_precedent = etat
		if etat == WebSocketPeer.STATE_OPEN:
			etat_connexion.emit(true, "rejeu" if rejeu != "" else "connecté")
		elif etat == WebSocketPeer.STATE_CLOSED:
			etat_connexion.emit(false, "rejeu terminé" if _fin_rejeu else "pont injoignable, nouvel essai…")
			_reconnexion = 2.0
	if etat == WebSocketPeer.STATE_CLOSED and rejeu == "" and _reconnexion > 0.0:
		_reconnexion -= delta
		if _reconnexion <= 0.0:
			connecter()


func passer_en_direct() -> void:
	rejeu = ""
	_ws.close()
	connecter()


func rejouer(run_id: String, vitesse_jours := 1.0) -> void:
	rejeu = run_id
	vitesse = vitesse_jours
	_ws.close()
	connecter()


# --- HTTP ---------------------------------------------------------------------------------------


func obtenir(chemin: String) -> void:
	_requete(chemin, HTTPClient.METHOD_GET, "")


func envoyer(chemin: String, corps: Dictionary) -> void:
	_requete(chemin, HTTPClient.METHOD_POST, JSON.stringify(corps))


func _requete(chemin: String, methode: int, corps: String) -> void:
	var http := HTTPRequest.new()
	add_child(http)
	http.request_completed.connect(
		func(_resultat: int, code: int, _entetes: PackedStringArray, contenu: PackedByteArray) -> void:
			var donnees = JSON.parse_string(contenu.get_string_from_utf8()) if contenu.size() > 0 else null
			reponse.emit(chemin, code, donnees)
			http.queue_free()
	)
	var erreur := http.request(url_http + chemin, ["Content-Type: application/json"], methode, corps)
	if erreur != OK:
		reponse.emit(chemin, 0, null)
		http.queue_free()
