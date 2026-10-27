# Interface 2D au-dessus de la scène : état du run, indicateurs, fil des événements, fiche d'un
# agent (D2), injection d'une perturbation et commandes (D3), choix entre direct et rejeu, accès
# à l'interface de paramétrage (`parametrage.gd`, étape 15 bis).
# Depuis l'étape 14, le panneau des indicateurs affiche les valeurs calculées par le service des
# indicateurs (B2) et leur courbe jour par jour ; sans elles (runs plus anciens), il compte les
# sens des habitants visibles.
#
# Elle ne parle pas au pont elle-même : elle émet des signaux, que `monde.gd` traduit en requêtes.
class_name Interface
extends CanvasLayer

signal commande(nom: String)
signal injection(nature: String, zone: String, description: String)
signal rejeu_demande(run_id: String, vitesse: float)
signal direct_demande
signal fiche_fermee
signal parametrage_demande

const NATURES := {
	"polemique": "Polémique",
	"publication_virale": "Publication virale",
	"article_presse": "Article de presse",
	"promotion_concurrente": "Promotion concurrente",
	"rupture_stock": "Rupture de stock",
	"vague_chaleur": "Vague de chaleur",
	"orage": "Orage",
}
const FIL_MAX := 9

var _titre: Label
var _connexion: Label
var _ips: Label
var _ips_attente := 0.0
var _barres := {}
var _resume: Label
var _mesures: Label
var courbe: Courbe
var _fil: RichTextLabel
var _lignes_fil: Array[String] = []
var _fiche: PanelContainer
var _fiche_texte: RichTextLabel
var _nature: OptionButton
var _zone: OptionButton
var _description: LineEdit
var _retour: Label
var _runs: OptionButton
var _vitesse: SpinBox
var _boutons_direct: Array[Button] = []


func construire(couleurs_sens: Dictionary) -> void:
	_barre_haute()
	_indicateurs(couleurs_sens)
	_panneau_fil()
	_panneau_fiche()
	_panneau_controle()


func _process(delta: float) -> void:
	_ips_attente -= delta
	if _ips_attente <= 0.0:
		_ips_attente = 1.0
		_ips.text = "%d ips  " % Engine.get_frames_per_second()


# --- mises à jour -------------------------------------------------------------------------------


func afficher_etat(jour: int, meteo: String, mode: String) -> void:
	_titre.text = "  Jour %d   ·   %s   ·   %s  " % [jour, meteo, mode]


func afficher_connexion(texte: String, ok: bool) -> void:
	_connexion.text = "  ● %s  " % texte
	_connexion.modulate = Color("7ee08a") if ok else Color("ff8a80")


func afficher_indicateurs(comptes: Dictionary, porteurs: int, total: int, messages_jour: int, incidents: int, mesures := {}) -> void:
	## `mesures` : dernières valeurs globales du service (sens_partage, information_mutuelle...).
	## Si elles existent, la répartition et l'adoption affichées sont les siennes.
	if mesures.has("repartition"):
		comptes = mesures["repartition"]
	var exprimes := 0
	for s in comptes:
		exprimes += int(comptes[s])
	for s in _barres:
		var part: float = float(comptes.get(s, 0)) / maxi(exprimes, 1)
		_barres[s][0].custom_minimum_size.x = 4 + 170 * part
		_barres[s][1].text = " %s  %d %%" % [s, round(part * 100)]
	var adoption: float = 100.0 * porteurs / maxi(total, 1)
	if mesures.has("adoption"):
		adoption = 100.0 * float(mesures["adoption"])
	_resume.text = " Porteurs %d / %d (%d %%)\n Messages du jour %d   ·   Incidents %d" % [porteurs, total, round(adoption), messages_jour, incidents]
	if mesures.has("sens_partage"):
		var p_valeur := float(mesures.get("information_mutuelle_p", 1.0))
		_mesures.text = " Convergence %.2f   ·   Écart entre rôles %.2f%s\n Dérives des agents LLM %s" % [
			float(mesures["sens_partage"]),
			float(mesures.get("information_mutuelle", 0.0)),
			" (p %.3f)" % p_valeur if mesures.has("information_mutuelle_p") else "",
			"%d %%" % round(100.0 * float(mesures["derives"])) if mesures.has("derives") else "—",
		]
	else:
		_mesures.text = " (indicateurs du service non reçus)"


