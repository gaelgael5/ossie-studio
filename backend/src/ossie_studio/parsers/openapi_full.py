"""Contrat OpenAPI **complet** à côté de son découpage (ticket docflow e79d008f).

Gael, 24/11/2026 : « le parseur raw a tout découpé. C'est bien mais il faut
également pouvoir voir le contrat complet. Tu vas le recopier dans le
répertoire sous un nom spécifique contrat.full.openapi.yaml ; le nom est en dur
pour comprendre dans l'affichage que c'est un répertoire qui expose un contrat
OAS. »

Ce module sait :
- nommer la copie (`raw/contracts/<dossier>/contrat.full.openapi.yaml`, le
  dossier étant celui où l'explorateur range les schémas du contrat) ;
- tenir l'index des copies (`raw/contracts-index.yaml`) que l'explorateur lit ;
- tirer d'un contrat une **vue lisible** (opérations, ce qu'on donne, ce qu'on
  reçoit) que l'écran met en mots simples.

La copie est **intégrale et telle quelle** (JSON compris : un JSON est un YAML
valide) : c'est le contrat imposé par le tiers, on ne le reformate pas.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

import yaml

from ossie_studio.parsers.base import OutputAction

FULL_CONTRACT_NAME = "contrat.full.openapi.yaml"
CONTRACTS_DIR = "contracts"
CONTRACTS_INDEX = "contracts-index.yaml"
CONTRACTS_INDEX_VERSION = 1

_HTTP_METHODS = ("get", "post", "put", "patch", "delete", "options", "head", "trace")
_SCHEMA_REF_PREFIX = "#/components/schemas/"
# Garde contre les `$ref` circulaires des composants (paramètre → paramètre…).
_MAX_REF_DEPTH = 8


def _map(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def is_full_contract(path: str) -> bool:
    return PurePosixPath(path).name.endswith(FULL_CONTRACT_NAME)


def contract_folder(source: str, *, repository: bool) -> str:
    """Dossier d'affichage du contrat — le même que celui de ses schémas dans
    l'arborescence d'origine (`explorer.object_folder`) : le dossier du fichier
    dans un dépôt, le fichier lui-même pour un contrat déposé."""
    if not repository:
        return source
    return source.rsplit("/", 1)[0] if "/" in source else ""


def copy_names(sources: Iterable[str], *, repository: bool) -> dict[str, str]:
    """Contrat d'origine → chemin de sa copie sous `contracts/`.

    Un seul contrat dans le dossier : exactement `contrat.full.openapi.yaml`
    (le nom en dur demandé). Plusieurs (cas réel : `api/OAS/send-event` porte
    les contrats AMS et PSSA) : `<radical>.contrat.full.openapi.yaml`, pour que
    chacun ait sa copie sans perdre le suffixe qui le signale."""
    by_folder: dict[str, list[str]] = {}
    for source in sorted(set(sources)):
        by_folder.setdefault(contract_folder(source, repository=repository), []).append(source)
    out: dict[str, str] = {}
    for folder, items in by_folder.items():
        prefix = f"{folder}/" if folder else ""
        for source in items:
            name = (
                FULL_CONTRACT_NAME
                if len(items) == 1
                else f"{PurePosixPath(source).stem}.{FULL_CONTRACT_NAME}"
            )
            out[source] = f"{prefix}{name}"
    return out


@dataclass(frozen=True)
class ContractEntry:
    """Une copie de contrat : son chemin sous `contracts/`, le contrat d'origine,
    son titre, sa version déclarée et son nombre d'opérations."""

    path: str
    source: str
    title: str
    version: str
    operations: int


def parse_contracts_index(text: str | None) -> dict[str, ContractEntry]:
    try:
        data = yaml.safe_load(text) if text else None
    except yaml.YAMLError:
        return {}
    rows = data.get("contracts") if isinstance(data, dict) else None
    out: dict[str, ContractEntry] = {}
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict) or not row.get("path"):
            continue
        entry = ContractEntry(
            path=str(row["path"]),
            source=str(row.get("source", "")),
            title=str(row.get("title", "")),
            version=str(row.get("version", "")),
            operations=int(row.get("operations") or 0),
        )
        out[entry.path] = entry
    return out


def dump_contracts_index(entries: dict[str, ContractEntry]) -> str:
    mapping = {
        "version": CONTRACTS_INDEX_VERSION,
        "contracts": [
            {
                "path": e.path,
                "source": e.source,
                "title": e.title,
                "version": e.version,
                "operations": e.operations,
            }
            for e in (entries[p] for p in sorted(entries))
        ],
    }
    return yaml.safe_dump(mapping, sort_keys=False, allow_unicode=True, default_flow_style=False)


def operation_count(doc: dict[str, Any]) -> int:
    paths = doc.get("paths")
    if not isinstance(paths, dict):
        return 0
    return sum(
        1
        for item in paths.values()
        if isinstance(item, dict)
        for m in _HTTP_METHODS
        if isinstance(item.get(m), dict)
    )


