"""Pour qu'un agent ne soit jamais perdu (epic « Primitives MCP — écriture commune par
type de document », ticket c7838a43) : `list_document_types` liste les types de document qu'on peut
écrire,
`describe_standard` expose chaque standard, et toute erreur de format renvoie vers
l'un ou l'autre (`aide`).

Gael : « prévois une primitive qui permet de lister tous les types possibles en
écriture, et une méthode permettant d'exposer tous les standards [...]. Pour toutes
les primitives, à chaque erreur de syntaxe, il faut que l'exception propose à l'agent
de consommer la méthode d'exposition qui décrit le standard. »

- `list_document_types` est **généré à partir du registre** (`document_types.py`) et de la
  configuration (taille maximale) : ajouter un type de document l'y fait apparaître.
- Les standards sont des fichiers versionnés avec le code, un par standard
  (`standards/<id>.md`, en anglais pour l'agent) ; leurs exemples (blocs
  « example ») sont vérifiés par les tests, en passant par le validateur du type de document.
- Le renvoi `aide` se pose à un seul endroit : là où le serveur met en forme les
  refus des primitives (`aide_for`).
"""

from __future__ import annotations

import re
from functools import cache
from pathlib import Path
from typing import Any

from pydantic import Field

from ossie_studio.app_settings.service import document_max_bytes
from ossie_studio.cartographie.catalog import catalog
from ossie_studio.mcp_surface.branch import BranchParam
from ossie_studio.mcp_surface.core import PrimitiveContext, PrimitiveError
from ossie_studio.mcp_surface.document_type_base import ListFilter
from ossie_studio.mcp_surface.document_types import DOCUMENT_TYPES

STANDARDS_DIR = Path(__file__).parent / "standards"


# --- Standards ----------------------------------------------------------------------------


@cache
def standards() -> dict[str, dict[str, str]]:
    """Les standards : identifiant (nom du fichier) → titre, résumé, texte."""
    out: dict[str, dict[str, str]] = {}
    for file in sorted(STANDARDS_DIR.glob("*.md")):
        text = file.read_text(encoding="utf-8")
        lines = text.splitlines()
        title = lines[0].removeprefix("# ").strip() if lines else file.stem
        summary = next((ln.strip() for ln in lines[1:] if ln.strip()), "")
        out[file.stem] = {"title": title, "summary": summary, "markdown": text}
    return out


_EXAMPLE = re.compile(r"```(yaml|json|xml) (example(?:-patch)?)\n(.*?)```", re.DOTALL)


def examples(standard: str) -> list[tuple[str, str]]:
    """Les blocs d'exemple d'un standard : (nature, texte). Nature « example » : une
    entrée de write_documents ou une liste d'entrées ; « example-patch » : un document
    et des opérations de patch_document."""
    text = standards()[standard]["markdown"]
    return [(m.group(2), m.group(3)) for m in _EXAMPLE.finditer(text)]


# --- Primitives -------------------------------------------------------------------------


class ListDocumentTypesArgs(BranchParam):
    pass


class DescribeStandardArgs(BranchParam):
    standard: str | None = Field(
        default=None,
        description=(
            "Id of a standard (e.g. 'carto-objet', 'jsonpath'); omit it to list the standards."
        ),
    )


def _filter(found: ListFilter) -> dict[str, Any]:
    out: dict[str, Any] = {
        "name": found.name,
        "description": found.description,
        "type": found.type,
    }
    if found.choices:
        out["choices"] = list(found.choices)
    return out


async def _limit(ctx: PrimitiveContext) -> int:
    """La taille maximale en vigueur : réglée sur la page « Paramètres », sinon celle
    de la configuration (ticket ae8fc4b6)."""
    return await document_max_bytes(ctx.conn, ctx.state.settings)


