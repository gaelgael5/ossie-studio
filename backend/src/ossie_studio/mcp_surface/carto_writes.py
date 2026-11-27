"""Primitives MCP d'écriture de la cartographie (ticket d4723be3).

Un agent qui dépouille un atelier écrit la cartographie avec les mêmes contrôles
que l'écran. Gael : « Une pour écrire tout le modèle et une qui, par un
JSONPath, permet de modifier. » — et pas plus :

- `write_carto_documents` : une **liste** de documents (objets et relations),
  appliquée **en bloc** : tout ou rien, un seul enregistrement dans le brouillon.
  Document absent → création ; présent → remplacement complet, `version`
  obligatoire ; `delete: true` → suppression, `version` obligatoire.
- `modify_carto_document` : des opérations par JSONPath sur un document.

Règles communes (epic « Primitives MCP d'écriture ») : réservées à un agent
connecté pour une personne (`person_only`), dans son **brouillon** (jamais
publié), capacité de l'écran (`cartographie:write`). `branch` absent ou `draft` ;
toute autre valeur est refusée en amont (`branch_unsupported`).

**Aucune règle de la cartographie n'est réécrite ici** : chaque document passe par
le service (`space.save_object`, `diagram.delete_object`) — schéma du type, clé
figée et unique, relation permise, suppression refusée tant qu'une relation ou un
modèle utilise l'objet. Cette couche ne fait que : vérifier la version
(concurrence), faire correspondre chemin, type et clé, ordonner la liste, et
**traduire** les refus du service en erreurs explicites.

**Erreurs** (Gael : « À chaque fois le JSONPath où se trouve l'erreur et la
description ») : au moindre refus, rien n'est écrit et **toutes** les erreurs sont
rendues (`carto_write_refused`, `written: false`, `errors`). Chaque erreur porte
le JSONPath de la donnée fautive **dans la requête** (`$.documents[2].content…`,
ou pour une modification le chemin dans le document, `$.operations[i]` si
l'opération est mal formée), un code (ceux de l'écran, préfixés `carto_`), un
message en français avec la valeur reçue et ce qui était attendu.

Pas de mode « simuler » : l'écriture étant tout ou rien, un envoi refusé ne
laisse aucune trace.

**Ordre dans la liste** : une relation peut viser un objet créé plus loin, et une
suppression d'objet accompagner celle des relations qui l'utilisent. Plutôt que
d'imposer un ordre à l'agent, on rejoue les documents refusés tant qu'un passage
en fait aboutir au moins un : la liste est validée comme un tout, et ce qui reste
refusé l'est pour une vraie raison.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ossie_studio.cartographie.catalog import catalog
from ossie_studio.cartographie.diagram import delete_object
from ossie_studio.cartographie.errors import CartographieError, Problem
from ossie_studio.cartographie.model import (
    CartoObject,
    object_location,
    object_path,
    properties_schema,
)
from ossie_studio.cartographie.space import Cartographie, read_cartographie, save_object
from ossie_studio.drafts.store import git_blob_sha
from ossie_studio.mcp_surface.branch import BranchArgs, BranchView, open_branch
from ossie_studio.mcp_surface.core import PrimitiveContext, PrimitiveError, agent_name
from ossie_studio.parsers.conventions import SchemaReadError, load_schema
from ossie_studio.spaces.workdir import WorkdirError

MAX_DOCUMENTS = 500
# Champs d'un document de cartographie (format du fichier), dans l'ordre d'écriture.
CONTENT_KEYS = ("cle", "type", "source", "cible", "nom", "documentation", "proprietes")
# Ce qu'aucune modification ne change : identité d'un objet, extrémités d'une relation.
FROZEN = ("cle", "type", "source", "cible")

# --- Arguments --------------------------------------------------------------------------


class CartoDocumentArg(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str | None = Field(
        default=None,
        description=(
            "Path of the document: 'cartographie/objets/<type>/<key in lowercase>.yaml'. "
            "Or give type and key."
        ),
    )
    type: str | None = Field(default=None, description="Type of the space (slug), with key.")
    key: str | None = Field(default=None, description="Functional key, with type.")
    version: str | None = Field(
        default=None,
        description=(
            "Version of the document as get_carto_object gives it: required to replace or "
            "delete an existing document, absent to create one."
        ),
    )
    content: dict[str, Any] | None = Field(
        default=None,
        description=(
            "The complete document, as its file writes it: cle, type, nom, documentation "
            "(markdown), proprietes (ArchiMate attributes as ArchiMate values, e.g. "
            "acces: Write, and added properties); source and cible (keys) for a relationship. "
            "Read its schema with read_schema on the type."
        ),
    )
    delete: bool = Field(default=False, description="True to delete the document (destructive).")


class WriteCartoArgs(BranchArgs):
    message: str = Field(
        min_length=1, max_length=200, description="What this change is, for the draft history."
    )
    documents: list[CartoDocumentArg] = Field(
        min_length=1,
        max_length=MAX_DOCUMENTS,
        description="Documents to create, replace or delete, applied all together or not at all.",
    )


class CartoOperationArg(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(
        description="JSONPath in the document, e.g. '$.nom' or '$.proprietes.volume_jour'."
    )
    value: Any = Field(default=None, description="New value (when delete is false).")
    delete: bool = Field(default=False, description="True to remove the value at path.")


class ModifyCartoArgs(BranchArgs):
    path: str = Field(description="Path of the document (as the lists give it).")
    version: str = Field(description="Version of the document, from get_carto_object.")
    operations: list[CartoOperationArg] = Field(min_length=1, max_length=200)
    message: str | None = Field(
        default=None, max_length=200, description="What this change is, for the draft history."
    )


# --- Erreurs explicites -----------------------------------------------------------------


@dataclass
class Refusal:
    path: str  # JSONPath dans la requête
    code: str
    message: str
    expected: Any = None
    received: Any = None
    extra: dict[str, Any] = field(default_factory=dict)

    def payload(self) -> dict[str, Any]:
        out: dict[str, Any] = {"path": self.path, "code": self.code, "message": self.message}
        if self.expected is not None:
            out["expected"] = self.expected
        if self.received is not None:
            out["received"] = self.received
        return {**out, **self.extra}


class _Refused(Exception):
    def __init__(self, refusals: list[Refusal]) -> None:
        super().__init__("refused")
        self.refusals = refusals


def _show(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _describe(schema: Any) -> str:
    """Ce qu'attend un schéma de propriété, en français (pour le message)."""
    if not isinstance(schema, dict):
        return "conforme au schéma du type"
    if "enum" in schema:
        return "l'une des valeurs " + ", ".join(_show(v) for v in schema["enum"])
    if "const" in schema:
        return f"égal à {_show(schema['const'])}"
    kind = schema.get("type")
    words = {
        "integer": "un entier",
        "number": "un nombre",
        "string": "un texte",
        "boolean": "un booléen (true ou false)",
        "array": "une liste",
        "object": "un objet",
    }
    parts = [words.get(str(kind), "une valeur")]
    bounds = (
        ("minimum", "supérieur ou égal à"),
        ("maximum", "inférieur ou égal à"),
        ("exclusiveMinimum", "strictement supérieur à"),
        ("exclusiveMaximum", "strictement inférieur à"),
        ("minLength", "d'au moins … caractères"),
        ("maxLength", "d'au plus … caractères"),
        ("pattern", "de la forme"),
        ("format", "au format"),
    )
    for keyword, text in bounds:
        if keyword in schema:
            value = schema[keyword]
            parts.append(text.replace("…", str(value)) if "…" in text else f"{text} {_show(value)}")
    return " ".join(parts)


