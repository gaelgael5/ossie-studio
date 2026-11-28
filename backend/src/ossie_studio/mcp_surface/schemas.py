"""Primitive commune de lecture des schémas : `read_schema` (ticket 06ef8679 ; désignation
par type de document, ticket 69be2ef9).

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

**Désignation par type de document** (epic « Primitives MCP — écriture commune par type de document
»,
ticket 69be2ef9 ; Gael : « un path optionnel, un type ») : le schéma du contenu d'un
type de document d'écriture se demande par le type de document et ce qui le type, sans chemin —
`read_schema(document_type='carto_objet', type='application')`,
`read_schema(document_type='referentiel_valeur', referentiel='pays')`. Le registre dit quel
champ type un type de document (`typed_by`) et quel document porte son schéma
(`schema_document_type`) ;
l'emplacement en est déduit, puis lu exactement comme par chemin (même réponse). Un
`path` donné avec un type de document suit la règle de ce type de document (interdit aujourd'hui).
Un type de document
dont le contenu ne dépend d'aucun type (modèle, processus, tag…) n'a pas de schéma
par type : son format est décrit par `describe_standard`. Le chemin reste l'entrée
des schémas qui ne sont pas des types de document d'écriture (raw, asset), et reste accepté
pour un référentiel ou un type de cartographie.

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

import json
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
from ossie_studio.cartographie.referentiel_links import (
    EXTEND_SUFFIX,
    ReferentielLink,
    parse_extend,
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
from ossie_studio.mcp_surface.document_reads import document_error, resolve_document_type
from ossie_studio.mcp_surface.document_type_base import DocumentError
from ossie_studio.mcp_surface.document_types import DocumentType, get_document_type
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
    document_type: str | None = Field(
        default=None,
        description=(
            "Kind of document whose content schema is wanted (list_document_types), with the "
            "field that types it: document_type='carto_objet' + type, "
            "document_type='referentiel_valeur' + "
            "referentiel. Omit it to read a raw or asset schema by path."
        ),
    )
    type: str | None = Field(
        default=None, description="With document_type='carto_objet': slug of the type of the map."
    )
    referentiel: str | None = Field(
        default=None,
        description="With document_type='referentiel_valeur': slug of the reference list.",
    )
    identity: dict[str, Any] | None = Field(
        default=None,
        description=(
            "With a document type whose document is itself a schema (schema_raw, schema_asset): "
            "its "
            "identity, e.g. {source: 'ventes', name: 'customer.schema.yaml'}."
        ),
    )
    path: str | None = Field(
        default=None,
        description=(
            "Path of the schema in the space, without document_type (prefer document_type). With "
            "document_type: never "
            "needed (refused for a document type whose path is fixed)."
        ),
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


# Les champs qui typent un type de document : nom du paramètre de read_schema.
_TYPING_FIELDS = ("type", "referentiel")


def _invalid(field: str, message: str, **extra: Any) -> PrimitiveError:
    return PrimitiveError("schema_designation_invalid", path=f"$.{field}", message=message, **extra)


def _typing_values(args: ReadSchemaArgs) -> dict[str, str | None]:
    return {name: getattr(args, name) for name in _TYPING_FIELDS}


def designate(args: ReadSchemaArgs) -> Target:
    """Le schéma désigné : par type de document (et ce qui le type), ou par chemin."""
    given = _typing_values(args)
    if not args.document_type:
        stray = [name for name, value in given.items() if value]
        if args.identity:
            stray.append("identity")
        if stray:
            raise _invalid(
                stray[0],
                f"« {stray[0]} » accompagne un type de document : donner aussi « document_type ».",
            )
        if not (args.path or "").strip():
            raise _invalid(
                "document_type",
                "Désigner le schéma par son type de document (document_type, avec type ou "
                "referentiel) ; le "
                "chemin (path) ne sert qu'aux schémas bruts et d'assets.",
            )
        return classify(args.path or "")
    return _by_document_type(resolve_document_type(args.document_type), args, given)


def _by_document_type(
    doc_type: DocumentType, args: ReadSchemaArgs, given: dict[str, str | None]
) -> Target:
    if doc_type.is_schema:
        # Le document est lui-même un schéma (raw, asset, ticket 56f3784a) : désigné
        # par son identité, lu comme par chemin.
        try:
            location = doc_type.locate(None, args.identity or {}, args.path)
        except DocumentError as exc:
            raise document_error(exc, doc_type.standard) from None
        assert location.path is not None
        return classify(location.path)
    if args.identity:
        raise _invalid(
            "identity",
            f"Le schéma d'un document « {doc_type.label} » ne se désigne pas par une identité.",
        )
    if doc_type.typed_by is None or doc_type.schema_document_type is None:
        raise PrimitiveError(
            "schema_document_type_untyped",
            path="$.document_type",
            document_type=doc_type.name,
            message=(
                f"Le contenu d'un document « {doc_type.label} » ne dépend d'aucun type : son "
                "format est décrit par son standard."
            ),
            aide={"primitive": "describe_standard", "standard": doc_type.standard},
        )
    value = given.pop(doc_type.typed_by)
    stray = [name for name, v in given.items() if v]
    if stray:
        raise _invalid(
            stray[0],
            f"Le schéma d'un document « {doc_type.label} » se désigne par « {doc_type.typed_by} » "
            f"seul.",
        )
    if not (value or "").strip():
        raise _invalid(
            doc_type.typed_by,
            f"read_schema(document_type='{doc_type.name}') demande « {doc_type.typed_by} ».",
        )
    holder = get_document_type(doc_type.schema_document_type)
    field = holder.identity[0]
    try:
        location = holder.locate(None, {field.name: value}, args.path)
    except DocumentError as exc:
        if exc.field != field.name:
            raise document_error(exc, doc_type.standard) from None
        raise _invalid(
            doc_type.typed_by,
            f"« {doc_type.typed_by} » invalide : {field.format} ; reçu « {value} ».",
            received=value,
        ) from None
    assert location.path is not None
    return classify(location.path)


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
        extras: dict[str, Any] = {
            "slug": ctype.slug,
            "name": ctype.title,
            "archimate": ctype.archimate,
            "layer": base.layer if base and not relation else None,
            "nature": "relation" if relation else "object",
        }
        schema = object_document_schema(ctype, base)
        links = _referentiel_links(file.with_name(f"{ctype.slug}{EXTEND_SUFFIX}"))
        if links:
            # Liens vers les référentiels (ticket 43478978) : une valeur absente du
            # référentiel est refusée à l'écriture.
            extras["referentiels"] = [link.payload() for link in links]
            props = schema["properties"]["proprietes"]["properties"]
            for link in links:
                for prop, target_prop in link.properties.items():
                    if isinstance(props.get(prop), dict):
                        props[prop] = {
                            **props[prop],
                            "x-referentiel": {
                                "referentiel": link.referentiel,
                                "propriete": target_prop,
                            },
                        }
        return schema, extras
    return raw, {}


def _referentiel_links(file: Path) -> list[ReferentielLink]:
    """Les liens du fichier d'extension d'un type ; illisible ou absent : aucun."""
    if not file.is_file():
        return []
    try:
        return parse_extend(file.read_text(encoding="utf-8"))
    except (CartographieError, UnicodeDecodeError):
        return []


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


def _code_schema(args: ReadSchemaArgs) -> dict[str, Any] | None:
    """Le schéma du contenu d'un type de document qui le définit dans le code (ticket b4aeae83 :
    un dataset, sa part Ossie 0.1.1 officielle et sa part extension), sinon None."""
    if not args.document_type:
        return None
    doc_type = resolve_document_type(args.document_type)
    if doc_type.content_schema is None:
        return None
    schema = doc_type.content_schema()
    text = json.dumps(schema, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return {
        "view": None,
        "path": None,
        "kind": "document",
        "version": git_blob_sha(text),
        "unpublished": False,
        "resolved": True,
        "schema": schema,
        "extras": {"document_type": doc_type.name, "standard": doc_type.standard},
    }


async def read_schema(ctx: PrimitiveContext, args: ReadSchemaArgs) -> dict[str, Any]:
    static = _code_schema(args)
    if static is not None:
        return static
    target = designate(args)
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
