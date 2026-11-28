"""Écriture YAML et JSON en aller-retour (enabler 14bb8eb8).

Le travail d'un espace vit dans Git et se relit par `git diff`. Réécrire un
fichier entier depuis des données Python (PyYAML, `json.dumps`) perd ses
commentaires, change l'ordre des clés, les guillemets, l'indentation : le diff ne
montre plus ce qui a changé sur le fond. Ici, la nouvelle valeur est appliquée
**sur le texte existant** : seules les lignes dont le contenu change bougent.

Règles (plan validé par Gael le 28/11/2026) :
- dictionnaire : valeur modifiée remplacée sur place, clé retirée supprimée ; une
  clé ajoutée va à sa place alphabétique si le dictionnaire existant est trié (les
  fichiers écrits par `dump_schema` le sont), sinon à la fin ;
- liste : les éléments existants gardent leur ordre ; les objets sont rapprochés
  par identifiant (`identity`), les scalaires par valeur, sinon par position ; un
  élément ajouté se place après son prédécesseur dans la nouvelle valeur
  (Gael, `valeurs.json` : « l'ordre existant est gardé, une nouvelle valeur à sa
  place dans l'ordre des clés ») ;
- scalaire inchangé : nœud d'origine gardé (guillemets, style) ; scalaire modifié :
  même style de guillemets ; aucun repli de ligne ;
- aucun changement : le texte d'origine, à l'octet près ;
- indentation, saut de ligne final, CRLF : ceux du fichier.

Repli sur le rendu neuf (`fresh`) quand le texte existant ne se relit pas, ou
compte plusieurs documents YAML : jamais d'exception à l'écriture pour une
question de forme.
"""

from __future__ import annotations

import io
import json
import re
from collections.abc import Callable, Hashable, Mapping, Sequence
from itertools import pairwise
from pathlib import Path
from typing import Any

import yaml as yaml_lib
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq
from ruamel.yaml.error import YAMLError
from ruamel.yaml.scalarstring import (
    DoubleQuotedScalarString,
    FoldedScalarString,
    LiteralScalarString,
    SingleQuotedScalarString,
)
from ruamel.yaml.util import load_yaml_guess_indent

# Identité d'un élément de liste : une valeur comparable, ou None (pas d'identité).
Identity = Callable[[Any], Hashable | None]

_ID_KEYS = ("name", "key", "cle", "id", "slug", "path")
_QUOTED = (
    DoubleQuotedScalarString,
    SingleQuotedScalarString,
    LiteralScalarString,
    FoldedScalarString,
)


def plain(value: Any) -> Any:
    """Valeur Python ordinaire (dict, list, scalaires) d'un nœud ruamel."""
    if isinstance(value, dict):
        return {
            str(k) if not isinstance(k, (int, float, bool)) else k: plain(v)
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [plain(v) for v in value]
    if isinstance(value, bool):
        return bool(value)
    if isinstance(value, int):
        return int(value)
    if isinstance(value, float):
        return float(value)
    if isinstance(value, str):
        return str(value)
    return value


def _same(a: Any, b: Any) -> bool:
    """Égalité de contenu : `True` n'est pas `1`, mais `400` et `400.0` sont le même
    nombre (l'application écrit volontairement `40.0` sous la forme `40`)."""
    return _equal(plain(a), plain(b))


def _equal(a: Any, b: Any) -> bool:
    if isinstance(a, bool) or isinstance(b, bool):
        return type(a) is type(b) and a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return a == b
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_equal(a[k], b[k]) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_equal(x, y) for x, y in zip(a, b, strict=True))
    return type(a) is type(b) and a == b


def default_identity(*lists: Sequence[Any]) -> Identity:
    """Identifiant commun aux objets des listes (`name`, `key`, `cle`…), présent
    partout et unique **dans chaque liste** ; sinon la valeur pour des scalaires
    uniques ; sinon rien (rapprochement par position)."""

    def unique(values: list[Any]) -> bool:
        return len(set(map(repr, values))) == len(values)

    items = [i for lst in lists for i in lst]
    if items and all(isinstance(i, dict) for i in items):
        for key in _ID_KEYS:
            if all(isinstance(i.get(key), (str, int)) for i in items) and all(
                unique([i.get(key) for i in lst]) for lst in lists
            ):
                return _by_key(key)
        return lambda _item: None
    if items and all(isinstance(i, (str, int, float, bool)) for i in items):
        if all(unique([plain(i) for i in lst]) for lst in lists):
            return lambda item: repr(plain(item))
    return lambda _item: None


def _by_key(key: str) -> Identity:
    def identity(item: Any) -> Hashable | None:
        value = item.get(key) if isinstance(item, dict) else None
        return value if isinstance(value, (str, int)) else None

    return identity


def _is_sorted(keys: list[Any]) -> bool:
    texts = [str(k) for k in keys]
    return len(texts) >= 2 and texts == sorted(texts)


# --- Fusion d'une nouvelle valeur dans un nœud existant ----------------------------


