"""Fusion à trois d'un objet du brouillon avec sa version publiée.

Décision (feature « Fenêtre de fusion par propriétés ») : les objets que
l'application connaît (YAML, JSON) se fusionnent **par propriétés**, jamais
ligne à ligne — une fusion textuelle d'un YAML peut produire un fichier valide
mais faux. Règles : une propriété changée d'un seul côté prend ce changement ;
changée des deux côtés différemment → conflit, l'utilisateur choisit. Une liste
dont les éléments portent un identifiant commun (`name`, `key`…) se fusionne
élément par élément (enabler f9bd4d93) ; une liste sans identifiant et les
valeurs simples sont atomiques. Les autres fichiers se replient sur une fusion
texte (`git merge-file`)."""

from __future__ import annotations

import json
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml

from ossie_studio.parsers.conventions import SCHEMA_SUFFIX, dump_schema, load_schema
from ossie_studio.roundtrip import default_identity, render
from ossie_studio.sources.draft_changes import is_collected_raw


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
    # Blocs pris d'un seul côté sans décision (badge « N fusions auto » de la
    # fenêtre, feature fcf32f4a).
    auto: list[str] = field(default_factory=list)


# Marque d'un conflit non tranché dans le texte proposé au milieu de la fenêtre de
# fusion (maquette du 28/11). Un scalaire YAML/JSON valide : le texte reste lisible
# tant qu'elle y est, et le serveur refuse de l'enregistrer.
CONFLICT_MARKER = "⟨ à choisir ⟩"


def _escape(key: str) -> str:
    return key.replace("~", "~0").replace("/", "~1")


def merge3(base: Any, theirs: Any, mine: Any, pointer: str = "") -> MergeResult:
    """Fusion récursive par clé. `MISSING` = absent d'un côté. En conflit, la
    valeur proposée est celle du brouillon (`mine`), à confirmer."""
    if theirs == mine:
        return MergeResult(theirs)
    if mine == base:
        return MergeResult(theirs, auto=[pointer] if pointer else [])
    if theirs == base:
        return MergeResult(mine, auto=[pointer] if pointer else [])
    if isinstance(theirs, dict) and isinstance(mine, dict):
        base_dict = base if isinstance(base, dict) else {}
        merged: dict[str, Any] = {}
        result = MergeResult(merged)
        keys = list(dict.fromkeys([*base_dict, *theirs, *mine]))
        for key in keys:
            sub = merge3(
                base_dict.get(key, MISSING),
                theirs.get(key, MISSING),
                mine.get(key, MISSING),
                f"{pointer}/{_escape(str(key))}",
            )
            _absorb(result, sub)
            if sub.merged is not MISSING:
                merged[key] = sub.merged
        return result
    if isinstance(theirs, list) and isinstance(mine, list):
        keyed = _merge_list(base if isinstance(base, list) else [], theirs, mine, pointer)
        if keyed is not None:
            return keyed
    return MergeResult(mine, [Conflict(pointer, base, theirs, mine)])


def _absorb(into: MergeResult, sub: MergeResult) -> None:
    into.conflicts.extend(sub.conflicts)
    into.auto.extend(sub.auto)


def _merge_list(
    base: list[Any], theirs: list[Any], mine: list[Any], pointer: str
) -> MergeResult | None:
    """Fusion élément par élément d'une liste dont les éléments s'identifient
    (enabler f9bd4d93) ; None si aucun identifiant fiable : la liste reste alors
    atomique. Raison : sans clé, rapprocher par position ferait fusionner deux
    éléments différents, un résultat valide mais faux."""
    identity = default_identity(base, theirs, mine)
    ids = [[identity(item) for item in lst] for lst in (base, theirs, mine)]
    if any(i is None for lst in ids for i in lst):
        return None
    by_id = [
        dict(zip(lst_ids, lst, strict=True))
        for lst_ids, lst in zip(ids, (base, theirs, mine), strict=True)
    ]
    merged: list[Any] = []
    result = MergeResult(merged)
    for key in _list_order(ids[1], ids[2]):
        sub = merge3(
            by_id[0].get(key, MISSING),
            by_id[1].get(key, MISSING),
            by_id[2].get(key, MISSING),
            f"{pointer}/{_escape(_element_name(key))}",
        )
        _absorb(result, sub)
        if sub.merged is not MISSING:
            merged.append(sub.merged)
    return result