func ajouter_au_fil(texte: String) -> void:
	_lignes_fil.push_front(texte)
	if _lignes_fil.size() > FIL_MAX:
		_lignes_fil.resize(FIL_MAX)
	_fil.text = "\n".join(_lignes_fil)


func afficher_fiche(texte_bbcode: String) -> void:
	_fiche_texte.text = texte_bbcode
	_fiche.visible = true


func cacher_fiche() -> void:
	_fiche.visible = false


func afficher_retour(texte: String) -> void:
	_retour.text = texte


func definir_runs(runs: Array) -> void:
	_runs.clear()
	for r in runs:
		_runs.add_item("%s (%d évts)" % [r["run_id"], int(r["evenements"])])
		_runs.set_item_metadata(_runs.item_count - 1, r["run_id"])


func definir_zones(zones: Array) -> void:
	## Zones proposées pour une injection : [identifiant, nom] ; un type présent plusieurs fois
	## est proposé en entier (tous ses exemplaires) puis exemplaire par exemplaire.
	_zone.clear()
	_zone.add_item("Toutes les zones")
	_zone.set_item_metadata(0, "")
	for z in zones:
		_zone.add_item(z[1])
		_zone.set_item_metadata(_zone.item_count - 1, z[0])


func zones_proposees() -> Array:
	var ids: Array = []
	for i in _zone.item_count:
		ids.append(_zone.get_item_metadata(i))
	return ids


func mode_rejeu(actif: bool) -> void:
	for b in _boutons_direct:
		b.disabled = actif
	_description.editable = not actif


# --- construction -------------------------------------------------------------------------------


func _panneau(position: Vector2, taille := Vector2.ZERO) -> PanelContainer:
	var p := PanelContainer.new()
	p.position = position
	if taille != Vector2.ZERO:
		p.custom_minimum_size = taille
	var style := StyleBoxFlat.new()
	style.bg_color = Color(0.09, 0.11, 0.17, 0.82)
	style.set_corner_radius_all(8)
	style.set_content_margin_all(10)
	p.add_theme_stylebox_override("panel", style)
	add_child(p)
	return p


func _label(texte: String, taille := 16) -> Label:
	var l := Label.new()
	l.text = texte
	l.add_theme_font_size_override("font_size", taille)
	return l


func _bouton(texte: String, action: Callable) -> Button:
	var b := Button.new()
	b.text = texte
	b.pressed.connect(action)
	return b


func _barre_haute() -> void:
	var p := _panneau(Vector2(16, 14))
	var ligne := HBoxContainer.new()
	ligne.add_theme_constant_override("separation", 8)
	p.add_child(ligne)
	_titre = _label("  En attente du pont…  ", 20)
	ligne.add_child(_titre)
	ligne.add_child(_bouton("Nouvelle simulation", func(): parametrage_demande.emit()))
	for spec in [["Pause", "pause"], ["Reprise", "reprise"], ["Arrêt", "arret"]]:
		var nom: String = spec[1]
		var b := _bouton(spec[0], func(): commande.emit(nom))
		ligne.add_child(b)
		_boutons_direct.append(b)
	_connexion = _label("  ● connexion…  ", 15)
	ligne.add_child(_connexion)
	_ips = _label("", 13)
	ligne.add_child(_ips)


