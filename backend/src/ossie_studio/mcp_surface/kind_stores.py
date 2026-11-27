"""Les validateurs des genres d'écriture (enabler 7b4b8f3a) : pour chaque genre, la
lecture, l'écriture et la suppression d'un document **par le service de l'écran**,
avec ses contrôles et ses refus nommés. Aucune règle de domaine n'est écrite ici :
un validateur traduit le document (tel qu'il est rangé dans l'espace) en appel du
service, vérifie seulement que l'identité donnée concorde avec le contenu
(`write_identity_mismatch`) et que le contenu a la forme d'un document de ce genre
(`write_content_invalid`).

Les refus des services remontent tels quels (CartographieError, ReferentielError,
GovernanceRefused) ; les primitives les traduisent en erreurs localisées.

Ce que l'écran ne sait pas faire, l'agent non plus : supprimer seul un graphique, un
fichier de règles ou un référentiel est refusé (`write_delete_unsupported`).
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

from ossie_studio.cartographie import diagram as carto_diagram
from ossie_studio.cartographie.errors import CartographieError
from ossie_studio.cartographie.model import CartoObject, parse_type
from ossie_studio.cartographie.process import delete_process, parse_process, save_process
from ossie_studio.cartographie.process_tools import is_linked
from ossie_studio.cartographie.space import delete_type, read_cartographie, save_object, save_type
from ossie_studio.drafts.store import git_blob_sha
from ossie_studio.governance import actions
from ossie_studio.governance import repo as governance_repo
from ossie_studio.mcp_surface.kind_base import Document, KindError, Location
from ossie_studio.referentiels.model import model_key
from ossie_studio.referentiels.repo import read_referentiel, write_referentiel
from ossie_studio.referentiels.values import key_of

# Champs d'un objet de cartographie (format du fichier), dans l'ordre d'écriture.
OBJECT_FIELDS = ("cle", "type", "source", "cible", "nom", "documentation", "proprietes")


# --- Outils communs -----------------------------------------------------------------------


def _file_document(root: Path, path: str | None, parse: Any) -> Document | None:
    if path is None:
        return None
    file = root / path
    if not file.is_file():
        return None
    data = file.read_bytes()
    return Document(parse(data), git_blob_sha(data), path)


def _yaml(data: bytes) -> Any:
    return yaml.safe_load(data.decode("utf-8"))


def _text(data: bytes) -> str:
    return data.decode("utf-8")


def _dump(content: Mapping[str, Any]) -> str:
    return yaml.safe_dump(dict(content), sort_keys=False, allow_unicode=True)


def _mapping(content: Any, label: str) -> dict[str, Any]:
    if not isinstance(content, Mapping):
        raise KindError(
            "write_content_invalid",
            "$",
            f"Le contenu d'un document « {label} » est un objet (YAML/JSON) ; reçu "
            f"{type(content).__name__}.",
            received=content,
        )
    return dict(content)


def _unknown(content: Mapping[str, Any], allowed: tuple[str, ...], label: str) -> None:
    for name in content:
        if name not in allowed:
            raise KindError(
                "write_content_invalid",
                f"$.{name}",
                f"Champ inconnu « {name} » pour un document « {label} » ; champs possibles : "
                f"{', '.join(allowed)}.",
                received=content[name],
                expected=list(allowed),
            )


def _same(field: str, given: Any, expected: Any, label: str) -> None:
    """L'identité donnée et le contenu disent la même chose."""
    if given is not None and given != expected:
        raise KindError(
            "write_identity_mismatch",
            f"$.{field}",
            f"Le contenu dit « {field}: {given} » mais l'identité désigne « {expected} » : un "
            f"document « {label} » ne change jamais d'identité. Pour en changer : créer le "
            "nouveau document et supprimer l'ancien dans le même lot (write_documents).",
            expected=expected,
            received=given,
        )


def _unsupported(label: str) -> KindError:
    return KindError(
        "write_delete_unsupported",
        "delete",
        f"Un document « {label} » ne se supprime pas seul (l'écran ne le permet pas non plus).",
    )


# --- Cartographie -------------------------------------------------------------------------


class CartoTypeStore:
    def read(self, root: Path, location: Location) -> Document | None:
        return _file_document(root, location.path, _yaml)

    def write(self, root: Path, location: Location, content: Any, *, create: bool) -> list[str]:
        slug = location.identity["slug"]
        ctype = parse_type(slug, _dump(_mapping(content, "type de cartographie")))
        save_type(root, ctype, create=create)
        return []

    def delete(self, root: Path, location: Location) -> None:
        delete_type(root, location.identity["slug"])


