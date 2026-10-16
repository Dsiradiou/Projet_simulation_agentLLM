import json
from datetime import datetime

import pytest

from bus import events as ev
from bus.export_schemas import render
from bus.topics import Topic
from sim.domaine import MINUTES_PAR_JOUR, debut_du_jour, format_sim, jour
from tests.exemples_evenements import exemples


def _dict(event: ev.Envelope) -> dict:
    return json.loads(ev.encode(event))


def test_every_type_has_an_example() -> None:
    assert set(exemples()) == set(ev.EVENT_CLASSES)


@pytest.mark.parametrize("cls", ev.EVENT_CLASSES, ids=lambda c: c.__name__)
def test_roundtrip(cls: type[ev.Envelope]) -> None:
    event = exemples()[cls]
    decoded = ev.decode(ev.encode(event))
    assert decoded == event
    assert type(decoded) is cls
    assert ev.topic_for(decoded) is cls.TOPIC


def test_every_topic_carries_at_least_one_type() -> None:
    assert {cls.TOPIC for cls in ev.EVENT_CLASSES} == set(Topic)


def test_type_names_are_unique() -> None:
    names = [cls.model_fields["type"].default for cls in ev.EVENT_CLASSES]
    assert len(names) == len(set(names))


def _decision() -> dict:
    return _dict(exemples()[ev.AgentDecision])


def _with(data: dict, path: str, value: object) -> dict:
    target = data
    *parents, last = path.split(".")
    for key in parents:
        target = target[key]
    if value is _DELETE:
        del target[last]
    else:
        target[last] = value
    return data


_DELETE = object()

INVALID_DECISIONS = {
    "version inconnue": ("schema_version", "2.0"),
    "type inconnu": ("type", "agent.reve"),
    "sens hors catégories": ("payload.sens", "genial"),
    "action inconnue": ("payload.action", "voler"),
    "zone inconnue": ("zone", "lycee"),
    "champ en trop": ("payload.humeur", "joyeuse"),
    "identifiant absent": ("event_id", _DELETE),
    "horodatage absent": ("wall_time_emitted", _DELETE),
    "horodatage sans fuseau": ("wall_time_emitted", "2026-10-16T12:00:00"),
    "traitement avant le fait": ("sim_time_processed", 500),
    "temps négatif": ("sim_time_occurred", -1),
    "agent à règles avec palier": ("payload.moteur", "regle"),
    "décision LLM sans coût": ("payload.appel_llm", None),
    "justification vide": ("payload.justification", ""),
    "émetteur mal formé": ("source_agent_id", "Pharmacien 01"),
}


@pytest.mark.parametrize("case", INVALID_DECISIONS, ids=list(INVALID_DECISIONS))
def test_invalid_decisions_are_rejected(case: str) -> None:
    path, value = INVALID_DECISIONS[case]
    with pytest.raises(ev.EventValidationError):
        ev.decode(json.dumps(_with(_decision(), path, value)))


def test_event_cannot_cause_itself() -> None:
    data = _decision()
    data["caused_by"] = [data["event_id"]]
    with pytest.raises(ev.EventValidationError, match="propre cause"):
        ev.decode(json.dumps(data))


@pytest.mark.parametrize("raw", [b"", b"pas du json", b"[]", b"{}", b"\xff\xfe"])
def test_garbage_is_rejected(raw: bytes) -> None:
    with pytest.raises(ev.EventValidationError):
        ev.decode(raw)


def test_message_rules() -> None:
    message = _dict(exemples()[ev.AgentMessage])
    for path, value in [
        ("payload.livraison", 601),  # avant l'émission (sim_time_processed = 602)
        ("payload.destinataires", []),  # message direct sans destinataire
    ]:
        with pytest.raises(ev.EventValidationError):
            ev.decode(json.dumps(_with(json.loads(json.dumps(message)), path, value)))
    zone_message = _with(json.loads(json.dumps(message)), "payload.canal", "zone")
    with pytest.raises(ev.EventValidationError, match="destinataires"):
        ev.decode(json.dumps(zone_message))


def test_move_and_stock_rules() -> None:
    move = _dict(exemples()[ev.AgentMove])
    with pytest.raises(ev.EventValidationError):
        ev.decode(json.dumps(_with(move, "payload.depuis", "plage")))
    stock = _dict(exemples()[ev.Stock])
    with pytest.raises(ev.EventValidationError, match="zone"):
        ev.decode(json.dumps(_with(stock, "zone", None)))


def test_error_message_points_to_the_field() -> None:
    with pytest.raises(ev.EventValidationError, match="payload.sens"):
        ev.decode(json.dumps(_with(_decision(), "payload.sens", "genial")))


def test_wall_time_is_utc_by_default() -> None:
    event = exemples()[ev.ClockStep]
    assert event.wall_time_emitted.utcoffset() is not None
    assert event.wall_time_emitted <= datetime.now(event.wall_time_emitted.tzinfo)


def test_sim_time_helpers() -> None:
    assert format_sim(0) == "J1 · 00:00"
    assert format_sim(MINUTES_PAR_JOUR + 14 * 60 + 30) == "J2 · 14:30"
    assert jour(MINUTES_PAR_JOUR - 1) == 1 and jour(MINUTES_PAR_JOUR) == 2
    assert debut_du_jour(3) == 2 * MINUTES_PAR_JOUR


def test_schema_documentation_is_up_to_date() -> None:
    for path, content in render().items():
        assert path.exists(), f"{path} absent : lancer python -m bus.export_schemas"
        assert path.read_text(encoding="utf-8") == content, (
            f"{path} périmé : lancer python -m bus.export_schemas"
        )
