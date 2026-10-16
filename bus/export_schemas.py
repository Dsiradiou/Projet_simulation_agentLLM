"""Génère la documentation du contrat d'événements à partir des classes Pydantic (F4).

Usage : python -m bus.export_schemas
Écrit docs/evenements/README.md (lisible) et docs/evenements/schemas/<type>.json (JSON Schema).
Un test vérifie que ces fichiers sont à jour : la documentation ne peut pas diverger du code.
"""

import json
from pathlib import Path

from bus.events import EVENT_CLASSES, WIRE_REQUIRED, Envelope
from bus.topics import Topic

OUT_DIR = Path("docs/evenements")
_CONSTRAINTS = {
    "minimum": "≥ {}",
    "maximum": "≤ {}",
    "minLength": "longueur ≥ {}",
    "maxLength": "longueur ≤ {}",
    "pattern": "motif `{}`",
}


def _label(prop: dict, defs: dict) -> str:
    if "$ref" in prop:
        name = prop["$ref"].rsplit("/", 1)[-1]
        target = defs.get(name, {})
        return _label(target, defs) if "enum" in target else name
    if "anyOf" in prop:
        return " ou ".join(_label(option, defs) for option in prop["anyOf"])
    if "const" in prop:
        return f"`{prop['const']}`"
    if "enum" in prop:
        return " \\| ".join(f"`{value}`" for value in prop["enum"])
    kind = prop.get("type", "?")
    if kind == "array":
        return f"liste de {_label(prop.get('items', {}), defs)}"
    if kind == "null":
        return "null"
    if "format" in prop:
        return f"{kind} ({prop['format']})"
    return kind


def _constraints(prop: dict) -> str:
    parts = [fmt.format(prop[key]) for key, fmt in _CONSTRAINTS.items() if key in prop]
    for option in prop.get("anyOf", []):
        parts += [fmt.format(option[key]) for key, fmt in _CONSTRAINTS.items() if key in option]
    return ", ".join(parts)


def _fields_table(
    schema: dict,
    defs: dict,
    skip: frozenset[str] = frozenset(),
    required_on_wire: frozenset[str] | None = None,
) -> list[str]:
    required = set(schema.get("required", []))
    lines = [
        "| Champ | Type | Obligatoire | Contraintes | Description |",
        "| --- | --- | --- | --- | --- |",
    ]
    if required_on_wire is not None:
        required = set(required_on_wire)
    for name, prop in schema.get("properties", {}).items():
        if name in skip:
            continue
        lines.append(
            f"| `{name}` | {_label(prop, defs)} | {'oui' if name in required else 'non'} | "
            f"{_constraints(prop)} | {prop.get('description', '')} |"
        )
    return lines


def _payload_schema(cls: type[Envelope]) -> tuple[dict, dict]:
    schema = cls.model_json_schema()
    defs = schema.get("$defs", {})
    payload_ref = schema["properties"]["payload"]["$ref"].rsplit("/", 1)[-1]
    return defs[payload_ref], defs


def render() -> dict[Path, str]:
    files: dict[Path, str] = {}
    envelope = Envelope.model_json_schema()
    lines = [
        "# Contrat d'événements",
        "",
        "> Fichier généré par `python -m bus.export_schemas` à partir de `bus/events.py`.",
        "> Ne pas le modifier à la main.",
        "",
        "Tout ce qui circule sur Kafka est un événement validé par un modèle Pydantic. Chaque type",
        "porte une version de schéma (`schema_version`). Un message qui ne correspond à aucun",
        "schéma connu, y compris une version inconnue, est isolé dans `sim.dlq` sans arrêter la",
        "simulation. Changer la forme d'un payload impose d'incrémenter sa version.",
        "",
        "Le temps simulé se compte en **minutes entières depuis le jour 1 à 00:00** (un jour =",
        "1 440 minutes). Le JSON Schema de chaque type est dans [`schemas/`](schemas/).",
        "",
        "## Enveloppe commune",
        "",
        "Champs présents dans tout événement. « Obligatoire » s'entend pour un message reçu : à la",
        "construction, `event_id` et `wall_time_emitted` sont remplis automatiquement.",
        "",
        *_fields_table(
            envelope,
            envelope.get("$defs", {}),
            skip=frozenset({"payload"}),
            required_on_wire=WIRE_REQUIRED,
        ),
        "",
        "## Types d'événements",
        "",
        "| Type | Topic | Version | Description |",
        "| --- | --- | --- | --- |",
    ]
    for cls in EVENT_CLASSES:
        version = cls.model_fields["schema_version"].default
        type_name = cls.model_fields["type"].default
        lines.append(f"| [`{type_name}`](#{type_name.replace('.', '')}) | `{cls.TOPIC.value}` | "
                     f"{version} | {cls.DESCRIPTION} |")  # fmt: skip
    for topic in Topic:
        classes = [cls for cls in EVENT_CLASSES if cls.TOPIC is topic]
        lines += ["", f"## Topic `{topic.value}`"]
        for cls in classes:
            type_name = cls.model_fields["type"].default
            payload, defs = _payload_schema(cls)
            lines += [
                "",
                f"### {type_name}",
                "",
                f"{cls.DESCRIPTION} Version {cls.model_fields['schema_version'].default}.",
                "",
                "Payload :",
                "",
                *_fields_table(payload, defs),
            ]
            files[OUT_DIR / "schemas" / f"{type_name}.json"] = (
                json.dumps(cls.model_json_schema(), ensure_ascii=False, indent=2) + "\n"
            )
    files[OUT_DIR / "README.md"] = "\n".join(lines) + "\n"
    return files


def main() -> None:
    for path, content in render().items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")
        print(path)


if __name__ == "__main__":
    main()