def _segments(dotted: str) -> list[str]:
    return [s for s in re.split(r"[/.]", dotted) if s]


def _jsonpath(prefix: str, *segments: str) -> str:
    out = prefix
    for seg in segments:
        out += f"[{seg}]" if seg.isdigit() else f".{seg}"
    return out


def _get(doc: Any, segments: list[str]) -> Any:
    for seg in segments:
        if isinstance(doc, dict):
            doc = doc.get(seg)
        elif isinstance(doc, list) and seg.isdigit() and int(seg) < len(doc):
            doc = doc[int(seg)]
        else:
            return None
    return doc


def _translate(
    exc: CartographieError,
    prefix: str,
    doc_path: str,
    content: dict[str, Any] | None,
    space: Cartographie,
) -> list[Refusal]:
    """Les refus du service, en erreurs localisées dans la requête. `prefix` : le
    JSONPath du document dans la requête ; `doc_path` : celui de l'entrée qui le
    désigne (pour une erreur qui porte sur le document entier)."""
    content = content or {}
    code = f"carto_{exc.code}"
    if exc.code != "object_invalid":
        return [_whole(exc, code, prefix, doc_path, content)]
    return [_problem(p, prefix, content, space) for p in exc.problems]


def _whole(
    exc: CartographieError, code: str, prefix: str, doc_path: str, content: dict[str, Any]
) -> Refusal:
    key = exc.detail.get("key", content.get("cle"))
    if exc.code == "object_key_taken":
        return Refusal(
            _jsonpath(prefix, "cle"),
            code,
            f"La clé {key} est déjà prise dans l'espace ; une clé est unique, tous types "
            "confondus. Pour remplacer cet objet, donner sa version.",
            received=key,
        )
    if exc.code == "object_type_frozen":
        return Refusal(
            _jsonpath(prefix, "type"),
            code,
            f"Le type de l'objet {key} est figé ; reçu {_show(content.get('type'))}.",
            received=content.get("type"),
        )
    if exc.code == "object_in_use":
        found = exc.detail.get("relations")
        relations = [str(r) for r in found] if isinstance(found, list) else []
        return Refusal(
            doc_path,
            code,
            f"L'objet {key} est utilisé par les relations {', '.join(relations)} : les "
            "supprimer dans la même liste, ou les garder et garder l'objet.",
            extra={"relations": relations},
        )
    if exc.code == "object_in_model":
        return Refusal(
            doc_path,
            code,
            f"L'objet {key} est retenu par les modèles {exc.detail.get('models')} : le "
            "retirer de ces modèles avant de le supprimer.",
            extra={"models": exc.detail.get("slugs", [])},
        )
    if exc.code == "object_not_found":
        return Refusal(doc_path, code, f"Aucun objet de clé {key} dans le brouillon.")
    if exc.code == "object_keys_duplicate":
        return Refusal(
            doc_path,
            code,
            "Des clés sont en double dans l'espace : aucune écriture n'est acceptée tant "
            "qu'elles ne sont pas corrigées.",
            extra={"files": sorted({p.path for p in exc.problems})},
        )
    return Refusal(doc_path, code, f"Refus de la cartographie : {exc.code}.", extra=exc.detail)