func _indicateurs(couleurs: Dictionary) -> void:
	var p := _panneau(Vector2(1290, 14), Vector2(294, 0))
	var col := VBoxContainer.new()
	col.add_theme_constant_override("separation", 5)
	p.add_child(col)
	col.add_child(_label("Sens partagé", 18))
	for s in couleurs:
		var ligne := HBoxContainer.new()
		col.add_child(ligne)
		var barre := ColorRect.new()
		barre.color = couleurs[s]
		barre.custom_minimum_size = Vector2(4, 16)
		ligne.add_child(barre)
		var l := _label(" %s" % s, 15)
		ligne.add_child(l)
		_barres[s] = [barre, l]
	_resume = _label("", 14)
	col.add_child(_resume)
	_mesures = _label("", 14)
	col.add_child(_mesures)
	courbe = Courbe.new()
	courbe.custom_minimum_size = Vector2(274, 56)
	courbe.definir("sens_partage", Color("ffd166"))
	courbe.definir("information_mutuelle", Color("7fd1ff"))
	col.add_child(courbe)
	var legende := _label(" convergence (jaune) · écart entre rôles (bleu)", 12)
	legende.modulate = Color(1, 1, 1, 0.7)
	col.add_child(legende)


func _panneau_fil() -> void:
	var p := _panneau(Vector2(1090, 640), Vector2(494, 246))
	var col := VBoxContainer.new()
	p.add_child(col)
	col.add_child(_label("Ce qui se passe", 16))
	_fil = RichTextLabel.new()
	_fil.bbcode_enabled = true
	_fil.fit_content = false
	_fil.custom_minimum_size = Vector2(474, 200)
	_fil.add_theme_font_size_override("normal_font_size", 13)
	col.add_child(_fil)


func _panneau_fiche() -> void:
	_fiche = _panneau(Vector2(1180, 330), Vector2(404, 300))
	var col := VBoxContainer.new()
	_fiche.add_child(col)
	var tete := HBoxContainer.new()
	col.add_child(tete)
	var titre := _label("Inspection d'un agent", 16)
	titre.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	tete.add_child(titre)
	tete.add_child(_bouton("✕", func(): cacher_fiche(); fiche_fermee.emit()))
	_fiche_texte = RichTextLabel.new()
	_fiche_texte.bbcode_enabled = true
	_fiche_texte.custom_minimum_size = Vector2(384, 250)
	_fiche_texte.add_theme_font_size_override("normal_font_size", 13)
	_fiche_texte.add_theme_font_size_override("bold_font_size", 13)
	col.add_child(_fiche_texte)
	_fiche.visible = false


func _panneau_controle() -> void:
	var p := _panneau(Vector2(16, 700), Vector2(560, 0))
	var col := VBoxContainer.new()
	col.add_theme_constant_override("separation", 6)
	p.add_child(col)
	col.add_child(_label("Injecter une perturbation (effet au début du jour suivant)", 15))
	var ligne := HBoxContainer.new()
	col.add_child(ligne)
	_nature = OptionButton.new()
	for n in NATURES:
		_nature.add_item(NATURES[n])
		_nature.set_item_metadata(_nature.item_count - 1, n)
	ligne.add_child(_nature)
	_zone = OptionButton.new()
	ligne.add_child(_zone)
	_description = LineEdit.new()
	_description.placeholder_text = "Ce qui se passe, en une phrase"
	_description.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	col.add_child(_description)
	var envoyer := _bouton("Injecter", _injecter)
	ligne.add_child(envoyer)
	_boutons_direct.append(envoyer)
	_retour = _label("", 13)
	col.add_child(_retour)

	var rejeu := HBoxContainer.new()
	col.add_child(rejeu)
	_runs = OptionButton.new()
	_runs.custom_minimum_size.x = 300
	rejeu.add_child(_runs)
	_vitesse = SpinBox.new()
	_vitesse.min_value = 0.2
	_vitesse.max_value = 10
	_vitesse.step = 0.2
	_vitesse.value = 1.0
	_vitesse.suffix = "j/s"
	rejeu.add_child(_vitesse)
	rejeu.add_child(_bouton("Rejouer", func():
		if _runs.item_count > 0:
			rejeu_demande.emit(_runs.get_item_metadata(_runs.selected), _vitesse.value)))
	rejeu.add_child(_bouton("Direct", func(): direct_demande.emit()))


func _injecter() -> void:
	var texte := _description.text.strip_edges()
	if texte == "":
		afficher_retour("Décrire la perturbation avant de l'injecter.")
		return
	injection.emit(_nature.get_item_metadata(_nature.selected), _zone.get_item_metadata(_zone.selected), texte)