def carto_object(content: Any) -> CartoObject:
    """Le document envoyé, en objet du service : seule la forme est vérifiée (champs
    connus, textes) ; le fond est l'affaire du service."""
    data = _mapping(content, "objet de cartographie")
    _unknown(data, OBJECT_FIELDS, "objet de cartographie")
    for name in ("cle", "type", "nom", "documentation", "source", "cible"):
        value = data.get(name)
        if value is not None and not isinstance(value, str):
            raise KindError(
                "write_content_invalid",
                f"$.{name}",
                f"{name} doit être un texte ; reçu {json.dumps(value, ensure_ascii=False)}.",
                expected={"type": "string"},
                received=value,
            )
    properties = data.get("proprietes")
    if properties is not None and not isinstance(properties, dict):
        raise KindError(
            "write_content_invalid",
            "$.proprietes",
            "proprietes doit être un objet.",
            expected={"type": "object"},
            received=properties,
        )
    return CartoObject(
        key=str(data.get("cle") or "").strip(),
        type=str(data.get("type") or "").strip(),
        name=str(data.get("nom") or "").strip(),
        documentation=str(data.get("documentation") or ""),
        properties=dict(properties or {}),
        source=data.get("source"),
        target=data.get("cible"),
    )


class CartoObjectStore:
    def read(self, root: Path, location: Location) -> Document | None:
        return _file_document(root, location.path, _yaml)

    def write(self, root: Path, location: Location, content: Any, *, create: bool) -> list[str]:
        obj = carto_object(content)
        _same("cle", obj.key or None, location.identity["key"], "objet de cartographie")
        _same("type", obj.type or None, location.identity["type"], "objet de cartographie")
        obj.key = obj.key or location.identity["key"]
        obj.type = obj.type or location.identity["type"]
        save_object(root, obj, create=create)
        return []

    def delete(self, root: Path, location: Location) -> None:
        carto_diagram.delete_object(root, location.identity["key"])


class CartoModelStore:
    """Le modèle et son graphique s'écrivent ensemble par le service : un modèle
    remplacé garde son graphique ; un modèle créé en reçoit un vide."""

    def read(self, root: Path, location: Location) -> Document | None:
        return _file_document(root, location.path, _yaml)

    def write(self, root: Path, location: Location, content: Any, *, create: bool) -> list[str]:
        slug = location.identity["slug"]
        path = location.path or ""
        diagram = carto_diagram.parse_diagram(_dump(_mapping(content, "modèle")), path)
        graphic_file = root / carto_diagram.graphic_path(slug)
        graphic = (
            carto_diagram.parse_graphic(graphic_file.read_text(encoding="utf-8"))
            if graphic_file.is_file() and not create
            else carto_diagram.Graphic()
        )
        problems = carto_diagram.save_diagram(root, slug, diagram, graphic, create=create)
        return [f"{p.code} {p.field or p.detail}".strip() for p in problems]

    def delete(self, root: Path, location: Location) -> None:
        carto_diagram.delete_diagram(root, location.identity["slug"])


class CartoGraphicStore:
    def read(self, root: Path, location: Location) -> Document | None:
        return _file_document(root, location.path, _yaml)

    def write(self, root: Path, location: Location, content: Any, *, create: bool) -> list[str]:
        slug = location.identity["slug"]
        model_file = root / carto_diagram.diagram_path(slug)
        if not model_file.is_file():
            raise CartographieError("model_not_found", status=404, slug=slug)
        diagram = carto_diagram.parse_diagram(model_file.read_text(encoding="utf-8"))
        graphic = carto_diagram.parse_graphic(
            _dump(_mapping(content, "graphique")), location.path or ""
        )
        problems = carto_diagram.save_diagram(root, slug, diagram, graphic, create=False)
        return [f"{p.code} {p.field or p.detail}".strip() for p in problems]

    def delete(self, root: Path, location: Location) -> None:
        raise _unsupported("graphique")


class CartoRulesStore:
    def read(self, root: Path, location: Location) -> Document | None:
        return _file_document(root, location.path, _yaml)

    def write(self, root: Path, location: Location, content: Any, *, create: bool) -> list[str]:
        data = _mapping(content, "règles")
        rules = carto_diagram.parse_rules(_dump(data) if data else "", location.path or "")
        carto_diagram.save_rules(root, location.identity["slug"], rules)
        return []

    def delete(self, root: Path, location: Location) -> None:
        raise _unsupported("règles")