def _problem(p: Problem, prefix: str, content: dict[str, Any], space: Cartographie) -> Refusal:
    code = "carto_object_invalid"
    if p.code.startswith("property_"):
        segs = _segments(p.field)
        where = _jsonpath(prefix, "proprietes", *segs)
        received = _get(content.get("proprietes"), segs)
        name = ".".join(segs) or "proprietes"
        if p.code == "property_unknown":
            return Refusal(
                where,
                code,
                f"La propriété {name} n'existe pas dans le schéma du type "
                f"{content.get('type')} (lire le schéma avec read_schema).",
                received=received,
            )
        if p.code == "property_required":
            return Refusal(where, code, f"La propriété {name} est obligatoire.")
        expected = _property_schema(space, content.get("type"), segs)
        return Refusal(
            where,
            code,
            f"{name} doit être {_describe(expected)} ; reçu {_show(received)}.",
            expected=expected,
            received=received,
        )
    if p.code == "relation_not_allowed":
        source, target = content.get("source"), content.get("cible")
        allowed = _allowed(space, source, target)
        names = ", ".join(f"{label} ({arch})" for arch, label in allowed) or "aucune"
        return Refusal(
            _jsonpath(prefix, "type"),
            "relation_not_allowed",
            f"ArchiMate ne permet pas une relation de type {content.get('type')} de "
            f"{source} vers {target} ({p.detail}). Relations permises : {names}.",
            expected=[arch for arch, _label in allowed],
            received=content.get("type"),
        )
    fields = {
        "object_key_invalid": (
            "cle",
            "La clé doit être en majuscules, chiffres et tirets (ex. APP-BOUTIQUE)",
        ),
        "object_name_missing": ("nom", "Le nom est obligatoire"),
        "object_type_unknown": ("type", "Ce type n'existe pas dans l'espace"),
        "relation_endpoint_missing": ("source", "Une relation a une source et une cible"),
        "object_not_relation": ("source", "Ce type n'est pas une relation : ni source ni cible"),
    }
    if p.code == "relation_endpoint_unknown":
        end = "source" if p.field == "source" else "cible"
        value = content.get(end)
        return Refusal(
            _jsonpath(prefix, end),
            code,
            f"La {end} {value} n'existe ni dans l'espace ni dans la liste envoyée.",
            received=value,
        )
    name, text = fields.get(p.code, ("", f"Refus : {p.code}"))
    if p.code == "object_type_unknown":
        code = "carto_type_not_found"
    received = content.get(name) if name else None
    where = _jsonpath(prefix, name) if name else prefix
    suffix = f" ; reçu {_show(received)}." if received is not None else "."
    return Refusal(where, code, text + suffix, received=received)


