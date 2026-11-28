"""Les refus des écritures par l'agent, localisés et expliqués (epic « Primitives MCP —
écriture commune par type de document », ticket 10cc4ed6).

Gael : « À chaque fois le JSONPath où se trouve l'erreur et la description. » Chaque
refus porte : le JSONPath de la donnée fautive **dans la requête**
(`$.documents[2].content.proprietes.volume_jour`, `$.operations[1].path`…), un code
(ceux des services, préfixés comme à l'écran), un message en français avec ce qui est
reçu et ce qui est attendu, et — pour une erreur de syntaxe ou de format — `aide`, qui
renvoie l'agent vers la primitive qui décrit le standard.

Rien n'est décidé ici : les refus viennent des services (cartographie, référentiels,
gouvernance) et du registre ; cette couche ne fait que les traduire. La traduction
des refus d'un objet de cartographie est celle du ticket d4723be3, reprise telle quelle.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from ossie_studio.cartographie.catalog import catalog
from ossie_studio.cartographie.errors import CartographieError
from ossie_studio.cartographie.model import properties_schema
from ossie_studio.cartographie.space import Cartographie
from ossie_studio.domains.documents import DomainError as SemanticModelError
from ossie_studio.governance.actions import GovernanceRefused
from ossie_studio.mcp_surface.document_type_base import DocumentError
from ossie_studio.problems import Problem
from ossie_studio.referentiels.errors import ReferentielError
from ossie_studio.referentiels.model import SEPARATOR

DomainError = (
    CartographieError | ReferentielError | GovernanceRefused | DocumentError | SemanticModelError
)
DOMAIN_ERRORS = (
    CartographieError,
    ReferentielError,
    GovernanceRefused,
    DocumentError,
    SemanticModelError,
)

# Les refus de forme : ils renvoient vers le standard du type de document.
_FORMAT_CODES = frozenset(
    {
        "write_content_invalid",
        "write_identity_mismatch",
        "write_process_unattached",
        "carto_object_invalid",
        "carto_type_unreadable",
        "carto_type_invalid",
        "carto_model_unreadable",
        "carto_model_invalid",
        "carto_graphic_unreadable",
        "carto_graphic_invalid",
        "carto_rules_unreadable",
        "carto_rules_invalid",
        "carto_process_unreadable",
        "carto_process_not_bpmn",
        "carto_process_invalid",
        "carto_process_schema_invalid",
        "carto_type_not_found",
        "relation_not_allowed",
        "model_invalid",
        "values_invalid",
        "invalid_request",
    }
)


def aide_standard(standard: str) -> dict[str, str]:
    return {"primitive": "describe_standard", "standard": standard}


AIDE_KINDS = {"primitive": "list_document_types"}


@dataclass
class Refusal:
    path: str  # JSONPath dans la requête (ou, pour une modification, dans le document)
    code: str
    message: str
    expected: Any = None
    received: Any = None
    aide: dict[str, str] | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def payload(self) -> dict[str, Any]:
        out: dict[str, Any] = {"path": self.path, "code": self.code, "message": self.message}
        if self.expected is not None:
            out["expected"] = self.expected
        if self.received is not None:
            out["received"] = self.received
        if self.aide is not None:
            out["aide"] = self.aide
        return {**out, **self.extra}


class Refused(Exception):
    """Au moindre refus, rien n'est écrit : toutes les erreurs sont rendues."""

    def __init__(self, refusals: list[Refusal], warnings: list[dict[str, Any]] | None = None):
        super().__init__("refused")
        self.refusals = refusals
        self.warnings = warnings or []


def show(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def jsonpath(prefix: str, *segments: str | int) -> str:
    out = prefix
    for seg in segments:
        if isinstance(seg, int) or (isinstance(seg, str) and seg.isdigit()):
            out += f"[{seg}]"
        elif re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]*", str(seg)):
            out += f".{seg}"
        else:
            out += f"['{seg}']"
    return out


def _segments(dotted: str) -> list[str]:
    return [s for s in re.split(r"[/.]", dotted) if s]


def _get(doc: Any, segments: list[str]) -> Any:
    for seg in segments:
        if isinstance(doc, dict):
            doc = doc.get(seg)
        elif isinstance(doc, list) and seg.isdigit() and int(seg) < len(doc):
            doc = doc[int(seg)]
        else:
            return None
    return doc


# --- Traduction ---------------------------------------------------------------------------