def _merge_map(
    old: dict[Any, Any],
    new: dict[Any, Any],
    insert: Callable[..., None],
    identity: Identity | None,
    keep_order: bool,
) -> None:
    for key in [k for k in old if k not in new]:
        del old[key]
    sorted_before = _is_sorted(list(old))
    for key, value in new.items():
        if key in old:
            old[key] = merge(old[key], value, identity, keep_order)
            continue
        if sorted_before:
            position = sum(1 for k in old if str(k) < str(key))
            insert(old, position, key, value)
        else:
            old[key] = value


def _insert_ruamel(target: Any, position: int, key: Any, value: Any) -> None:
    target.insert(position, key, value)


def _insert_plain(target: dict[Any, Any], position: int, key: Any, value: Any) -> None:
    items = list(target.items())
    items.insert(position, (key, value))
    target.clear()
    target.update(items)


def _merge_list(
    old: list[Any], new: list[Any], identity: Identity | None, keep_order: bool
) -> list[Any]:
    """Nouvelle liste, éléments rapprochés réutilisés (leur mise en forme reste).
    Ordre : celui de `new` (un réordonnancement voulu est respecté) ; avec
    `keep_order`, celui du fichier, un ajout se plaçant après son prédécesseur
    dans `new` (`valeurs.json`, décision de Gael du 28/11)."""
    ident = identity or default_identity(old, new)
    old_ids = [ident(i) for i in old]
    new_ids = [ident(i) for i in new]
    if any(i is None for i in old_ids + new_ids) or len(set(new_ids)) != len(new_ids):
        # Sans identité : par position.
        out = [merge(o, n, identity, keep_order) for o, n in zip(old, new, strict=False)]
        return out + list(new[len(old) :])
    if not keep_order:
        by_old = dict(zip(old_ids, old, strict=True))
        return [merge(by_old[i], n, identity, keep_order) if i in by_old else n
                for i, n in zip(new_ids, new, strict=True)]  # fmt: skip
    by_id = dict(zip(new_ids, new, strict=True))
    result: list[tuple[Hashable, Any]] = [
        (i, merge(o, by_id[i], identity, keep_order))
        for i, o in zip(old_ids, old, strict=True)
        if i in by_id
    ]
    placed = {i for i, _ in result}
    for index, (i, value) in enumerate(zip(new_ids, new, strict=True)):
        if i in placed:
            continue
        previous = next((p for p in reversed(new_ids[:index]) if p in placed), None)
        at = (
            0
            if previous is None
            else next(k for k, (pid, _) in enumerate(result) if pid == previous) + 1
        )
        result.insert(at, (i, value))
        placed.add(i)
    return [v for _, v in result]


def _restyle(old: Any, new: str) -> Any:
    for style in _QUOTED:
        if isinstance(old, style):
            return style(new)
    return new