def full_contract_actions(
    contracts: dict[str, tuple[bytes, dict[str, Any]]],
    previous_index: str | None,
    *,
    repository: bool,
) -> tuple[list[OutputAction], list[str]]:
    """Écritures et suppressions (relatives à `raw/`) des copies intégrales.

    `contracts` : contrats **valides** de la source (chemin → contenu, document
    chargé). Toutes les copies sont recalculées à chaque collecte — le nom de
    l'une dépend de ses voisines (un second contrat dans le dossier renomme la
    première) ; une copie identique ne change rien au commit. Une copie dont le
    contrat a disparu ou est devenu invalide est supprimée."""
    names = copy_names(contracts, repository=repository)
    entries: dict[str, ContractEntry] = {}
    writes: list[OutputAction] = []
    for source, (raw, doc) in sorted(contracts.items()):
        rel = names[source]
        info = _map(doc.get("info"))
        entries[rel] = ContractEntry(
            path=rel,
            source=source,
            title=str(info.get("title", "")),
            version=str(info.get("version", "")),
            operations=operation_count(doc),
        )
        writes.append(OutputAction(path=f"{CONTRACTS_DIR}/{rel}", content=raw.decode("utf-8-sig")))
    deletes = [
        f"{CONTRACTS_DIR}/{old}"
        for old in sorted(parse_contracts_index(previous_index))
        if old not in entries
    ]
    if entries or previous_index is not None:
        writes.append(OutputAction(path=CONTRACTS_INDEX, content=dump_contracts_index(entries)))
    return writes, deletes


# --- Vue lisible --------------------------------------------------------------


def _resolve(doc: dict[str, Any], node: Any, depth: int = 0) -> Any:
    """Suit un `$ref` interne vers un composant **autre qu'un schéma**
    (paramètre, corps, réponse partagés) : l'écran montre ce qu'il contient. Les
    schémas, eux, restent des noms (ce sont les objets découpés)."""
    while (
        isinstance(node, dict)
        and isinstance(node.get("$ref"), str)
        and not node["$ref"].startswith(_SCHEMA_REF_PREFIX)
        and depth < _MAX_REF_DEPTH
    ):
        ref = node["$ref"]
        if not ref.startswith("#/"):
            return {}
        target: Any = doc
        for part in ref[2:].split("/"):
            part = part.replace("~1", "/").replace("~0", "~")
            target = target.get(part) if isinstance(target, dict) else None
        node = target if isinstance(target, dict) else {}
        depth += 1
    return node


def describe_schema(node: Any, schema_files: dict[str, str], depth: int = 0) -> dict[str, Any]:
    """Ce que transporte un schéma, en une forme que l'écran met en mots :
    `ref` (un objet du contrat, avec son fichier découpé), `list` (une liste de…),
    `object` (un objet sans nom, ses champs), `type` (texte, nombre…)."""
    if not isinstance(node, dict) or depth > 3:
        return {"kind": "type", "type": ""}
    ref = node.get("$ref")
    if isinstance(ref, str):
        if ref.startswith(_SCHEMA_REF_PREFIX):
            name = ref[len(_SCHEMA_REF_PREFIX) :]
            return {"kind": "ref", "name": name, "file": schema_files.get(name, "")}
        return {"kind": "type", "type": ""}
    for combo in ("oneOf", "anyOf", "allOf"):
        options = node.get(combo)
        if isinstance(options, list) and options:
            return {
                "kind": combo,
                "options": [describe_schema(o, schema_files, depth + 1) for o in options[:6]],
            }
    node_type = node.get("type")
    if isinstance(node_type, list):
        node_type = next((t for t in node_type if t != "null"), "")
    if node_type == "array" or "items" in node:
        return {"kind": "list", "of": describe_schema(node.get("items"), schema_files, depth + 1)}
    props = node.get("properties")
    if node_type == "object" or isinstance(props, dict):
        return {
            "kind": "object",
            "fields": sorted(props)[:30] if isinstance(props, dict) else [],
        }
    return {
        "kind": "type",
        "type": str(node_type or ""),
        "format": str(node.get("format") or ""),
        "enum": [str(v) for v in node.get("enum") or []][:12],
    }


def _content_schema(doc: dict[str, Any], content: Any, files: dict[str, str]) -> dict[str, Any]:
    """Premier type de contenu (JSON de préférence) et ce qu'il transporte."""
    if not isinstance(content, dict) or not content:
        return {}
    media = next((m for m in content if "json" in m), next(iter(content)))
    body = content.get(media)
    schema = body.get("schema") if isinstance(body, dict) else None
    return {"media": media, "schema": describe_schema(schema, files) if schema else None}


def _parameters(doc: dict[str, Any], *groups: Any) -> list[dict[str, Any]]:
    """Paramètres du chemin puis de l'opération ; celui de l'opération remplace
    celui du chemin de même nom et même emplacement (règle OpenAPI)."""
    seen: dict[tuple[str, str], dict[str, Any]] = {}
    for group in groups:
        for raw in group if isinstance(group, list) else []:
            param = _resolve(doc, raw)
            if not isinstance(param, dict) or not param.get("name"):
                continue
            key = (str(param.get("name")), str(param.get("in", "")))
            seen[key] = param
    return [
        {
            "name": name,
            "in": where,
            "required": bool(p.get("required")) or where == "path",
            "description": str(p.get("description") or ""),
            "schema": describe_schema(p.get("schema"), {}) if p.get("schema") else None,
        }
        for (name, where), p in seen.items()
    ]