def translate(
    exc: DomainError,
    *,
    base: str,
    content_path: str,
    content: Any,
    standard: str,
    space: Cartographie | None = None,
) -> list[Refusal]:
    """Un refus d'un service ou du registre, en erreurs localisées. `base` : le
    JSONPath de l'entrée dans la requête ($.documents[i], ou $ pour une
    modification) ; `content_path` : celui de son contenu."""
    if isinstance(exc, DocumentError):
        refusals = [_document(exc, base, content_path)]
    elif isinstance(exc, CartographieError):
        refusals = _carto(exc, base, content_path, content, space)
    elif isinstance(exc, ReferentielError):
        refusals = _referentiel(exc, base, content_path, content)
    elif isinstance(exc, SemanticModelError):
        refusals = [
            Refusal(
                content_path,
                exc.code,
                "Refusé par le service des domaines : "
                + exc.code
                + (f" ({exc.detail})" if exc.detail else "")
                + ".",
            )
        ]
    else:
        refusals = [_governance(exc, base, content_path)]
    for refusal in refusals:
        if refusal.aide is None and refusal.code in _FORMAT_CODES:
            refusal.aide = aide_standard(standard)
    return refusals


def _document(exc: DocumentError, base: str, content_path: str) -> Refusal:
    if exc.field.startswith("$"):
        where = content_path + exc.field[1:]
    elif exc.field in ("path", "delete", "document_type", "version"):
        where = jsonpath(base, exc.field)
    else:
        where = jsonpath(base, "identity", exc.field)
    extra = {k: v for k, v in exc.extra.items() if k not in ("expected", "received")}
    refusal = Refusal(
        where,
        exc.code,
        exc.message,
        expected=exc.extra.get("expected"),
        received=exc.extra.get("received"),
        extra=extra,
    )
    if exc.code == "document_type_unknown":
        refusal.aide = dict(AIDE_KINDS)
    return refusal


def _governance(exc: GovernanceRefused, base: str, content_path: str) -> Refusal:
    detail = {k: v for k, v in exc.detail.items()}
    if exc.code == "invalid_request":
        raw = detail.get("fields")
        fields = [str(f) for f in raw] if isinstance(raw, list) else []
        return Refusal(
            content_path,
            "invalid_request",
            f"Document refusé par la gouvernance : {', '.join(fields) or 'invalide'}.",
            extra={"fields": fields},
        )
    return Refusal(base, exc.code, f"Refus de la gouvernance : {exc.code}.", extra=detail)


def _referentiel(
    exc: ReferentielError, base: str, content_path: str, content: Any
) -> list[Refusal]:
    if not exc.problems:
        return [
            Refusal(base, exc.code, f"Refus du référentiel : {exc.code}.", extra=dict(exc.detail))
        ]
    out = []
    for p in exc.problems:
        where = content_path
        if p.field:
            for part in p.field.split(SEPARATOR):
                m = re.fullmatch(r"([^\[]*)((?:\[\d+\])*)", part)
                name, indexes = (m.group(1), m.group(2)) if m else (part, "")
                where = jsonpath(where, name) if name else where
                where += indexes
        out.append(Refusal(where, exc.code, _value_message(p), extra={"problem": p.code}))
    return out


def _value_message(p: Problem) -> str:
    """Le refus d'une valeur de référentiel, en français ; même texte par MCP et par
    l'import (ticket 1612cce4)."""
    name = p.field.replace(SEPARATOR, ".") or "la valeur"
    label = p.path.split(SEPARATOR)[0]
    texts = {
        "value_required": f"{name} est obligatoire",
        "value_type": f"{name} doit être de type {p.detail}",
        "value_enum": f"{name} : valeur non permise ({p.detail})",
        "value_format": f"{name} : format {p.detail} attendu",
        "value_unknown_property": f"{name} n'est pas une propriété du modèle",
        "key_duplicate": f"la clé {p.detail or label} est déjà présente",
    }
    text = texts.get(p.code) or f"{name} : {p.code}" + (f" ({p.detail})" if p.detail else "")
    return f"Valeur {label} : {text}."


