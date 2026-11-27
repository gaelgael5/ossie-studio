"""Primitive commune de lecture des schémas : `read_schema` (ticket 06ef8679).

Gael : « Est-ce que pour la lecture des schémas il y a moyen de mutualiser ? »
Une seule primitive lit tout schéma de l'espace par son **chemin** et remplace
les lectures par domaine (`read_raw_schema`, `read_asset`,
`read_referentiel_model`) ; même esprit que la cartographie : « n'explosons pas
la liste des API ». Les listes restent par domaine et rendent le chemin à donner
ici.

La **nature** se déduit du chemin :

- `sources/<source>/raw/…<nom>.schema.yaml` → `raw` ;
- `sources/<source>/assets/…<nom>.schema.yaml` → `asset` ;
- `referentiels/<nom>/modele.schema.yaml` → `referentiel` ;
- `cartographie/types/<slug>.schema.yaml` → `carto_type`.

Tout autre chemin est refusé en le nommant (`schema_path_unknown`).

**Droits** : la primitive suppose `spaces:read` (elle est visible de qui lit un
espace) ; la capacité du domaine désigné par le chemin est vérifiée ensuite sur
l'espace visé, pour une connexion OAuth (la personne) — `sources:read`,
`curation:read`, `referentiels:read`, `cartographie:read` : l'agent n'en lit
jamais plus que la personne à l'écran. Une clé d'API n'a pas de personne : son
profil agent, qui lui donne `read_schema`, suffit.

**Schéma effectif** (`resolved`, vrai par défaut) — celui dont un agent a besoin :
- `raw` : le schéma collecté ; ses références sont déjà résolues à la collecte
  par le service de recherche de schéma commun (`sources/schema_resolve.py`) ;
- `asset` : le schéma fusionné avec son `.extend.yaml` (travail humain) ;
  `extras` porte l'enrichissement métier et le statut (ce que rendait
  `read_asset`) ;
- `referentiel` : le modèle, avec sa clé (`x-key`) ;
- `carto_type` : le schéma d'un objet du type tel que son fichier l'écrit (base
  ArchiMate + propriétés ajoutées) ; `extras` porte le type ArchiMate, la couche,
  la nature.
`resolved: false` : le fichier tel qu'il est écrit.

`version` : l'empreinte Git du fichier dans la branche lue (même calcul que la
publication et que la cartographie).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from ossie_studio.authz.catalog import SPACE_SCOPED
from ossie_studio.cartographie.catalog import catalog
from ossie_studio.cartographie.errors import CartographieError
from ossie_studio.cartographie.model import (
    TYPE_SUFFIX,
    TYPES_DIR,
    object_document_schema,
    parse_type,
)
from ossie_studio.drafts.store import git_blob_sha
from ossie_studio.mcp_surface.branch import (
    BranchArgs,
    BranchView,
    check_branch,
    open_branch,
    open_view,
)
from ossie_studio.mcp_surface.core import PrimitiveContext, PrimitiveError
from ossie_studio.mcp_surface.reads import asset_payload
from ossie_studio.parsers.conventions import SchemaReadError, load_schema
from ossie_studio.profiles.service import load_capabilities
from ossie_studio.referentiels.model import model_key
from ossie_studio.referentiels.repo import MODEL_FILE, REFERENTIELS_DIR
from ossie_studio.slugs import is_valid_slug
from ossie_studio.sources.asset_files import extend_name, is_extend_name, load_asset
from ossie_studio.sources.collect_core import RAW_DIR
from ossie_studio.sources.curation import is_schema_file
from ossie_studio.sources.paths import ASSETS_DIR, SOURCES_DIR, is_valid_source_slug
from ossie_studio.spaces.branch_service import db_get_base

Kind = Literal["raw", "asset", "referentiel", "carto_type"]

# Au-delà, un schéma n'est pas rendu d'un bloc (réponse trop lourde pour un agent).
MAX_SCHEMA_BYTES = 1_000_000

# La capacité de l'écran équivalent, pour chaque nature.
CAPABILITY: dict[Kind, str] = {
    "raw": "sources:read",
    "asset": "curation:read",
    "referentiel": "referentiels:read",
    "carto_type": "cartographie:read",
}

_SOURCE = re.compile(rf"^{SOURCES_DIR}/([^/]+)/({RAW_DIR}|{ASSETS_DIR})/(.+)$")
_REFERENTIEL = re.compile(rf"^{REFERENTIELS_DIR}/([^/]+)/{re.escape(MODEL_FILE)}$")
_CARTO_TYPE = re.compile(rf"^{TYPES_DIR}/([^/]+){re.escape(TYPE_SUFFIX)}$")


class ReadSchemaArgs(BranchArgs):
    path: str = Field(
        description=(
            "Path of the schema in the space, as the lists give it: 'schema_path' of "
            "list_raw_schemas and list_assets, 'path' of list_referentiels and "
            "list_carto_types."
        )
    )
    resolved: bool = Field(
        default=True,
        description=(
            "True (default): the effective schema (asset merged with its human work, "
            "cartography type with its ArchiMate base). False: the file as written."
        ),
    )


@dataclass(frozen=True)
class Target:
    """Ce que désigne un chemin : sa nature et ce qui l'identifie dans son domaine."""

    kind: Kind
    path: str
    # raw / asset : slug de la source et nom du schéma sous raw/ ou assets/ ;
    # referentiel : nom ; carto_type : slug du type.
    name: str
    source: str = ""