class ProcessStore:
    """Un processus BPMN : le XML tel quel. Décision de Gael : écrit par un agent, il
    doit détailler un processus métier (l'écran, lui, accepte un import non
    rattaché). Les règles souples de la cartographie (élément rattaché à un objet que
    le processus métier ne relie pas) sortent en avertissements, sans bloquer."""

    def read(self, root: Path, location: Location) -> Document | None:
        return _file_document(root, location.path, _text)

    def write(self, root: Path, location: Location, content: Any, *, create: bool) -> list[str]:
        if not isinstance(content, str):
            raise KindError(
                "write_content_invalid",
                "$",
                "Le contenu d'un processus BPMN est le XML, en texte.",
                received=type(content).__name__,
            )
        process = parse_process(content)
        if process.detail_of is None:
            raise KindError(
                "write_process_unattached",
                "$",
                "Un processus écrit par un agent détaille un processus métier : poser "
                '<urban:reference cle="…" type="…"/> dans les extensionElements du '
                "bpmn:process (voir describe_standard bpmn).",
            )
        save_process(root, location.identity["key"], content, create=create)
        return _soft_warnings(root, process.detail_of, process.attached())

    def delete(self, root: Path, location: Location) -> None:
        delete_process(root, location.identity["key"])


def _soft_warnings(root: Path, detail_of: str, attached: list[Any]) -> list[str]:
    """Les éléments rattachés à un objet que le processus métier ne relie pas
    (règle « souple » de l'epic Processus BPMN)."""
    space = read_cartographie(root)
    detail = space.objects.get(detail_of)
    detail_name = detail[1].name if detail else detail_of
    out = []
    for ref in attached:
        found = space.objects.get(ref.key)
        if found is not None and not is_linked(space, detail_of, ref.key):
            out.append(
                f"{found[1].name or ref.key} ({ref.key}) n'est pas relié à {detail_name} dans la "
                "cartographie."
            )
    return out


# --- Référentiels -------------------------------------------------------------------------


class ReferentielModelStore:
    def read(self, root: Path, location: Location) -> Document | None:
        return _file_document(root, location.path, _yaml)

    def write(self, root: Path, location: Location, content: Any, *, create: bool) -> list[str]:
        name = location.identity["referentiel"]
        model = _mapping(content, "modèle de référentiel")
        values = [] if create else read_referentiel(root, name).values
        write_referentiel(root, name, model, values, create=create)
        return []

    def delete(self, root: Path, location: Location) -> None:
        raise _unsupported("modèle de référentiel")


def _value_version(value: Any) -> str:
    """La version d'une valeur : celle de son contenu (les autres valeurs du fichier
    peuvent changer sans la rendre périmée)."""
    canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return git_blob_sha(canonical.encode("utf-8"))


class ReferentielValueStore:
    """Une seule valeur de `valeurs.json` : l'agent ne touche jamais le fichier
    entier ; le service revalide toutes les valeurs et trie le fichier."""

    def _find(self, root: Path, location: Location) -> tuple[Any, list[str], int | None]:
        ref = read_referentiel(root, location.identity["referentiel"])
        fields = model_key(ref.model)
        index = next(
            (i for i, v in enumerate(ref.values) if key_of(v, fields) == location.entry), None
        )
        return ref, fields, index

    def read(self, root: Path, location: Location) -> Document | None:
        ref, _fields, index = self._find(root, location)
        if index is None:
            return None
        value = ref.values[index]
        return Document(value, _value_version(value), location.path or "")

    def write(self, root: Path, location: Location, content: Any, *, create: bool) -> list[str]:
        ref, fields, index = self._find(root, location)
        value = _mapping(content, "valeur de référentiel")
        for name, expected in zip(fields, location.entry or (), strict=False):
            _same(name, value.get(name, expected), expected, "valeur de référentiel")
            value.setdefault(name, expected)
        values = list(ref.values)
        if index is None:
            values.append(value)
        else:
            values[index] = value
        write_referentiel(root, ref.name, ref.model, values, create=False)
        return []

    def delete(self, root: Path, location: Location) -> None:
        ref, _fields, index = self._find(root, location)
        if index is not None:
            values = [v for i, v in enumerate(ref.values) if i != index]
            write_referentiel(root, ref.name, ref.model, values, create=False)


# --- Gouvernance --------------------------------------------------------------------------

TAG_DOCUMENT = ("id", "info", "links", "properties")
SCHEME_DOCUMENT = ("id", *actions.SCHEME_FIELDS, "levels")
LEVEL_DOCUMENT = ("id", *actions.LEVEL_FIELDS)
CERT_DOCUMENT = ("id", "slug", *actions.CERT_FIELDS)