def _property_schema(space: Cartographie, type_slug: Any, segs: list[str]) -> Any:
    ctype = space.types.get(str(type_slug))
    if ctype is None or not segs:
        return None
    schema: Any = properties_schema(ctype, catalog().get(ctype.archimate))
    for seg in segs:
        if not isinstance(schema, dict):
            return None
        if seg.isdigit():
            schema = schema.get("items")
        else:
            schema = (schema.get("properties") or {}).get(seg)
    return schema


def _allowed(space: Cartographie, source: Any, target: Any) -> list[tuple[str, str]]:
    """Les relations permises entre les deux extrémités, par la règle du service."""
    try:
        names = space.allowed(str(source), str(target))
    except CartographieError:
        return []
    cat = catalog()
    return [(n, (cat.get(n).label if cat.get(n) else n)) for n in names]  # type: ignore[union-attr]


# --- Lire un contenu --------------------------------------------------------------------


def _to_object(content: dict[str, Any], prefix: str) -> tuple[CartoObject | None, list[Refusal]]:
    """Le document envoyé, en objet du service. Seule la forme est vérifiée ici
    (champs connus, textes) ; le fond est l'affaire du service."""
    refusals = [
        Refusal(
            _jsonpath(prefix, str(k)),
            "carto_object_invalid",
            f"Champ inconnu {_show(k)} ; un document porte : {', '.join(CONTENT_KEYS)}.",
            received=content[k],
        )
        for k in content
        if k not in CONTENT_KEYS
    ]
    for name in ("cle", "type", "nom", "documentation", "source", "cible"):
        value = content.get(name)
        if value is not None and not isinstance(value, str):
            refusals.append(
                Refusal(
                    _jsonpath(prefix, name),
                    "carto_object_invalid",
                    f"{name} doit être un texte ; reçu {_show(value)}.",
                    expected={"type": "string"},
                    received=value,
                )
            )
    properties = content.get("proprietes")
    if properties is not None and not isinstance(properties, dict):
        refusals.append(
            Refusal(
                _jsonpath(prefix, "proprietes"),
                "carto_object_invalid",
                f"proprietes doit être un objet ; reçu {_show(properties)}.",
                expected={"type": "object"},
                received=properties,
            )
        )
    if refusals:
        return None, refusals
    source, target = content.get("source"), content.get("cible")
    return (
        CartoObject(
            key=str(content.get("cle") or "").strip(),
            type=str(content.get("type") or "").strip(),
            name=str(content.get("nom") or "").strip(),
            documentation=str(content.get("documentation") or ""),
            properties=dict(properties or {}),
            source=source,
            target=target,
        ),
        [],
    )


def _version(root: Path, rel: str) -> str | None:
    file = root / rel
    return git_blob_sha(file.read_bytes()) if file.is_file() else None


def _stale(where: str, given: str | None, current: str) -> Refusal:
    if not given:
        return Refusal(
            where,
            "carto_version_stale",
            "Ce document existe déjà : donner sa version (get_carto_object) pour le remplacer "
            "ou le supprimer.",
            extra={"current_version": current},
        )
    return Refusal(
        where,
        "carto_version_stale",
        f"La version {given} n'est plus la version actuelle du document ({current}) : il a "
        "changé depuis la lecture. Le relire avant de l'écrire.",
        expected=current,
        received=given,
        extra={"current_version": current},
    )


# --- Écrire une liste -------------------------------------------------------------------


@dataclass
class _Step:
    index: int
    path: str  # chemin du document dans l'espace
    content: dict[str, Any] | None
    obj: CartoObject | None  # None : suppression
    create: bool
    key: str