def classify(path: str) -> Target:
    """La nature d'un chemin, ou `schema_path_unknown` qui le nomme."""
    clean = path.strip().lstrip("/")
    unknown = PrimitiveError("schema_path_unknown", path=path)
    if ".." in clean.split("/"):
        raise unknown
    if m := _SOURCE.match(clean):
        source, folder, rest = m.groups()
        if not is_valid_source_slug(source) or not is_schema_file(rest.rsplit("/", 1)[-1]):
            raise unknown
        if folder == ASSETS_DIR:
            if is_extend_name(rest):
                raise unknown
            return Target("asset", clean, rest, source)
        return Target("raw", clean, rest, source)
    if m := _REFERENTIEL.match(clean):
        return Target("referentiel", clean, m.group(1))
    if (m := _CARTO_TYPE.match(clean)) and is_valid_slug(m.group(1)):
        return Target("carto_type", clean, m.group(1))
    raise unknown


async def _require(ctx: PrimitiveContext, args: ReadSchemaArgs, target: Target) -> None:
    """La personne derrière une connexion OAuth doit avoir la capacité du domaine
    sur l'espace visé ; une clé d'API n'a que son profil agent (déjà vérifié)."""
    if ctx.user is None:
        return
    capability = CAPABILITY[target.kind]
    space_id = args.space_id if capability in SPACE_SCOPED else None
    if capability not in await load_capabilities(ctx.conn, ctx.user, space_id):
        raise PrimitiveError(
            "primitive_forbidden",
            primitive="read_schema",
            capability=capability,
            path=target.path,
        )


@dataclass(frozen=True)
class _File:
    schema: Any
    version: str
    extras: dict[str, Any]


def _read_file(target: Target, resolved: bool) -> Callable[[Path], _File]:
    def read(root: Path) -> _File:
        file = root / target.path
        if not file.is_file():
            raise PrimitiveError("schema_not_found", path=target.path)
        data = file.read_bytes()
        if len(data) > MAX_SCHEMA_BYTES:
            raise PrimitiveError("schema_too_large", path=target.path)
        try:
            text = data.decode("utf-8")
            raw = load_schema(text)
            schema, extras = _effective(target, file, text, raw) if resolved else (raw, {})
        except (SchemaReadError, UnicodeDecodeError, CartographieError):
            raise PrimitiveError("schema_invalid", path=target.path) from None
        return _File(schema, git_blob_sha(data), extras)

    return read


def _effective(target: Target, file: Path, text: str, raw: Any) -> tuple[Any, dict[str, Any]]:
    if target.kind == "asset":
        return load_asset(file), {}
    if target.kind == "referentiel":
        return raw, {"name": target.name, "key": model_key(raw) if isinstance(raw, dict) else []}
    if target.kind == "carto_type":
        ctype = parse_type(target.name, text)
        base = catalog().get(ctype.archimate)
        relation = base is not None and base.is_relation
        extras = {
            "slug": ctype.slug,
            "name": ctype.title,
            "archimate": ctype.archimate,
            "layer": base.layer if base and not relation else None,
            "nature": "relation" if relation else "object",
        }
        return object_document_schema(ctype, base), extras
    return raw, {}


def _unpublished(bv: BranchView, target: Target) -> bool:
    if target.kind == "asset":
        # Un asset est non publié si son schéma ou son `.extend.yaml` l'est.
        folder, _, name = target.path.rpartition("/")
        return bv.unpublished(target.path) or bv.unpublished(f"{folder}/{extend_name(name)}")
    return bv.unpublished(target.path)


async def _open(ctx: PrimitiveContext, args: ReadSchemaArgs, target: Target) -> BranchView:
    """Les référentiels vivent sur la branche racine de l'espace : on les y lit,
    quelle que soit la branche donnée (comme les primitives des référentiels)."""
    if target.kind != "referentiel":
        return await open_branch(ctx, args)
    base = await db_get_base(ctx.conn, args.space_id)
    if base is None:
        raise PrimitiveError("schema_not_found", path=target.path)
    return await open_view(ctx, args.space_id, base.id, with_draft=check_branch(args.branch))


async def read_schema(ctx: PrimitiveContext, args: ReadSchemaArgs) -> dict[str, Any]:
    target = classify(args.path)
    await _require(ctx, args, target)
    bv = await _open(ctx, args, target)
    found = await bv.read(_read_file(target, args.resolved))
    extras = found.extras
    if target.kind == "asset" and args.resolved:
        # L'enrichissement métier et le statut de l'asset : ce que rendait read_asset.
        payload = await asset_payload(bv, target.source, target.name)
        extras = {k: v for k, v in payload.items() if k not in ("view", "unpublished")}
    elif target.kind == "raw":
        extras = {"source": target.source, "name": target.name}
    return {
        "view": bv.view,
        "path": target.path,
        "kind": target.kind,
        "version": found.version,
        "unpublished": _unpublished(bv, target),
        "resolved": args.resolved,
        "schema": found.schema,
        "extras": extras,
    }