async def list_document_types(ctx: PrimitiveContext, args: ListDocumentTypesArgs) -> dict[str, Any]:
    document_types = []
    for doc_type in DOCUMENT_TYPES:
        item: dict[str, Any] = {
            "document_type": doc_type.name,
            "description": doc_type.description,
            "identity": [
                {"name": f.name, "description": f.description, "format": f.format}
                for f in doc_type.identity
            ],
            "path": {
                "rule": doc_type.path_rule,
                **({"root": doc_type.path_root} if doc_type.path_root else {}),
            },
            "standard": doc_type.standard,
            "syntax": doc_type.syntax,
            "identity_fields": list(doc_type.identity_paths),
            "frozen_fields": list(doc_type.frozen),
            "list_filters": [_filter(f) for f in doc_type.list_filters],
            "writable": doc_type.writable,
            "write_capability": doc_type.capability if doc_type.writable else None,
            "read_capability": doc_type.read_capability,
        }
        if not doc_type.writable:
            item["produced_by"] = list(doc_type.produced_by)
        if doc_type.typed_by:
            item["schema"] = (
                f"The exact schema of the content depends on '{doc_type.typed_by}': "
                f"read_schema(document_type='{doc_type.name}', {doc_type.typed_by}=...)."
            )
        document_types.append(item)
    return {
        "document_max_bytes": await _limit(ctx),
        "rules": "describe_standard('writing-rules')",
        "document_types": document_types,
    }


def _archimate_catalogue() -> dict[str, Any]:
    """Tous les types ArchiMate 3.2 sur lesquels baser un type de cartographie (ce que
    rendait `list_carto_types(catalogue=true)`)."""
    return {
        "archimate_catalogue": [
            {
                "archimate": t.name,
                "label": t.label,
                "layer": t.layer or None,
                "nature": "relation" if t.is_relation else "object",
            }
            for t in catalog().types.values()
        ]
    }


# Ce qu'un standard rend en plus de son texte, calculé depuis le code.
_EXTRAS = {"carto-type": _archimate_catalogue}


async def describe_standard(ctx: PrimitiveContext, args: DescribeStandardArgs) -> dict[str, Any]:
    known = standards()
    wanted = (args.standard or "").strip()
    if not wanted:
        return {
            "document_max_bytes": await _limit(ctx),
            "standards": [
                {"standard": k, "title": v["title"], "summary": v["summary"]}
                for k, v in known.items()
            ],
        }
    if wanted not in known:
        raise PrimitiveError("standard_unknown", standard=wanted, expected=list(known))
    found = known[wanted]
    return {
        "standard": wanted,
        "title": found["title"],
        "document_max_bytes": await _limit(ctx),
        "markdown": found["markdown"],
        **(_EXTRAS[wanted]() if wanted in _EXTRAS else {}),
    }


# --- Le renvoi « aide », pour toutes les primitives ------------------------------------------


def _standard(name: str) -> dict[str, str]:
    return {"primitive": "describe_standard", "standard": name}


_KINDS_AIDE = {"primitive": "list_document_types"}

# Une erreur de format, par son code.
_BY_CODE: dict[str, dict[str, str]] = {
    "document_type_unknown": _KINDS_AIDE,
    "write_jsonpath_invalid": _standard("jsonpath"),
    "write_xpath_invalid": _standard("xpath"),
    "write_xpath_value_invalid": _standard("xpath"),
    "write_identity_frozen": _standard("writing-rules"),
    "write_path_forbidden": _standard("writing-rules"),
    "branch_unsupported": _standard("writing-rules"),
    "schema_path_unknown": _KINDS_AIDE,
    "schema_designation_invalid": _KINDS_AIDE,
    "list_filter_unknown": _KINDS_AIDE,
    "list_filter_invalid": _KINDS_AIDE,
    "standard_unknown": {"primitive": "describe_standard"},
}
# Des arguments mal formés, par le domaine de la primitive : ce qui décrit ce domaine.
_BY_DOMAIN: dict[str, dict[str, str]] = {
    "documents": _KINDS_AIDE,
}


def aide_for(domain: str, code: str, detail: dict[str, Any]) -> dict[str, str] | None:
    """Le renvoi d'une erreur de format vers ce qui en décrit la règle ; None pour une
    erreur qui n'est pas de format (droits, introuvable, version…)."""
    if "aide" in detail:
        return None
    if code in _BY_CODE:
        return dict(_BY_CODE[code])
    if code == "invalid_arguments" and domain in _BY_DOMAIN:
        return dict(_BY_DOMAIN[domain])
    return None