def _plan(root: Path, space: Cartographie, args: WriteCartoArgs) -> list[_Step]:
    """Chaque entrée de la liste en geste à jouer ; les incohérences de forme, de
    chemin et de version sont rendues toutes ensemble."""
    refusals: list[Refusal] = []
    steps: list[_Step] = []
    seen: dict[str, int] = {}
    for i, doc in enumerate(args.documents):
        base = f"$.documents[{i}]"
        prefix = f"{base}.content"
        if doc.delete:
            step = _plan_delete(root, space, doc, i, base, refusals)
        else:
            step = _plan_write(root, space, doc, i, base, prefix, refusals)
        if step is None:
            continue
        if step.path in seen:
            refusals.append(
                Refusal(
                    base,
                    "carto_object_keys_duplicate",
                    f"Ce document ({step.path}) apparaît déjà en $.documents[{seen[step.path]}] ;"
                    " un document ne figure qu'une fois dans la liste.",
                )
            )
            continue
        seen[step.path] = i
        steps.append(step)
    if refusals:
        raise _Refused(refusals)
    return steps


def _designated(
    doc: CartoDocumentArg, base: str, refusals: list[Refusal]
) -> tuple[str | None, str | None, str | None]:
    """(chemin, type, clé) désignés par `path` ou par `type` + `key`."""
    if doc.path:
        clean = doc.path.strip().lstrip("/")
        location = object_location(clean)
        if location is None:
            refusals.append(
                Refusal(
                    f"{base}.path",
                    "carto_path_mismatch",
                    "Un document de cartographie est rangé sous "
                    "'cartographie/objets/<type>/<clé en minuscules>.yaml'.",
                    received=doc.path,
                )
            )
            return None, None, None
        return clean, location[0], None
    if doc.type and doc.key:
        return object_path(doc.type, doc.key), doc.type, doc.key
    return None, None, None


def _plan_delete(
    root: Path,
    space: Cartographie,
    doc: CartoDocumentArg,
    i: int,
    base: str,
    refusals: list[Refusal],
) -> _Step | None:
    path, _type, _key = _designated(doc, base, refusals)
    if path is None:
        if not refusals or not refusals[-1].path.startswith(base):
            refusals.append(
                Refusal(base, "carto_path_mismatch", "Désigner le document : path, ou type et key.")
            )
        return None
    if doc.content is not None:
        refusals.append(
            Refusal(
                f"{base}.content",
                "carto_object_invalid",
                "Une suppression ne porte pas de contenu : seulement le chemin et la version.",
            )
        )
        return None
    found = next(((k, o) for k, (p, o) in space.objects.items() if p == path), None)
    if found is None:
        refusals.append(
            Refusal(f"{base}.path", "carto_object_not_found", f"Aucun document {path}.")
        )
        return None
    current = _version(root, path) or ""
    if doc.version != current:
        refusals.append(_stale(f"{base}.version", doc.version, current))
        return None
    return _Step(i, path, None, None, False, found[0])


def _plan_write(
    root: Path,
    space: Cartographie,
    doc: CartoDocumentArg,
    i: int,
    base: str,
    prefix: str,
    refusals: list[Refusal],
) -> _Step | None:
    if doc.content is None:
        refusals.append(
            Refusal(
                f"{base}.content",
                "carto_object_invalid",
                "Le contenu du document est obligatoire (ou delete: true pour le supprimer).",
            )
        )
        return None
    obj, problems = _to_object(doc.content, prefix)
    if obj is None:
        refusals.extend(problems)
        return None
    path, type_slug, key = _designated(doc, base, refusals)
    expected = object_path(obj.type, obj.key) if obj.type and obj.key else None
    if (doc.path and path is None) or (path is not None and expected and path != expected):
        if path is not None:
            refusals.append(
                Refusal(
                    f"{base}.path" if doc.path else f"{base}.key",
                    "carto_path_mismatch",
                    f"Le chemin ne correspond pas au type et à la clé du contenu : attendu "
                    f"{expected}.",
                    expected=expected,
                    received=doc.path or path,
                )
            )
        return None
    if type_slug and obj.type and type_slug != obj.type and not doc.path:
        refusals.append(
            Refusal(
                f"{base}.type",
                "carto_path_mismatch",
                f"Le type désigné ({type_slug}) n'est pas celui du contenu ({obj.type}).",
            )
        )
        return None
    if key and obj.key and key != obj.key:
        refusals.append(
            Refusal(
                f"{base}.key",
                "carto_path_mismatch",
                f"La clé désignée ({key}) n'est pas celle du contenu ({obj.key}).",
            )
        )
        return None
    target = expected or path or ""
    existing = space.objects.get(obj.key)
    if existing is None:
        if doc.version:
            refusals.append(
                Refusal(
                    f"{base}.version",
                    "carto_object_not_found",
                    f"Une version est donnée mais aucun objet de clé {obj.key} n'existe : sans "
                    "version, le document est créé.",
                    received=doc.version,
                )
            )
            return None
        return _Step(i, target, doc.content, obj, True, obj.key)
    current = _version(root, existing[0]) or ""
    if doc.version != current:
        refusals.append(_stale(f"{base}.version", doc.version, current))
        return None
    return _Step(i, target, doc.content, obj, False, obj.key)