def _carto(
    exc: CartographieError,
    base: str,
    content_path: str,
    content: Any,
    space: Cartographie | None,
) -> list[Refusal]:
    data = content if isinstance(content, dict) else {}
    code = f"carto_{exc.code}"
    if exc.code == "object_invalid" and space is not None:
        return [_problem(p, content_path, data, space) for p in exc.problems]
    if exc.code.startswith("object_"):
        return [_whole(exc, code, content_path, base, data)]
    if exc.code == "process_schema_invalid":
        errors = exc.detail.get("errors")
        listed = errors if isinstance(errors, list) else []
        return [
            Refusal(
                content_path,
                code,
                f"Le XML n'est pas conforme au schéma BPMN 2.0, ligne {e.get('line')} : "
                f"{e.get('message')}.",
                extra={"line": e.get("line")},
            )
            for e in listed
            if isinstance(e, dict)
        ] or [Refusal(content_path, code, str(exc.detail.get("detail", exc.code)))]
    if exc.problems:
        return [
            Refusal(
                content_path,
                code,
                f"Refus de la cartographie : {p.code}"
                + (f" ({p.field})" if p.field else "")
                + (f" : {p.detail}" if p.detail else "")
                + ".",
                extra={"problem": p.code},
            )
            for p in exc.problems
        ]
    detail = exc.detail.get("detail")
    text = f"Refus de la cartographie : {exc.code}" + (f" — {detail}" if detail else "") + "."
    return [Refusal(content_path if detail else base, code, text, extra=dict(exc.detail))]


# --- Objets de cartographie (repris du ticket d4723be3) ----------------------------------


def _describe(schema: Any) -> str:
    """Ce qu'attend un schéma de propriété, en français (pour le message)."""
    if not isinstance(schema, dict):
        return "conforme au schéma du type"
    if "enum" in schema:
        return "l'une des valeurs " + ", ".join(show(v) for v in schema["enum"])
    if "const" in schema:
        return f"égal à {show(schema['const'])}"
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
            parts.append(text.replace("…", str(value)) if "…" in text else f"{text} {show(value)}")
    return " ".join(parts)


def _whole(
    exc: CartographieError, code: str, prefix: str, doc_path: str, content: dict[str, Any]
) -> Refusal:
    key = exc.detail.get("key", content.get("cle"))
    if exc.code == "object_key_taken":
        type_slug = exc.detail.get("type", content.get("type"))
        return Refusal(
            jsonpath(prefix, "cle"),
            code,
            f"La clé {key} est déjà prise dans le type {type_slug} ; une clé est unique dans "
            "son type. Pour remplacer cet objet, donner sa version.",
            received=key,
        )
    if exc.code == "object_type_frozen":
        return Refusal(
            jsonpath(prefix, "type"),
            code,
            f"Le type de l'objet {key} est figé ; reçu {show(content.get('type'))}.",
            received=content.get("type"),
        )
    if exc.code == "object_in_use":
        found = exc.detail.get("relations")
        relations = [str(r) for r in found] if isinstance(found, list) else []
        return Refusal(
            jsonpath(doc_path, "delete"),
            code,
            f"L'objet {key} est utilisé par les relations {', '.join(relations)} : les "
            "supprimer dans la même liste, ou les garder et garder l'objet.",
            extra={"relations": relations},
        )
    if exc.code == "object_in_model":
        return Refusal(
            jsonpath(doc_path, "delete"),
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
        where = jsonpath(prefix, "proprietes", *segs)
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
            f"{name} doit être {_describe(expected)} ; reçu {show(received)}.",
            expected=expected,
            received=received,
        )
    if p.code == "relation_not_allowed":
        source, target = content.get("source"), content.get("cible")
        allowed = _allowed(space, source, target)
        names = ", ".join(f"{label} ({arch})" for arch, label in allowed) or "aucune"
        return Refusal(
            jsonpath(prefix, "type"),
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
        "object_key_duplicate": ("cle", "Cette clé apparaît plusieurs fois dans la liste"),
        "object_type_frozen": ("type", "Le type d'un objet existant ne change pas"),
        "relation_ends_frozen": (
            "source",
            "La source et la cible d'une relation existante ne changent pas : créer la "
            "nouvelle relation et supprimer l'ancienne",
        ),
    }
    if p.code in ("relation_endpoint_unknown", "relation_endpoint_ambiguous"):
        end = "source" if p.field == "source" else "cible"
        value = content.get(end)
        text = (
            f"La {end} {value} n'existe ni dans l'espace ni dans la liste envoyée "
            "(désigner un objet par type/CLÉ, ex. application/APP-01)."
            if p.code == "relation_endpoint_unknown"
            else f"La clé {value} existe dans plusieurs types : désigner la {end} par "
            "type/CLÉ (ex. application/APP-01)."
        )
        return Refusal(jsonpath(prefix, end), code, text, received=value)
    name, text = fields.get(p.code, ("", f"Refus : {p.code}"))
    if p.code == "object_type_unknown":
        code = "carto_type_not_found"
    received = content.get(name) if name else None
    where = jsonpath(prefix, name) if name else prefix
    suffix = f" ; reçu {show(received)}." if received is not None else "."
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
