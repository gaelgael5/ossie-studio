"""Pour qu'un agent ne soit jamais perdu (epic « Primitives MCP — écriture commune par
genre », ticket c7838a43) : `list_write_kinds` liste les genres qu'on peut écrire,
`describe_standard` expose chaque standard, et toute erreur de format renvoie vers
l'un ou l'autre (`aide`).

Gael : « prévois une primitive qui permet de lister tous les types possibles en
écriture, et une méthode permettant d'exposer tous les standards [...]. Pour toutes
les primitives, à chaque erreur de syntaxe, il faut que l'exception propose à l'agent
de consommer la méthode d'exposition qui décrit le standard. »

- `list_write_kinds` est **généré à partir du registre** (`kinds.py`) et de la
  configuration (taille maximale) : ajouter un genre l'y fait apparaître.
- Les standards sont des fichiers versionnés avec le code, un par standard
  (`standards/<id>.md`, en anglais pour l'agent) ; leurs exemples (blocs
  « example ») sont vérifiés par les tests, en passant par le validateur du genre.
- Le renvoi `aide` se pose à un seul endroit : là où le serveur met en forme les
  refus des primitives (`aide_for`).
"""

from __future__ import annotations

import re
from functools import cache
from pathlib import Path
from typing import Any

from pydantic import Field

from ossie_studio.mcp_surface.branch import BranchParam
from ossie_studio.mcp_surface.core import PrimitiveContext, PrimitiveError
from ossie_studio.mcp_surface.kinds import KINDS

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


class ListWriteKindsArgs(BranchParam):
    pass


class DescribeStandardArgs(BranchParam):
    standard: str | None = Field(
        default=None,
        description=(
            "Id of a standard (e.g. 'carto-objet', 'jsonpath'); omit it to list the standards."
        ),
    )


def _limit(ctx: PrimitiveContext) -> int:
    return int(ctx.state.settings.document_max_bytes)


async def list_write_kinds(ctx: PrimitiveContext, args: ListWriteKindsArgs) -> dict[str, Any]:
    kinds = []
    for kind in KINDS:
        item: dict[str, Any] = {
            "kind": kind.name,
            "description": kind.description,
            "identity": [
                {"name": f.name, "description": f.description, "format": f.format}
                for f in kind.identity
            ],
            "path": {
                "rule": kind.path_rule,
                **({"root": kind.path_root} if kind.path_root else {}),
            },
            "standard": kind.standard,
            "syntax": kind.syntax,
            "identity_fields": list(kind.identity_paths),
            "frozen_fields": list(kind.frozen),
            "list_filters": list(kind.list_filters),
            "write_capability": kind.capability,
            "read_capability": kind.read_capability,
        }
        if kind.typed_by:
            item["schema"] = (
                f"The exact schema of the content depends on '{kind.typed_by}': "
                f"read_schema(kind='{kind.name}', {kind.typed_by}=...)."
            )
        kinds.append(item)
    return {
        "document_max_bytes": _limit(ctx),
        "rules": "describe_standard('writing-rules')",
        "kinds": kinds,
    }


async def describe_standard(ctx: PrimitiveContext, args: DescribeStandardArgs) -> dict[str, Any]:
    known = standards()
    wanted = (args.standard or "").strip()
    if not wanted:
        return {
            "document_max_bytes": _limit(ctx),
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
        "document_max_bytes": _limit(ctx),
        "markdown": found["markdown"],
    }


# --- Le renvoi « aide », pour toutes les primitives ------------------------------------------


def _standard(name: str) -> dict[str, str]:
    return {"primitive": "describe_standard", "standard": name}


_KINDS_AIDE = {"primitive": "list_write_kinds"}

# Une erreur de format, par son code.
_BY_CODE: dict[str, dict[str, str]] = {
    "write_kind_unknown": _KINDS_AIDE,
    "write_jsonpath_invalid": _standard("jsonpath"),
    "write_xpath_invalid": _standard("xpath"),
    "write_xpath_value_invalid": _standard("xpath"),
    "write_identity_frozen": _standard("writing-rules"),
    "write_path_forbidden": _standard("writing-rules"),
    "branch_unsupported": _standard("writing-rules"),
    "schema_path_unknown": _KINDS_AIDE,
    "carto_model_invalid": _standard("carto-modele"),
    "standard_unknown": {"primitive": "describe_standard"},
}
# Des arguments mal formés, par le domaine de la primitive : le standard de ce domaine.
_BY_DOMAIN: dict[str, dict[str, str]] = {
    "documents": _KINDS_AIDE,
    "cartographie": _standard("carto-objet"),
    "referentiels": _standard("referentiel-valeur"),
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