def _apply(root: Path, steps: list[_Step]) -> None:
    """Joue les gestes par le service ; rejoue les refusés tant qu'un passage en
    fait aboutir un (ordre libre dans la liste). Ce qui reste refusé est traduit."""
    pending = list(steps)
    failures: dict[int, CartographieError] = {}
    while pending:
        progressed = False
        still: list[_Step] = []
        for step in pending:
            try:
                if step.obj is None:
                    delete_object(root, step.key)
                else:
                    save_object(root, step.obj, create=step.create)
            except CartographieError as exc:
                failures[step.index] = exc
                still.append(step)
            else:
                failures.pop(step.index, None)
                progressed = True
        pending = still
        if not progressed:
            break
    if pending:
        space = read_cartographie(root)
        refusals: list[Refusal] = []
        for step in pending:
            base = f"$.documents[{step.index}]"
            where = f"{base}.delete" if step.obj is None else base
            refusals.extend(
                _translate(failures[step.index], f"{base}.content", where, step.content, space)
            )
        raise _Refused(refusals)


async def _open(ctx: PrimitiveContext, args: BranchArgs) -> BranchView:
    if ctx.user is None:  # seconde barrière derrière le catalogue
        raise PrimitiveError("draft_requires_person")
    return await open_branch(ctx, args, write=True)


async def _commit(
    ctx: PrimitiveContext, bv: BranchView, message: str, fn: Any
) -> tuple[Any, str | None]:
    assert ctx.user is not None
    results: list[Any] = []
    try:
        revision = await bv.workdir.commit_to_branch(
            bv.space.id,
            bv.branch.git_ref,
            bv.space.repo_url,
            bv.username,
            bv.token,
            ctx.user.email,
            ctx.user.email,
            f"cartographie : {message} (via l'agent {agent_name(ctx)})",
            lambda root: results.append(fn(root)),
        )
    except _Refused as exc:
        raise PrimitiveError(
            "carto_write_refused",
            written=False,
            errors=[r.payload() for r in exc.refusals],
        ) from None
    except WorkdirError as exc:
        raise PrimitiveError(exc.code) from None
    return results[-1], revision


async def write_carto_documents(ctx: PrimitiveContext, args: WriteCartoArgs) -> dict[str, Any]:
    bv = await _open(ctx, args)

    def write(root: Path) -> list[dict[str, Any]]:
        space = read_cartographie(root)
        steps = _plan(root, space, args)
        _apply(root, steps)
        return [
            {"path": s.path, "deleted": True}
            if s.obj is None
            else {"path": s.path, "created": s.create, "version": _version(root, s.path)}
            for s in steps
        ]

    documents, revision = await _commit(ctx, bv, args.message.strip(), write)
    return {
        "written": True,
        "revision": revision,
        "documents": documents,
        "changed_paths": [d["path"] for d in documents] if revision else [],
    }


# --- Modifier par JSONPath --------------------------------------------------------------

_SEGMENT = re.compile(r"\.([A-Za-z_][A-Za-z0-9_-]*)|\[(\d+)\]|\['([^']+)'\]")


def parse_jsonpath(path: str) -> list[str | int] | None:
    """`$.a.b[0]['c d']` → ['a', 'b', 0, 'c d'] ; None si la forme n'est pas lue."""
    text = path.strip()
    if not text.startswith("$"):
        return None
    pos = 1
    segments: list[str | int] = []
    while pos < len(text):
        m = _SEGMENT.match(text, pos)
        if m is None:
            return None
        name, index, quoted = m.groups()
        segments.append(int(index) if index is not None else (name or quoted))
        pos = m.end()
    return segments


