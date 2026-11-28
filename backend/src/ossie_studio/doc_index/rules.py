"""Les règles de validation par type de document (ticket d7139b17), appliquées par les
passes de la file (`passes.py`).

Gael : « La validation dépend en premier du type de document. On valide le document,
les références et les règles entre fichiers. » Chaque type de document déclare ses
règles dans le registre (`mcp_surface/document_types.py`, `RuleDeclaration` : nom,
portée, niveau). Ce module les applique :

- **document seul** (`document_findings`) : le fichier lisible (`parseable`), la
  description d'un dataset (`dataset_description`) ; résultat réutilisé pour la même
  empreinte (et la même version des règles, `RULES_VERSION`) ;
- **références** (`reference_findings`) : chaque identité citée (références sortantes
  de l'index) existe dans la vue ;
- **entre fichiers** : les règles de la publication d'avant, lancées sur la vue
  (`drafts.validation.run_rules` : manifestes, `$ref` des schémas, assets orphelins,
  gouvernance, domaines), plus la cartographie (`CartoRule`) et les référentiels
  (`ReferentielRule`), rangées par type de document.

**Niveau** (`level_for`) : le niveau déclaré pour le type du fichier est le plus haut
que la règle peut donner. Un fichier raw (`sources/<source>/raw/…`) n'a jamais de
problème bloquant, qu'il ait un type ou non (« c'est juste de la récupération »). Un
fichier que le registre ne connaît pas (déclaration d'import, fichier d'extension d'un
type…) garde le niveau de la règle.

**Règle d'or** (côté publication, `drafts/preparation.py`) : un problème bloquant
bloque, qu'il soit dans le brouillon ou non, qu'il ait existé avant ou non.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from functools import cache
from pathlib import Path
from typing import Any

from ossie_studio.cartographie.space import read_cartographie
from ossie_studio.doc_index.extract import ANY_TYPE
from ossie_studio.doc_index.store import IndexedRow
from ossie_studio.drafts.validation import (
    VALIDATION_RULES,
    Finding,
    Level,
    ParseableRule,
    _digest,
)
from ossie_studio.parsers.conventions import SchemaReadError, load_schema
from ossie_studio.referentiels.errors import ReferentielError
from ossie_studio.referentiels.model import check_model
from ossie_studio.referentiels.repo import (
    list_referentiels,
    model_path,
    read_referentiel,
    values_path,
)
from ossie_studio.referentiels.values import check_values

# Version des règles du document seul : la changer revalide tous les contenus.
RULES_VERSION = 2

ERROR: Level = "error"
WARNING: Level = "warning"
RAW_PATH = re.compile(r"^sources/[^/]+/raw/")

Problem = dict[str, Any]


@cache
def _declarations() -> dict[str, dict[str, str]]:
    """Type de document → règle → niveau déclaré (lu une fois dans le registre)."""
    # Import local : le registre importe les services de tous les domaines.
    from ossie_studio.mcp_surface.document_types import DOCUMENT_TYPES

    out: dict[str, dict[str, str]] = {}
    for doc_type in DOCUMENT_TYPES:
        out[doc_type.name] = {d.rule: d.level for d in doc_type.validation}
    return out


def _types(documents: list[dict[str, Any]]) -> set[str]:
    return {str(d.get("document_type")) for d in documents if d.get("document_type")}


def declared(rule: str, documents: list[dict[str, Any]]) -> bool:
    """Un des types du fichier déclare cette règle."""
    decls = _declarations()
    return any(rule in decls.get(t, {}) for t in _types(documents))


def level_for(rule: str, level: str, path: str, documents: list[dict[str, Any]]) -> str:
    """Le niveau d'un constat de `rule` (de niveau propre `level`) sur ce fichier."""
    if RAW_PATH.match(path):
        return WARNING
    decls = _declarations()
    levels = [decls[t][rule] for t in _types(documents) if rule in decls.get(t, {})]
    if levels and ERROR not in levels:
        return WARNING
    return level


def problem(finding: Finding, path: str, documents: list[dict[str, Any]]) -> Problem:
    """Un constat tel que l'index le range : niveau selon le type de document, règle,
    code, arguments, et le document en cause (pour un lien vers lui)."""
    first = documents[0] if documents else {}
    return {
        "level": level_for(finding.rule, finding.level, path, documents),
        "rule": finding.rule,
        "code": finding.message_code,
        "args": dict(finding.args),
        "document_type": first.get("document_type"),
        "identity": first.get("identity"),
    }


# --- Le document seul ------------------------------------------------------------------------

_PARSEABLE = ParseableRule()