def _element_name(key: Any) -> str:
    """Segment de pointeur d'un élément : son identifiant (« segment »), ou sa
    valeur pour une liste de scalaires."""
    return key if isinstance(key, str) and not key.startswith("'") else str(key).strip("'\"")


def _list_order(theirs: list[Any], mine: list[Any]) -> list[Any]:
    """Ordre du brouillon ; un élément ajouté côté publié se place après son
    prédécesseur dans la version publiée (en tête s'il n'en a pas)."""
    order = list(mine)
    for index, key in enumerate(theirs):
        if key in order:
            continue
        position = 0
        for previous in reversed(theirs[:index]):
            if previous in order:
                position = order.index(previous) + 1
                break
        order.insert(position, key)
    return order


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
    auto: list[str] = field(default_factory=list)
    # Repli texte en conflit : la sortie de `git merge-file` avec ses marques, pour
    # le milieu de la fenêtre de fusion (feature fcf32f4a).
    marked: bytes | None = None


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


def _fresh(path: str) -> Callable[[Any], str]:
    """Rendu neuf : même forme que celle qu'écrit l'application — schéma YAML
    déterministe (`dump_schema`), autre YAML dans l'ordre des clés."""
    if path.endswith(SCHEMA_SUFFIX):
        return dump_schema
    if path.endswith(".json"):
        return lambda v: json.dumps(v, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
    return lambda v: yaml.safe_dump(
        v, sort_keys=False, allow_unicode=True, default_flow_style=False
    )


def serialize(path: str, value: Any, like: bytes | None = None) -> bytes:
    """Texte de `value` pour `path`. `like` : texte existant (en général la version
    du brouillon) ; le résultat d'une fusion s'écrit dessus, en aller-retour, pour
    que seules les lignes changées bougent (enabler 14bb8eb8). Sans `like` : rendu
    neuf. Objet raw d'une source : toujours le rendu de la collecte, jamais
    l'aller-retour (décision de Gael, 28/11 : seule la collecte écrit ces fichiers)."""
    if like is None or is_collected_raw(path):
        return _fresh(path)(value).encode("utf-8")
    try:
        old = like.decode("utf-8")
    except UnicodeDecodeError:
        old = None
    return render(path, old, value, _fresh(path)).encode("utf-8")


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
    merged = serialize(path, result.merged, like=mine)
    kind: Kind = "conflict" if result.conflicts else "auto"
    return ObjectMerge(kind, merged, result.conflicts, b, t, m, structured=True, auto=result.auto)


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
            [  # noqa: S607
                "git",
                "merge-file",
                "-p",
                "-L",
                "brouillon",
                "-L",
                "base",
                "-L",
                "publié",
                files["mine"],
                files["base"],
                files["theirs"],
            ],
            capture_output=True,
            check=False,
        )
    if proc.returncode == 0:
        return ObjectMerge("text", proc.stdout, [], _text(base), _text(theirs), _text(mine))
    return ObjectMerge(
        "text_conflict", mine, [], _text(base), _text(theirs), _text(mine), marked=proc.stdout
    )


def set_pointer(document: Any, pointer: str, value: Any) -> Any:
    """Pose `value` au pointeur (MISSING = supprimer) ; rend le document. Dans une
    liste, un segment désigne l'élément par son identifiant (enabler f9bd4d93)."""
    if pointer == "":
        return value
    parts = [p.replace("~1", "/").replace("~0", "~") for p in pointer.lstrip("/").split("/")]
    node = document
    for part in parts[:-1]:
        if isinstance(node, list):
            index = _index(node, part)
            if index is None:
                node.append({})
                index = len(node) - 1
            node = node[index]
        else:
            node = node.setdefault(_key(node, part), {})
    last = parts[-1]
    if isinstance(node, list):
        index = _index(node, last)
        if value is MISSING:
            if index is not None:
                del node[index]
        elif index is None:
            node.append(value)
        else:
            node[index] = value
    elif value is MISSING:
        node.pop(_key(node, last), None)
    else:
        node[_key(node, last)] = value
    return document


def _index(items: list[Any], part: str) -> int | None:
    """Position de l'élément nommé `part` dans une liste fusionnée par clé."""
    identity = default_identity(items)
    for index, item in enumerate(items):
        key = identity(item)
        if key is not None and _element_name(key) == part:
            return index
    return None


def _key(node: dict[Any, Any], part: str) -> Any:
    """La clé existante dont la forme texte est `part` (une clé YAML peut être un
    entier : `responses: {200: …}`), sinon `part` tel quel."""
    for key in node:
        if str(key) == part:
            return key
    return part