def normalize(value: Any) -> Any:
    """Valeur écrivable par ruamel : dictionnaires et listes ordinaires. Les
    documents de l'application portent parfois des n-uplets ou des sous-classes de
    dict, que ruamel refuse (`RepresenterError`)."""
    if isinstance(value, (CommentedMap, CommentedSeq)):
        return value
    if isinstance(value, Mapping):
        return {k: normalize(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [normalize(v) for v in value]
    return value


def merge(old: Any, new: Any, identity: Identity | None = None, keep_order: bool = False) -> Any:
    """`new` appliqué sur `old` en gardant ce qui ne change pas (nœuds, styles)."""
    new = normalize(new)
    if _same(old, new):
        return old
    if isinstance(old, dict) and isinstance(new, dict):
        insert = _insert_ruamel if isinstance(old, CommentedMap) else _insert_plain
        _merge_map(old, new, insert, identity, keep_order)
        return old
    if isinstance(old, list) and isinstance(new, list):
        merged = _merge_list(old, new, identity, keep_order)
        if isinstance(old, CommentedSeq):
            # Remplacement élément par élément : les commentaires rattachés aux
            # positions restent tant que la liste ne se réordonne pas.
            del old[len(merged) :]
            for index, value in enumerate(merged):
                if index < len(old):
                    if old[index] is not value:
                        old[index] = value
                else:
                    old.append(value)
            return old
        return merged
    if isinstance(old, str) and isinstance(new, str):
        return _restyle(old, new)
    return new


# --- YAML ---------------------------------------------------------------------------


def _line_end(text: str) -> str:
    return "\r\n" if "\r\n" in text else "\n"


def _finish(text: str, like: str) -> str:
    """Fin de ligne et saut de ligne final du fichier d'origine."""
    eol = _line_end(like)
    if eol != "\n":
        text = text.replace("\r\n", "\n").replace("\n", eol)
    if not like.endswith(("\n", "\r")):
        text = text.rstrip("\r\n")
    return text


def update_yaml(
    old_text: str,
    new: Any,
    fresh: Callable[[Any], str],
    identity: Identity | None = None,
    keep_order: bool = False,
) -> str:
    """Texte YAML de `new`, écrit sur `old_text` (voir l'en-tête du module)."""
    if (
        re.search(r"^---\s*$", old_text, flags=re.MULTILINE)
        and old_text.strip().count("\n---") >= 1
    ):
        return fresh(new)  # plusieurs documents : hors du périmètre, rendu neuf
    yaml = _yaml()
    try:
        # Indentation devinée sur le texte ; la lecture elle-même garde les
        # guillemets (`preserve_quotes`), que `load_yaml_guess_indent` perd.
        _guess, indent, offset = load_yaml_guess_indent(old_text)
        data = yaml.load(old_text)
    except YAMLError:
        return fresh(new)
    if data is None or not isinstance(data, (CommentedMap, CommentedSeq)):
        return fresh(new)
    if _same(data, new):
        return old_text
    data = merge(data, _styled(yaml, new, fresh), identity, keep_order)
    sequence = indent or 2
    yaml.indent(mapping=_mapping_indent(old_text, sequence), sequence=sequence, offset=offset or 0)
    out = io.StringIO()
    yaml.dump(data, out)
    return _finish(out.getvalue(), old_text)


def _styled(yaml: YAML, new: Any, fresh: Callable[[Any], str]) -> Any:
    """`new` relu depuis son rendu neuf : une partie ajoutée ou modifiée prend le
    style que l'application lui donne d'ordinaire (une liste de points sur une
    ligne dans un graphique…) ; ce qui ne change pas garde celui du fichier."""
    try:
        styled = yaml.load(fresh(new))
    except YAMLError:
        return new
    return styled if _same(styled, new) else new


def _yaml() -> YAML:
    yaml = YAML(typ="rt")
    yaml.preserve_quotes = True
    yaml.width = 1_000_000  # pas de repli des textes longs
    yaml.default_flow_style = False
    yaml.allow_unicode = True
    return yaml


def _mapping_indent(text: str, default: int) -> int:
    """Indentation des dictionnaires imbriqués : la première ligne indentée qui
    suit une clé seule (`clé:`)."""
    lines = text.splitlines()
    for prev, line in pairwise(lines):
        # Clé seule, éventuellement suivie d'une ancre, d'un tag ou d'un commentaire.
        if re.match(r"^\s*[^\s#-][^:]*:\s*([&!]\S+\s*)?(#.*)?$", prev):
            base = len(prev) - len(prev.lstrip())
            current = len(line) - len(line.lstrip())
            if current > base and not line.lstrip().startswith("-"):
                return current - base
    return default


# --- JSON ---------------------------------------------------------------------------


def _json_indent(text: str) -> int | None:
    match = re.search(r"\n( +)\S", text)
    return len(match.group(1)) if match else None


def update_json(
    old_text: str,
    new: Any,
    fresh: Callable[[Any], str],
    identity: Identity | None = None,
    keep_order: bool = False,
) -> str:
    """Texte JSON de `new`, écrit sur `old_text` : ordre des clés et indentation
    d'origine."""
    try:
        data = json.loads(old_text)
    except ValueError:
        return fresh(new)
    if _same(data, new):
        return old_text
    data = merge(data, new, identity, keep_order)
    ascii_only = "\\u" in old_text and not _has_non_ascii(old_text)
    text = json.dumps(data, ensure_ascii=ascii_only, indent=_json_indent(old_text))
    return _finish(text + "\n", old_text)


def _has_non_ascii(text: str) -> bool:
    return any(ord(c) > 127 for c in text)


# --- Point d'écriture unique ----------------------------------------------------------


def render(
    path: str,
    old_text: str | None,
    new: Any,
    fresh: Callable[[Any], str],
    identity: Identity | None = None,
    keep_order: bool = False,
) -> str:
    """Texte à écrire pour `path` : aller-retour sur `old_text` s'il existe, sinon
    rendu neuf (déjà stable et déterministe)."""
    if old_text is None or not old_text.strip():
        return fresh(new)
    if path.endswith(".json"):
        return update_json(old_text, new, fresh, identity, keep_order)
    if path.endswith((".yaml", ".yml")):
        return update_yaml(old_text, new, fresh, identity, keep_order)
    return fresh(new)


def write_document(
    target: Path,
    new: Any,
    fresh: Callable[[Any], str],
    identity: Identity | None = None,
    keep_order: bool = False,
) -> None:
    """Écrit `new` dans `target` sans réécrire ce qui n'a pas changé."""
    try:
        old_text: str | None = target.read_text(encoding="utf-8")
    except (FileNotFoundError, UnicodeDecodeError):
        old_text = None
    text = render(target.name, old_text, new, fresh, identity, keep_order)
    if text == old_text:
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    # newline="" : le texte porte déjà ses fins de ligne (CRLF conservé).
    with target.open("w", encoding="utf-8", newline="") as handle:
        handle.write(text)


def dump_yaml(value: Any) -> str:
    """Rendu neuf habituel de l'application pour un YAML ordinaire : clés dans
    l'ordre du document, blocs, Unicode conservé."""
    return yaml_lib.safe_dump(value, sort_keys=False, allow_unicode=True, default_flow_style=False)


def write_yaml(target: Path, value: Any, identity: Identity | None = None) -> None:
    """`write_document` avec le rendu YAML ordinaire (`dump_yaml`) pour un nouveau
    fichier."""
    write_document(target, value, dump_yaml, identity)