def _object_detail(schemas: dict[str, Any], name: str, files: dict[str, str]) -> dict[str, Any]:
    """Un objet de `components/schemas` pour le panneau des propriétés (ticket
    82686dc0) : description, propriétés (type en forme `describe_schema`,
    obligatoire, description, valeurs possibles) et objets dont il hérite.
    `allOf` : les membres nommés deviennent « hérite de », leurs propriétés et
    celles des membres en ligne sont reprises (on montre tout ce que l'objet a)."""
    node = _map(schemas.get(name))
    extends: list[str] = []
    props: dict[str, Any] = {}
    required: set[str] = set()
    seen: set[str] = {name}

    def gather(current: dict[str, Any], depth: int = 0) -> None:
        if depth > 6:
            return
        for member in current.get("allOf") or []:
            member = _map(member)
            ref = member.get("$ref")
            if isinstance(ref, str) and ref.startswith(_SCHEMA_REF_PREFIX):
                parent = ref[len(_SCHEMA_REF_PREFIX) :]
                if parent not in extends:
                    extends.append(parent)
                if parent not in seen:
                    seen.add(parent)
                    gather(_map(schemas.get(parent)), depth + 1)
            else:
                gather(member, depth + 1)
        for key, value in _map(current.get("properties")).items():
            props.setdefault(str(key), value)
        required.update(str(r) for r in current.get("required") or [])

    gather(node)
    return {
        "name": name,
        "file": files.get(name, ""),
        "description": str(node.get("description") or ""),
        # Un objet composé (`allOf`) se lit comme un objet : ses propriétés sont réunies.
        "shape": (
            {"kind": "object", "fields": sorted(props)[:30]}
            if "allOf" in node
            else describe_schema(node, files)
        ),
        "extends": extends,
        "properties": [
            {
                "name": key,
                "required": key in required,
                "description": str(_map(value).get("description") or ""),
                "schema": describe_schema(value, files),
            }
            for key, value in props.items()
        ],
    }


def readable_contract(doc: dict[str, Any], schema_files: dict[str, str]) -> dict[str, Any]:
    """Vue lisible d'un contrat : présentation, adresses, une entrée par
    opération (ce qu'on donne, ce qu'on reçoit) et les objets qu'il décrit.
    `schema_files` : nom d'objet de `components/schemas` → fichier découpé dans
    `raw/` (pour ouvrir l'objet depuis la vue)."""
    info = _map(doc.get("info"))
    servers = [
        str(s.get("url")) for s in doc.get("servers") or [] if isinstance(s, dict) and s.get("url")
    ]
    operations: list[dict[str, Any]] = []
    paths = _map(doc.get("paths"))
    for path, raw_item in paths.items():
        item = _resolve(doc, raw_item)
        if not isinstance(item, dict):
            continue
        for method in _HTTP_METHODS:
            op = item.get(method)
            if not isinstance(op, dict):
                continue
            body = _resolve(doc, op.get("requestBody"))
            responses = _map(op.get("responses"))
            operations.append(
                {
                    "method": method.upper(),
                    "path": str(path),
                    "summary": str(op.get("summary") or ""),
                    "description": str(op.get("description") or ""),
                    "operation_id": str(op.get("operationId") or ""),
                    "tags": [str(t) for t in op.get("tags") or []],
                    "deprecated": bool(op.get("deprecated")),
                    "parameters": _parameters(doc, item.get("parameters"), op.get("parameters")),
                    "body": (
                        {
                            "required": bool(body.get("required")),
                            "description": str(body.get("description") or ""),
                            **_content_schema(doc, body.get("content"), schema_files),
                        }
                        if isinstance(body, dict) and body
                        else None
                    ),
                    "responses": [
                        {
                            "code": str(code),
                            "description": str(resp.get("description") or ""),
                            **_content_schema(doc, resp.get("content"), schema_files),
                        }
                        for code, raw_resp in sorted(responses.items(), key=lambda kv: str(kv[0]))
                        for resp in [_resolve(doc, raw_resp)]
                        if isinstance(resp, dict)
                    ],
                }
            )
    components = _map(doc.get("components"))
    schemas = _map(components.get("schemas"))
    return {
        "openapi": str(doc.get("openapi", "")),
        "title": str(info.get("title", "")),
        "version": str(info.get("version", "")),
        "description": str(info.get("description") or ""),
        "servers": servers,
        "operations": operations,
        "objects": [{"name": n, "file": schema_files.get(n, "")} for n in sorted(schemas)],
        # Détail de chaque objet, pour le graphe d'une méthode et le panneau des
        # propriétés (ticket 82686dc0) : tout vient du contrat, en un appel.
        "schemas": {n: _object_detail(schemas, n, schema_files) for n in sorted(schemas)},
    }