def _apply_operation(doc: dict[str, Any], segments: list[str | int], op: CartoOperationArg) -> str:
    """Applique une opération ; rend '' si elle a abouti, sinon la raison."""
    node: Any = doc
    for seg in segments[:-1]:
        if isinstance(seg, int):
            if not isinstance(node, list) or seg >= len(node):
                return f"l'indice {seg} n'existe pas"
            node = node[seg]
        else:
            if not isinstance(node, dict):
                return f"{seg} n'est pas sous un objet"
            node = node.setdefault(seg, {})
    last = segments[-1]
    if isinstance(last, int):
        if not isinstance(node, list) or last > len(node) or (op.delete and last >= len(node)):
            return f"l'indice {last} n'existe pas"
        if op.delete:
            node.pop(last)
        elif last == len(node):
            node.append(op.value)
        else:
            node[last] = op.value
        return ""
    if not isinstance(node, dict):
        return f"{last} n'est pas sous un objet"
    if op.delete:
        node.pop(last, None)
    else:
        node[last] = op.value
    return ""


async def modify_carto_document(ctx: PrimitiveContext, args: ModifyCartoArgs) -> dict[str, Any]:
    bv = await _open(ctx, args)
    wanted = args.path.strip().lstrip("/")

    def write(root: Path) -> dict[str, Any]:
        space = read_cartographie(root)
        found = next(((k, o) for k, (p, o) in space.objects.items() if p == wanted), None)
        if found is None:
            raise _Refused(
                [Refusal("$.path", "carto_object_not_found", f"Aucun document {args.path}.")]
            )
        current = _version(root, wanted) or ""
        if args.version != current:
            raise _Refused([_stale("$.version", args.version, current)])
        try:
            doc = load_schema((root / wanted).read_text(encoding="utf-8"))
        except (SchemaReadError, UnicodeDecodeError):
            doc = None
        if not isinstance(doc, dict):
            doc = found[1].document()
        refusals: list[Refusal] = []
        for i, op in enumerate(args.operations):
            where = f"$.operations[{i}].path"
            segments = parse_jsonpath(op.path)
            if not segments:
                refusals.append(
                    Refusal(
                        where,
                        "carto_jsonpath_invalid",
                        "JSONPath non lu : attendu '$' suivi de '.nom', '[indice]' ou "
                        "\"['nom']\", et au moins un segment (ex. '$.proprietes.volume_jour').",
                        received=op.path,
                    )
                )
                continue
            if segments[0] in FROZEN:
                code = (
                    "carto_object_type_frozen"
                    if segments[0] == "type"
                    else ("carto_jsonpath_invalid")
                )
                refusals.append(
                    Refusal(
                        where,
                        code,
                        f"{segments[0]} est figé : la clé, le type, la source et la cible d'un "
                        "document ne changent pas.",
                        received=op.path,
                    )
                )
                continue
            if segments[0] not in CONTENT_KEYS:
                refusals.append(
                    Refusal(
                        where,
                        "carto_jsonpath_invalid",
                        f"Champ inconnu {_show(segments[0])} ; un document porte : "
                        f"{', '.join(CONTENT_KEYS)}.",
                        received=op.path,
                    )
                )
                continue
            reason = _apply_operation(doc, segments, op)
            if reason:
                refusals.append(
                    Refusal(
                        where,
                        "carto_jsonpath_invalid",
                        f"Opération impossible : {reason}.",
                        received=op.path,
                    )
                )
        if refusals:
            raise _Refused(refusals)
        obj, problems = _to_object(doc, "$")
        if obj is None:
            raise _Refused(problems)
        try:
            save_object(root, obj, create=False)
        except CartographieError as exc:
            raise _Refused(_translate(exc, "$", "$.path", doc, space)) from None
        return {"path": wanted, "version": _version(root, wanted)}

    message = (args.message or "").strip() or f"modifie {wanted}"
    result, revision = await _commit(ctx, bv, message, write)
    return {
        "written": True,
        "revision": revision,
        "documents": [result],
        "changed_paths": [wanted] if revision else [],
    }