class TagStore:
    """Un tag tel que son fichier l'écrit : id, info (owner, description), links,
    properties."""

    def read(self, root: Path, location: Location) -> Document | None:
        return _file_document(root, location.path, _yaml)

    def write(self, root: Path, location: Location, content: Any, *, create: bool) -> list[str]:
        tag_id = location.identity["id"]
        data = _mapping(content, "tag")
        _unknown(data, TAG_DOCUMENT, "tag")
        _same("id", data.get("id"), tag_id, "tag")
        info = data.get("info") or {}
        if not isinstance(info, Mapping):
            raise KindError("write_content_invalid", "$.info", "info est un objet.", received=info)
        values = {
            "owner": info.get("owner") or "",
            "description": info.get("description") or "",
            "links": data.get("links") or {},
            "properties": data.get("properties") or {},
        }
        if create:
            actions.create_tag(root, tag_id, values)
        else:
            actions.update_tag(root, tag_id, values)
        return []

    def delete(self, root: Path, location: Location) -> None:
        actions.delete_tag(root, location.identity["id"])


class ClassificationStore:
    """Une classification et ses niveaux, tels que son fichier les écrit. Remplacer
    passe par les gestes de l'écran : propriétés, niveaux modifiés (par leur id),
    ajoutés (sans id), retirés, puis remis dans l'ordre du document."""

    def read(self, root: Path, location: Location) -> Document | None:
        return _file_document(root, location.path, _yaml)

    def write(self, root: Path, location: Location, content: Any, *, create: bool) -> list[str]:
        scheme_id = location.identity["id"]
        data = _mapping(content, "classification")
        _unknown(data, SCHEME_DOCUMENT, "classification")
        _same("id", data.get("id"), scheme_id, "classification")
        levels = data.get("levels") or []
        if not isinstance(levels, list) or not all(isinstance(lv, Mapping) for lv in levels):
            raise KindError(
                "write_content_invalid",
                "$.levels",
                "levels est une liste d'objets.",
                received=levels,
            )
        for i, level in enumerate(levels):
            for name in level:
                if name not in LEVEL_DOCUMENT:
                    raise KindError(
                        "write_content_invalid",
                        f"$.levels[{i}].{name}",
                        f"Champ inconnu « {name} » pour un niveau ; champs possibles : "
                        f"{', '.join(LEVEL_DOCUMENT)}.",
                        received=level[name],
                    )
        values = {k: data[k] for k in actions.SCHEME_FIELDS if k in data}
        level_values = [{k: lv[k] for k in actions.LEVEL_FIELDS if k in lv} for lv in levels]
        if create:
            actions.create_scheme(root, {**values, "id": scheme_id}, level_values)
            return []
        actions.update_scheme(root, scheme_id, values)
        current = {lv.id for lv in actions.find_scheme(root, scheme_id).levels}
        kept = {str(lv["id"]) for lv in levels if lv.get("id") in current}
        for level_id in current - kept:
            actions.delete_level(root, scheme_id, level_id)
        order = []
        for level, changes in zip(levels, level_values, strict=True):
            if level.get("id") in current:
                actions.update_level(root, scheme_id, str(level["id"]), changes)
                order.append(str(level["id"]))
            else:
                order.append(actions.add_level(root, scheme_id, changes).id)
        if order:
            actions.reorder_levels(root, scheme_id, order)
        return []

    def delete(self, root: Path, location: Location) -> None:
        actions.delete_scheme(root, location.identity["id"])


class CertificationStore:
    """Une certification, désignée par son slug ; son identifiant interne (le nom
    de son fichier) est donné par le service à la création, comme à l'écran."""

    def read(self, root: Path, location: Location) -> Document | None:
        return _file_document(root, location.path, _yaml)

    def _id(self, root: Path, slug: str) -> str:
        found = next((c for c in governance_repo.read_certs(root) if c.slug == slug), None)
        if found is None:
            raise actions.GovernanceRefused("certification_not_found", 404, certification_id=slug)
        return found.id

    def write(self, root: Path, location: Location, content: Any, *, create: bool) -> list[str]:
        slug = location.identity["slug"]
        data = _mapping(content, "certification")
        _unknown(data, CERT_DOCUMENT, "certification")
        _same("slug", data.get("slug"), slug, "certification")
        values = {k: data[k] for k in actions.CERT_FIELDS if k in data}
        if create:
            actions.create_certification(root, values, slug)
        else:
            cert_id = self._id(root, slug)
            _same("id", data.get("id"), cert_id, "certification")
            actions.update_certification(root, cert_id, values)
        return []

    def delete(self, root: Path, location: Location) -> None:
        actions.delete_certification(root, self._id(root, location.identity["slug"]))
