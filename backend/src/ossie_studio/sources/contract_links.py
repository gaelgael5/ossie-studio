"""Relier les points d'entrée HTTP du code aux opérations des contrats OpenAPI de
la même source (epic « Cartographie applicative dans raw », ticket 90ed0967).

Étape du **socle**, après les parseurs et la résolution des types : elle voit
l'ensemble final de `raw/` — les `.extend.yaml` (méthodes, fonctions libres et
leurs points d'entrée) et les copies intégrales des contrats
(`contracts/…/contrat.full.openapi.yaml`, listées par `contracts-index.yaml`).

Pour chaque point d'entrée HTTP dont la route est connue, elle cherche les
opérations de même verbe et de même route :

- les paramètres se comparent par position (`{id}`, `{commandeId}`, `{id:guid}`,
  `{id?}` se valent) ; la casse et la barre finale sont ignorées ;
- le préfixe des `servers` du contrat est pris en compte (`/api/v1` + `/commandes`
  répond à une route de code `api/v1/commandes`, comme `/commandes` seul) ;
- trouvée → `contract: {status: linked, operations: [...]}` (toutes, une même
  opération pouvant figurer dans deux contrats) ; sinon `{status: absent}`.

Une route inconnue (`route: null`) n'est pas comparée. Une source sans contrat
n'a rien à comparer : les champs `contract` d'une collecte précédente sont
retirés. Un `.extend.yaml` n'est réécrit que si son état au contrat change ; le
contrat, lui, n'est jamais modifié.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlparse

from ossie_studio.parsers.application import dump_functions, is_extend_name
from ossie_studio.parsers.base import OutputAction
from ossie_studio.parsers.conventions import ConventionError, SchemaReadError, load_schema
from ossie_studio.parsers.openapi import OpenApiError, load_document
from ossie_studio.parsers.openapi_full import CONTRACTS_DIR, CONTRACTS_INDEX, parse_contracts_index

HTTP = "http"
LINKED = "linked"
ABSENT = "absent"

_HTTP_METHODS = ("get", "post", "put", "patch", "delete", "options", "head")
_PARAM = re.compile(r"\{[^}]*\}")

# (verbe, route normalisée) → opérations des contrats.
OperationIndex = dict[tuple[str, str], list[dict[str, str]]]


def route_key(route: str) -> str:
    """La forme comparable d'une route : segments en minuscules, chaque paramètre
    réduit à `{}`, sans barre de tête ni de fin, sans chaîne de requête."""
    # Paramètres réduits d'abord : le `?` d'un paramètre facultatif (`{id?}`)
    # n'est pas le début d'une chaîne de requête.
    path = _PARAM.sub("{}", route).split("?", 1)[0].strip()
    segments = [s for s in path.split("/") if s and s != "~"]
    return "/" + "/".join(s.casefold() for s in segments)


def server_prefixes(doc: Mapping[str, Any]) -> list[str]:
    """Les chemins des `servers` du contrat (`https://h/api/v1` → `/api/v1`) ;
    une variable du chemin est remplacée par sa valeur par défaut (sans défaut,
    l'adresse est écartée) ; une adresse sans chemin ne donne rien."""
    out: list[str] = []
    for server in doc.get("servers") or []:
        if not isinstance(server, dict) or not isinstance(server.get("url"), str):
            continue
        url = server["url"]
        raw_vars = server.get("variables")
        variables: dict[str, Any] = raw_vars if isinstance(raw_vars, dict) else {}

        def default(match: re.Match[str], variables: dict[str, Any] = variables) -> str:
            var = variables.get(match.group(0)[1:-1])
            value = var.get("default") if isinstance(var, dict) else None
            return str(value) if value is not None else match.group(0)

        # Seul le chemin compte : une variable dans l'hôte est sans effet.
        path = _PARAM.sub(default, urlparse(url).path if "://" in url else url)
        if _PARAM.search(path):
            continue
        prefix = route_key(path)
        if prefix != "/" and prefix not in out:
            out.append(prefix)
    return out


def operation_index(contracts: Mapping[str, Mapping[str, Any]]) -> OperationIndex:
    """Les opérations des contrats (copie sous `contracts/` → document chargé),
    rangées par verbe et route normalisée — avec et sans le préfixe des `servers`."""
    index: OperationIndex = {}
    for file in sorted(contracts):
        doc = contracts[file]
        prefixes = server_prefixes(doc)
        raw_paths = doc.get("paths")
        paths: dict[str, Any] = raw_paths if isinstance(raw_paths, dict) else {}
        for path, item in paths.items():
            if not isinstance(item, dict):
                continue
            for method in _HTTP_METHODS:
                op = item.get(method)
                if not isinstance(op, dict):
                    continue
                verb = method.upper()
                operation = {"file": file, "verb": verb, "path": str(path)}
                if op.get("operationId"):
                    operation["operationId"] = str(op["operationId"])
                own = route_key(str(path))
                keys = {own, *(route_key(prefix + own) for prefix in prefixes)}
                for key in sorted(keys):
                    found = index.setdefault((verb, key), [])
                    if operation not in found:
                        found.append(operation)
    return index


def link_point(point: Mapping[str, Any], index: OperationIndex | None) -> dict[str, Any]:
    """Le point d'entrée avec son état au contrat (`index` None : source sans
    contrat, rien à comparer)."""
    out = {k: v for k, v in point.items() if k != "contract"}
    route, verb = point.get("route"), point.get("verb")
    if index is None or point.get("kind") != HTTP or not route or not verb:
        return out
    operations = index.get((str(verb).upper(), route_key(str(route))), [])
    out["contract"] = (
        {"status": LINKED, "operations": [dict(o) for o in operations]}
        if operations
        else {"status": ABSENT}
    )
    return out


def link_document(document: Mapping[str, Any], index: OperationIndex | None) -> dict[str, Any]:
    """Le `.extend.yaml` avec l'état au contrat de chacun de ses points d'entrée."""
    out = dict(document)
    for key in ("methods", "functions"):
        items = document.get(key)
        if not isinstance(items, list):
            continue
        out[key] = [
            {**f, "entryPoint": link_point(f["entryPoint"], index)}
            if isinstance(f, dict) and isinstance(f.get("entryPoint"), dict)
            else f
            for f in items
        ]
    if isinstance(document.get("entryPoint"), dict):
        out["entryPoint"] = link_point(document["entryPoint"], index)
    return out


def _contracts(final: Mapping[str, str]) -> dict[str, dict[str, Any]]:
    """Les copies des contrats de la source, chargées (une copie illisible est
    ignorée : elle n'a rien à offrir à la comparaison)."""
    out: dict[str, dict[str, Any]] = {}
    for path in parse_contracts_index(final.get(CONTRACTS_INDEX)):
        text = final.get(f"{CONTRACTS_DIR}/{path}")
        if text is None:
            continue
        try:
            out[path] = load_document(text.encode("utf-8"))
        except OpenApiError:
            continue
    return out


def sync_contract_links(
    existing: Mapping[str, str], writes: list[OutputAction], deletes: list[str]
) -> list[OutputAction]:
    """`existing` : `raw/` avant la collecte ; `writes`/`deletes` : actions de la
    collecte. Rend les écritures complétées (ou corrigées) des `.extend.yaml`
    dont l'état au contrat change."""
    removed = set(deletes)
    final = {path: text for path, text in existing.items() if path not in removed}
    final.update({action.path: action.content for action in writes})
    contracts = _contracts(final)
    index = operation_index(contracts) if contracts else None

    updated: dict[str, str] = {}
    for path in sorted(final):
        if not is_extend_name(path):
            continue
        try:
            document = load_schema(final[path])
        except SchemaReadError:
            continue
        if not isinstance(document, dict):
            continue
        linked = link_document(document, index)
        if linked == document:
            continue
        try:
            updated[path] = dump_functions(linked)
        except ConventionError:
            continue  # `.extend.yaml` mal formé : signalé ailleurs, jamais réécrit ici

    written = {a.path for a in writes}
    out = [OutputAction(path=a.path, content=updated.get(a.path, a.content)) for a in writes]
    out += [OutputAction(path=p, content=t) for p, t in sorted(updated.items()) if p not in written]
    return out