def _dataset_description(path: str, data: bytes) -> list[Finding]:
    """Gael : « sur un dataset, une description qui manque, c'est problématique »."""
    try:
        doc = load_schema(data)
    except (SchemaReadError, UnicodeDecodeError, ValueError):
        return []
    out = []
    models = doc.get("semantic_model") if isinstance(doc, dict) else None
    for model in models if isinstance(models, list) else []:
        datasets = model.get("datasets") if isinstance(model, dict) else None
        for item in datasets if isinstance(datasets, list) else []:
            if not isinstance(item, dict) or not item.get("name"):
                continue
            if not str(item.get("description") or "").strip():
                digest = hashlib.sha256(data).hexdigest()
                out.append(
                    Finding(ERROR, path, "dataset_description", "dataset_description_missing",
                            {"dataset": str(item["name"])}, digest)
                )  # fmt: skip
    return out


def document_findings(path: str, data: bytes, documents: list[dict[str, Any]]) -> list[Finding]:
    """Les constats du document seul (fonction du chemin et du contenu)."""
    out: list[Finding] = []
    readable = _PARSEABLE.check_file(path, data)
    if readable is not None:
        out.append(readable)
    if declared("dataset_description", documents):
        out += _dataset_description(path, data)
    return out


# --- Les références --------------------------------------------------------------------------


def _shown(ref: str) -> tuple[str, str]:
    """(espèce, identité lisible) d'une référence de l'index."""
    kind, _sep, ident = ref.partition(":")
    if kind == "carto_objet" and ident.startswith(f"{ANY_TYPE}/"):
        ident = ident[len(ANY_TYPE) + 1 :]
    return kind, ident


def reference_findings(files: Mapping[str, IndexedRow]) -> list[Finding]:
    """Chaque identité citée par un fichier dont le type déclare `references` existe
    dans la vue (parmi les clés de ses fichiers)."""
    keys: set[str] = set()
    for row in files.values():
        keys.update(row.keys)
    out = []
    for path, row in sorted(files.items()):
        if not declared("references", row.documents):
            continue
        for ref in row.refs:
            if ref in keys:
                continue
            kind, ident = _shown(ref)
            out.append(
                Finding(ERROR, path, "references", "reference_unknown",
                        {"kind": kind, "ref": ident}, row.blob)
            )  # fmt: skip
    return out


# --- Entre fichiers : cartographie et référentiels ----------------------------------------------

# Ce que la cartographie signale sans bloquer : une valeur absente du référentiel lié
# (ticket 43478978 : « seulement signalé »), un lien cassé vers un référentiel.
_CARTO_WARNINGS = ("property_referentiel_unknown", "type_extend_orphan")
_CARTO_WARNING_PREFIXES = ("referentiel_link_",)
# Une extrémité introuvable est déjà une référence cassée (`references`).
_CARTO_SKIPPED = ("relation_endpoint_unknown",)


class CartoRule:
    """Le validateur de la cartographie (types, objets, relations) : schéma du type,
    relation permise par les règles effectives du type source, clés en double…"""

    name = "carto"

    def check(self, view: Path) -> list[Finding]:
        space = read_cartographie(view)
        out = []
        for p in space.problems:
            if p.code in _CARTO_SKIPPED:
                continue
            warning = p.code in _CARTO_WARNINGS or p.code.startswith(_CARTO_WARNING_PREFIXES)
            args = {k: v for k, v in (("detail", p.detail), ("field", p.field)) if v}
            out.append(
                Finding(WARNING if warning else ERROR, p.path, self.name, p.code, args,
                        _digest(view / p.path))
            )  # fmt: skip
        return out


class ReferentielRule:
    """Le validateur des référentiels : le modèle, puis les valeurs contre lui."""

    name = "referentiel"

    def check(self, view: Path) -> list[Finding]:
        out: list[Finding] = []
        for name in list_referentiels(view):
            model_rel, values_rel = model_path(name), values_path(name)
            try:
                ref = read_referentiel(view, name)
            except ReferentielError as exc:
                out.append(Finding(ERROR, model_rel, self.name, exc.code, {"referentiel": name},
                                   _digest(view / model_rel)))  # fmt: skip
                continue
            model_problems = check_model(ref.model)
            for p in model_problems:
                out.append(Finding(ERROR, model_rel, self.name, p.code,
                                   {"where": p.path, "detail": p.detail},
                                   _digest(view / model_rel)))  # fmt: skip
            if model_problems:
                continue
            for p in check_values(ref.model, ref.values):
                out.append(Finding(ERROR, values_rel, self.name, p.code,
                                   {"where": p.path, "detail": p.detail},
                                   _digest(view / values_rel)))  # fmt: skip
        return out


def register() -> None:
    """Ajoute les règles de la cartographie et des référentiels aux règles lancées
    sur la vue (idempotent)."""
    for rule in (CartoRule(), ReferentielRule()):
        if not any(r.name == rule.name for r in VALIDATION_RULES):
            VALIDATION_RULES.append(rule)


def view_rules() -> list[Any]:
    """Les règles lancées sur la vue entière ; « fichier lisible » se vérifie fichier
    par fichier (document seul)."""
    register()
    return [r for r in VALIDATION_RULES if r.name != _PARSEABLE.name]
