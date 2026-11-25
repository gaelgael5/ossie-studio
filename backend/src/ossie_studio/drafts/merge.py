"""Fusion à trois d'un objet du brouillon avec sa version publiée.

Décision (feature « Fenêtre de fusion par propriétés ») : les objets que
l'application connaît (YAML, JSON) se fusionnent **par propriétés**, jamais
ligne à ligne — une fusion textuelle d'un YAML peut produire un fichier valide
mais faux. Règles : une propriété changée d'un seul côté prend ce changement ;
changée des deux côtés différemment → conflit, l'utilisateur choisit. Listes et
valeurs simples sont atomiques. Les autres fichiers se replient sur une fusion
texte (`git merge-file`)."""

from __future__ import annotations

import json
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml

from ossie_studio.parsers.conventions import SCHEMA_SUFFIX, dump_schema, load_schema


class _Missing:
    def __repr__(self) -> str:
        return "MISSING"


MISSING: Any = _Missing()


@dataclass(frozen=True)
class Conflict:
    pointer: str  # JSON Pointer de la propriété (« » = l'objet entier)
    base: Any
    theirs: Any
    mine: Any


@dataclass
class MergeResult:
    merged: Any
    conflicts: list[Conflict] = field(default_factory=list)


def _escape(key: str) -> str:
    return key.replace("~", "~0").replace("/", "~1")


def merge3(base: Any, theirs: Any, mine: Any, pointer: str = "") -> MergeResult:
    """Fusion récursive par clé. `MISSING` = absent d'un côté. En conflit, la
    valeur proposée est celle du brouillon (`mine`), à confirmer."""
    if theirs == mine:
        return MergeResult(theirs)
    if mine == base:
        return MergeResult(theirs)
    if theirs == base:
        return MergeResult(mine)
    if isinstance(theirs, dict) and isinstance(mine, dict):
        base_dict = base if isinstance(base, dict) else {}
        merged: dict[str, Any] = {}
        conflicts: list[Conflict] = []
        keys = list(dict.fromkeys([*base_dict, *theirs, *mine]))
        for key in keys:
            sub = merge3(
                base_dict.get(key, MISSING),
                theirs.get(key, MISSING),
                mine.get(key, MISSING),
                f"{pointer}/{_escape(str(key))}",
            )
            conflicts.extend(sub.conflicts)
            if sub.merged is not MISSING:
                merged[key] = sub.merged
        return MergeResult(merged, conflicts)
    return MergeResult(mine, [Conflict(pointer, base, theirs, mine)])


Kind = Literal["auto", "conflict", "text", "text_conflict", "deleted_upstream", "deleted_in_draft"]


@dataclass
class ObjectMerge:
    kind: Kind
    result: bytes | None
    conflicts: list[Conflict] = field(default_factory=list)
    base: Any = None
    theirs: Any = None
    mine: Any = None
    structured: bool = False


def is_structured(path: str) -> bool:
    return path.endswith((".json", ".yaml", ".yml"))


def parse(path: str, data: bytes | None) -> Any:
    if data is None:
        return MISSING
    text = data.decode("utf-8")
    if path.endswith(SCHEMA_SUFFIX):
        return load_schema(text) if text.strip() else {}
    if path.endswith(".json"):
        return json.loads(text) if text.strip() else {}
    return yaml.safe_load(text) if text.strip() else {}


def serialize(path: str, value: Any) -> bytes:
    """Même forme que celle qu'écrit l'application : schéma YAML déterministe
    (`dump_schema`), autre YAML dans l'ordre des clés (`dump_manifest`)."""
    if path.endswith(SCHEMA_SUFFIX):
        return dump_schema(value).encode("utf-8")
    if path.endswith(".json"):
        return (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode()
    return yaml.safe_dump(
        value, sort_keys=False, allow_unicode=True, default_flow_style=False
    ).encode("utf-8")


def merge_object(
    path: str, base: bytes | None, theirs: bytes | None, mine: bytes | None
) -> ObjectMerge:
    """`base` : version de départ du brouillon ; `theirs` : version publiée
    actuelle ; `mine` : version du brouillon. None = absent."""
    if theirs == mine:
        return ObjectMerge("auto", theirs)
    if theirs is None and base is not None:
        # Supprimé côté publié, modifié dans le brouillon : garder ou supprimer.
        return _structured_or_text(path, base, theirs, mine, kind="deleted_upstream")
    if mine is None:
        # Supprimé dans le brouillon, modifié côté publié.
        return _structured_or_text(path, base, theirs, mine, kind="deleted_in_draft")
    if not is_structured(path):
        return _merge_text(base, theirs, mine)
    try:
        b, t, m = parse(path, base), parse(path, theirs), parse(path, mine)
    except (ValueError, yaml.YAMLError):
        return _merge_text(base, theirs, mine)
    result = merge3({} if b is MISSING else b, t, m)
    merged = serialize(path, result.merged)
    kind: Kind = "conflict" if result.conflicts else "auto"
    return ObjectMerge(kind, merged, result.conflicts, b, t, m, structured=True)


def _structured_or_text(
    path: str, base: bytes | None, theirs: bytes | None, mine: bytes | None, *, kind: Kind
) -> ObjectMerge:
    if is_structured(path):
        try:
            return ObjectMerge(
                kind,
                mine,
                [],
                parse(path, base),
                parse(path, theirs),
                parse(path, mine),
                structured=True,
            )
        except (ValueError, yaml.YAMLError):
            pass
    return ObjectMerge(kind, mine, [], _text(base), _text(theirs), _text(mine))


def _text(data: bytes | None) -> str | None:
    return data.decode("utf-8", errors="replace") if data is not None else None


def _merge_text(base: bytes | None, theirs: bytes | None, mine: bytes | None) -> ObjectMerge:
    with tempfile.TemporaryDirectory() as tmp:
        files = {}
        for name, data in (("mine", mine), ("base", base), ("theirs", theirs)):
            target = Path(tmp) / name
            target.write_bytes(data or b"")
            files[name] = str(target)
        proc = subprocess.run(  # noqa: S603 — arguments fixes, aucune saisie
            ["git", "merge-file", "-p", files["mine"], files["base"], files["theirs"]],  # noqa: S607
            capture_output=True,
            check=False,
        )
    if proc.returncode == 0:
        return ObjectMerge("text", proc.stdout, [], _text(base), _text(theirs), _text(mine))
    return ObjectMerge("text_conflict", mine, [], _text(base), _text(theirs), _text(mine))


def set_pointer(document: Any, pointer: str, value: Any) -> Any:
    """Pose `value` au pointeur (MISSING = supprimer la clé) ; rend le document."""
    if pointer == "":
        return value
    parts = [p.replace("~1", "/").replace("~0", "~") for p in pointer.lstrip("/").split("/")]
    node = document
    for part in parts[:-1]:
        node = node.setdefault(_key(node, part), {})
    if value is MISSING:
        node.pop(_key(node, parts[-1]), None)
    else:
        node[_key(node, parts[-1])] = value
    return document


def _key(node: dict[Any, Any], part: str) -> Any:
    """La clé existante dont la forme texte est `part` (une clé YAML peut être un
    entier : `responses: {200: …}`), sinon `part` tel quel."""
    for key in node:
        if str(key) == part:
            return key
    return part
