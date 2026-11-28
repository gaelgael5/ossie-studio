"""Les validateurs des types de document (enabler 7b4b8f3a) : pour chaque type de
document, la lecture, l'écriture et la suppression d'un document **par le service de l'écran**,
avec ses contrôles et ses refus nommés. Aucune règle de domaine n'est écrite ici :
un validateur traduit le document (tel qu'il est rangé dans l'espace) en appel du
service, vérifie seulement que l'identité donnée concorde avec le contenu
(`write_identity_mismatch`) et que le contenu a la forme d'un document de ce type de document
(`write_content_invalid`).

Les refus des services remontent tels quels (CartographieError, ReferentielError,
GovernanceRefused) ; les primitives les traduisent en erreurs localisées.

Ce que l'écran ne sait pas faire, l'agent non plus : supprimer seul un graphique, un
fichier de règles ou un référentiel est refusé (`write_delete_unsupported`).
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import replace as dataclass_replace
from pathlib import Path
from typing import Any

import yaml

from ossie_studio.cartographie import diagram as carto_diagram
from ossie_studio.cartographie.errors import CartographieError
from ossie_studio.cartographie.model import CartoObject, object_ref, parse_type
from ossie_studio.cartographie.process import (
    Process,
    delete_process,
    parse_process,
    resolve_reference,
    save_process,
)
from ossie_studio.cartographie.process_tools import is_linked
from ossie_studio.cartographie.space import (
    Cartographie,
    check_batch,
    delete_type,
    read_cartographie,
    save_object,
    save_objects,
    save_type,
)
from ossie_studio.drafts.store import git_blob_sha
from ossie_studio.governance import actions, classification_rules, relation_profiles, tag_rules
from ossie_studio.governance import repo as governance_repo
from ossie_studio.governance.builtin_classification_rules import builtin_classification_rules
from ossie_studio.governance.builtin_tag_rules import builtin_rules
from ossie_studio.mcp_surface.document_type_base import Document, DocumentError, Location
from ossie_studio.parsers.conventions import SchemaReadError, load_schema
from ossie_studio.problems import Problem
from ossie_studio.referentiels.errors import ReferentielError
from ossie_studio.referentiels.model import model_key
from ossie_studio.referentiels.repo import read_referentiel, write_referentiel
from ossie_studio.referentiels.values import check_values, key_of
from ossie_studio.sources.curation import read_tri_rules_raw, set_tri_rules_fn
from ossie_studio.sources.curation_actions import clean_tri_rules
from ossie_studio.sources.tri import validate_tri_rules

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


def _readable(data: bytes) -> Any:
    """YAML ou JSON lu ; le texte brut si le fichier est illisible."""
    try:
        return load_schema(data)
    except SchemaReadError:
        return data.decode("utf-8", errors="replace")


def _dump(content: Mapping[str, Any]) -> str:
    return yaml.safe_dump(dict(content), sort_keys=False, allow_unicode=True)


def _mapping(content: Any, label: str) -> dict[str, Any]:
    if not isinstance(content, Mapping):
        raise DocumentError(
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
            raise DocumentError(
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
        raise DocumentError(
            "write_identity_mismatch",
            f"$.{field}",
            f"Le contenu dit « {field}: {given} » mais l'identité désigne « {expected} » : un "
            f"document « {label} » ne change jamais d'identité. Pour en changer : créer le "
            "nouveau document et supprimer l'ancien dans le même lot (write_documents).",
            expected=expected,
            received=given,
        )


def _unsupported(label: str) -> DocumentError:
    return DocumentError(
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
            raise DocumentError(
                "write_content_invalid",
                f"$.{name}",
                f"{name} doit être un texte ; reçu {json.dumps(value, ensure_ascii=False)}.",
                expected={"type": "string"},
                received=value,
            )
    properties = data.get("proprietes")
    if properties is not None and not isinstance(properties, dict):
        raise DocumentError(
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


def _by_index(problems: list[Problem]) -> dict[int, list[Problem]]:
    grouped: dict[int, list[Problem]] = defaultdict(list)
    for problem in problems:
        grouped[problem.index].append(problem)
    return grouped


class CartoObjectStore:
    """Un objet ou une relation. `check` valide un lot par les règles du service
    (`space.check_batch`, celles de `save_object`) : c'est la méthode de l'écriture
    commune comme de l'import (ticket 1612cce4)."""

    def read(self, root: Path, location: Location) -> Document | None:
        return _file_document(root, location.path, _yaml)

    def _object(self, location: Location, content: Any) -> CartoObject:
        obj = carto_object(content)
        _same("cle", obj.key or None, location.identity["key"], "objet de cartographie")
        _same("type", obj.type or None, location.identity["type"], "objet de cartographie")
        obj.key = obj.key or location.identity["key"]
        obj.type = obj.type or location.identity["type"]
        return obj

    def check(
        self, scope: Cartographie, entries: Sequence[tuple[Location, Any]]
    ) -> list[tuple[int, Exception]]:
        refused: list[tuple[int, Exception]] = []
        objects: list[CartoObject] = []
        ranks: list[int] = []
        for rank, (location, content) in enumerate(entries):
            try:
                objects.append(self._object(location, content))
                ranks.append(rank)
            except DocumentError as exc:
                refused.append((rank, exc))
        for index, problems in _by_index(check_batch(scope, objects)).items():
            refused.append((ranks[index], CartographieError("object_invalid", problems=problems)))
        return sorted(refused, key=lambda r: r[0])

    def write(self, root: Path, location: Location, content: Any, *, create: bool) -> list[str]:
        refused = self.check(read_cartographie(root), [(location, content)])
        if refused:
            raise refused[0][1]
        save_object(root, self._object(location, content), create=create)
        return []

    def write_batch(self, root: Path, entries: Sequence[tuple[Location, Any, bool]]) -> None:
        """Le lot d'un import (ticket eed2dd0d) : mêmes règles que `write` appelé
        pour chaque entrée, mais la cartographie n'est lue qu'une fois
        (`space.save_objects`). (emplacement, contenu, création ?) ; tout ou rien."""
        save_objects(
            root,
            [(self._object(location, content), create) for location, content, create in entries],
        )

    def delete(self, root: Path, location: Location) -> None:
        identity = location.identity
        carto_diagram.delete_object(root, object_ref(identity["type"], identity["key"]))


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
            raise DocumentError(
                "write_content_invalid",
                "$",
                "Le contenu d'un processus BPMN est le XML, en texte.",
                received=type(content).__name__,
            )
        process = parse_process(content)
        if process.detail_of is None:
            raise DocumentError(
                "write_process_unattached",
                "$",
                "Un processus écrit par un agent détaille un processus métier : poser "
                '<urban:reference cle="…" type="…"/> dans les extensionElements du '
                "bpmn:process (voir describe_standard bpmn).",
            )
        save_process(root, location.identity["key"], content, create=create)
        return _soft_warnings(root, process, process.attached())

    def delete(self, root: Path, location: Location) -> None:
        delete_process(root, location.identity["key"])


def _soft_warnings(root: Path, process: Process, attached: list[Any]) -> list[str]:
    """Les éléments rattachés à un objet que le processus métier ne relie pas
    (règle « souple » de l'epic Processus BPMN)."""
    space = read_cartographie(root)
    detail_of = process.detail_in(space)
    detail = space.find(detail_of)
    detail_name = detail[1].name if detail else process.detail_of
    out = []
    for ref in attached:
        found = space.find(resolve_reference(space, ref))
        if found is None or detail_of is None:
            continue
        if not is_linked(space, detail_of, found[1].ref):
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


def value_version(value: Any) -> str:
    """La version d'une valeur : celle de son contenu (les autres valeurs du fichier
    peuvent changer sans la rendre périmée)."""
    canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return git_blob_sha(canonical.encode("utf-8"))


class ReferentielValueStore:
    """Une seule valeur de `valeurs.json` : l'agent ne touche jamais le fichier
    entier ; le service revalide toutes les valeurs et trie le fichier. `check`
    valide un lot de valeurs par le modèle (`values.check_values`, clé unique
    comprise) : c'est la méthode de l'écriture commune comme de l'import (ticket
    1612cce4)."""

    def _value(self, location: Location, content: Any, fields: list[str]) -> dict[str, Any]:
        value = _mapping(content, "valeur de référentiel")
        for name, expected in zip(fields, location.entry or (), strict=False):
            _same(name, value.get(name, expected), expected, "valeur de référentiel")
            value.setdefault(name, expected)
        return value

    def check(
        self, scope: dict[str, Any], entries: Sequence[tuple[Location, Any]]
    ) -> list[tuple[int, Exception]]:
        fields = model_key(scope)
        refused: list[tuple[int, Exception]] = []
        values: list[dict[str, Any]] = []
        ranks: list[int] = []
        for rank, (location, content) in enumerate(entries):
            try:
                values.append(self._value(location, content, fields))
                ranks.append(rank)
            except DocumentError as exc:
                refused.append((rank, exc))
        for index, problems in _by_index(check_values(scope, values)).items():
            if 0 <= index < len(ranks):
                refused.append(
                    (ranks[index], ReferentielError("values_invalid", problems=problems))
                )
        return sorted(refused, key=lambda r: r[0])

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
        return Document(value, value_version(value), location.path or "")

    def write(self, root: Path, location: Location, content: Any, *, create: bool) -> list[str]:
        ref, fields, index = self._find(root, location)
        refused = self.check(ref.model, [(location, content)])
        if refused:
            raise refused[0][1]
        value = self._value(location, content, fields)
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


# --- Règles (ticket 71347227) : tags, classification, tri ----------------------------------

# Le champ du document qu'un code d'erreur des services de règles désigne.
_RULE_FIELDS = {
    "unknown_key": "$",
    "not_mapping": "$",
    "id_invalid": "$.id",
    "id_reserved": "$.id",
    "files_invalid": "$.files",
    "sources_invalid": "$.sources",
    "pattern_invalid": "$.when",
    "match_invalid": "$.match",
    "conditions_missing": "$.when",
    "condition_unknown": "$.when",
    "condition_invalid": "$.when",
    "tags_invalid": "$.tags",
    "flag_invalid": "$.enabled",
    "effect_missing": "$.scheme",
    "scheme_unknown": "$.scheme",
    "value_unknown": "$.values",
    "single_value": "$.values",
}


def _rule_refused(label: str, errors: list[str]) -> DocumentError:
    """Les refus d'un service de règles, au champ du premier en cause."""
    suffix = errors[0].split("_rule_", 1)[-1]
    return DocumentError(
        "write_content_invalid",
        _RULE_FIELDS.get(suffix, "$"),
        f"Règle « {label} » refusée par le service : {', '.join(errors)}.",
        problems=errors,
    )


def _native_frozen(label: str) -> DocumentError:
    return DocumentError(
        "write_content_invalid",
        "$",
        f"Une règle native ({label}) est fournie par Ossie : seul « enabled » change "
        "(l'activer ou la désactiver, comme à l'écran).",
    )


def _native_delete(label: str) -> DocumentError:
    return DocumentError(
        "write_delete_unsupported",
        "delete",
        f"Une règle native ({label}) ne se supprime pas : la désactiver (enabled: false), "
        "comme à l'écran.",
    )


def _without(doc: Mapping[str, Any], *names: str) -> dict[str, Any]:
    return {k: v for k, v in doc.items() if k not in names}


class _RuleStore:
    """Une règle de tags ou de classification : un fichier du dépôt, ou une règle
    **native** fournie par Ossie dont seul l'état (activée ou non) vit dans le dépôt,
    comme à l'écran. Validée par le service de l'écran."""

    label = ""
    service: Any = None

    def _builtins(self) -> dict[str, Any]:
        raise NotImplementedError

    def native_document(self, root: Path, rule_id: str) -> dict[str, Any] | None:
        return self.native_with(rule_id, self.service.read_builtin_disabled(root))

    def native_with(self, rule_id: str, disabled: set[str]) -> dict[str, Any] | None:
        """La règle native `rule_id`, selon la liste des règles natives désactivées."""
        rule = self._builtins().get(rule_id)
        if rule is None:
            return None
        enabled = rule_id not in disabled
        doc: dict[str, Any] = self.service.rule_document(dataclass_replace(rule, enabled=enabled))
        return doc

    def read(self, root: Path, location: Location) -> Document | None:
        rule_id = location.identity["id"]
        native = self.native_document(root, rule_id)
        if native is not None:
            return Document(native, value_version(native), location.path or "")
        return _file_document(root, location.path, _yaml)

    def _check(self, root: Path, rule: Any) -> list[str]:
        return []

    def _after(self, root: Path, rule: Any) -> list[str]:
        return []

    def write(self, root: Path, location: Location, content: Any, *, create: bool) -> list[str]:
        rule_id = location.identity["id"]
        data = _mapping(content, self.label)
        _same("id", data.get("id"), rule_id, self.label)
        native = self.native_document(root, rule_id)
        if native is not None:
            if _without(data, "id", "enabled") != _without(native, "id", "enabled"):
                raise _native_frozen(self.label)
            disabled = self.service.read_builtin_disabled(root)
            enabled = data.get("enabled", True)
            if not isinstance(enabled, bool):
                raise _rule_refused(self.label, [f"{self.label}_rule_flag_invalid"])
            disabled.discard(rule_id) if enabled else disabled.add(rule_id)
            self.service.write_builtin_disabled(root, disabled)
            return self._after(root, None)
        rule, errors = self.service.parse_rule(rule_id, {**data, "id": rule_id})
        if rule is None:
            raise _rule_refused(self.label, errors)
        errors = self._check(root, rule)
        if errors:
            raise _rule_refused(self.label, errors)
        self._write(root, rule)
        return self._after(root, rule)

    def _write(self, root: Path, rule: Any) -> None:
        raise NotImplementedError

    def delete(self, root: Path, location: Location) -> None:
        rule_id = location.identity["id"]
        if rule_id in self._builtins():
            raise _native_delete(self.label)
        self._delete(root, rule_id)

    def _delete(self, root: Path, rule_id: str) -> None:
        raise NotImplementedError


class TagRuleStore(_RuleStore):
    """Une règle de tags. Comme à l'écran, l'enregistrer crée les tags gouvernés
    qu'elle cite et qui n'existent pas (rendus en avertissement)."""

    label = "règle de tags"
    service = tag_rules

    def _builtins(self) -> dict[str, Any]:
        return {r.id: r for r in builtin_rules()}

    def _write(self, root: Path, rule: Any) -> None:
        tag_rules.write_tag_rule(root, rule)

    def _after(self, root: Path, rule: Any) -> list[str]:
        created = tag_rules.ensure_tags(root, rule) if rule is not None else []
        created += [t for t in tag_rules.ensure_builtin_tags(root) if t not in created]
        return [f"Tag gouverné créé : {tag}." for tag in created]

    def _delete(self, root: Path, rule_id: str) -> None:
        tag_rules.delete_tag_rule(root, rule_id)


class ClassificationRuleStore(_RuleStore):
    """Une règle de classification. Comme à l'écran, son schéma et ses valeurs
    doivent exister au moment de l'enregistrement."""

    label = "règle de classification"
    service = classification_rules

    def _builtins(self) -> dict[str, Any]:
        return {r.id: r for r in builtin_classification_rules()}

    def _check(self, root: Path, rule: Any) -> list[str]:
        found: list[str] = classification_rules.effect_errors(
            rule, governance_repo.read_schemes(root)
        )
        return found

    def _write(self, root: Path, rule: Any) -> None:
        classification_rules.write_classification_rule(root, rule)

    def _delete(self, root: Path, rule_id: str) -> None:
        classification_rules.delete_classification_rule(root, rule_id)


class TriRulesStore:
    """Les règles de tri d'une source : la section `triage` de son manifeste, rien
    d'autre (le service réécrit cette section et garde le reste du manifeste). Le
    contenu est la liste complète des règles."""

    def read(self, root: Path, location: Location) -> Document | None:
        if location.path is None or not (root / location.path).is_file():
            return None
        rules = read_tri_rules_raw(root, location.identity["source"])
        return Document(rules, value_version(rules), location.path)

    def write(self, root: Path, location: Location, content: Any, *, create: bool) -> list[str]:
        source = location.identity["source"]
        if not isinstance(content, list) or not all(isinstance(r, Mapping) for r in content):
            raise DocumentError(
                "write_content_invalid",
                "$",
                "Les règles de tri d'une source sont une liste d'objets (target, pattern, "
                "kind, label).",
                received=content,
            )
        cleaned = clean_tri_rules([dict(r) for r in content])
        errors = validate_tri_rules(cleaned)
        if errors:
            index = errors[0].split("]", 1)[0].removeprefix("triage[")
            raise DocumentError(
                "write_content_invalid",
                f"$[{index}]" if index.isdigit() else "$",
                f"Règles de tri refusées : {', '.join(errors)}.",
                problems=errors,
            )
        if location.path is None or not (root / location.path).is_file():
            raise DocumentError(
                "write_identity_invalid",
                "source",
                f"La source « {source} » n'existe pas (list_documents, type de document « source "
                f"»).",
                received=source,
            )
        set_tri_rules_fn(source, cleaned)(root)
        return []

    def delete(self, root: Path, location: Location) -> None:
        raise DocumentError(
            "write_delete_unsupported",
            "delete",
            "Les règles de tri d'une source ne se suppriment pas : écrire une liste vide "
            "pour n'en avoir aucune.",
        )


# --- Types de document en lecture seule (ticket 56f3784a)
# ----------------------------------------------


class ReadOnlyStore:
    """Un document lisible, jamais écrit directement (schéma raw ou d'asset, manifeste
    d'une source…). Il est produit par une action (la collecte, la curation, la
    déclaration d'une source), qui seule sait le tenir à jour. Un fichier illisible
    (YAML cassé) est rendu en texte : l'agent voit ce qui ne va pas."""

    def __init__(self, label: str, actions: tuple[str, ...]) -> None:
        self.label = label
        self.actions = actions

    def read(self, root: Path, location: Location) -> Document | None:
        return _file_document(root, location.path, _readable)

    def _refused(self) -> DocumentError:
        return read_only(self.label, self.actions)

    def write(self, root: Path, location: Location, content: Any, *, create: bool) -> list[str]:
        raise self._refused()

    def delete(self, root: Path, location: Location) -> None:
        raise self._refused()


def read_only(label: str, actions: tuple[str, ...]) -> DocumentError:
    """Le refus d'écrire un type de document en lecture seule : il nomme l'action à utiliser."""
    return DocumentError(
        "document_type_read_only",
        "document_type",
        f"Un document « {label} » ne s'écrit pas directement : il est produit par "
        f"{' / '.join(actions)}. Utiliser cette action à la place.",
        actions=list(actions),
    )


# --- Profils de relations (epic « lectures communes », ticket 970c90ae) --------------------

_PROFILE_FIELDS = {
    "relation_profile_id_invalid": "$.id",
    "relation_profile_id_reserved": "$.id",
    "relation_profile_rules_missing": "$.rules",
}
_PROFILE_RULE_FIELDS = {
    "relation_rule_pattern_invalid": ".column",
    "relation_rule_template_invalid": ".table",
}


def _profile_refused(profile_id: str, data: Mapping[str, Any], errors: list[str]) -> DocumentError:
    """Les refus du service des profils (`parse_profile`, celui de l'écran), au champ en
    cause : pour une règle, la première refusée, retrouvée par le même service."""
    path = _PROFILE_FIELDS.get(errors[0], "$")
    rules = data.get("rules")
    if errors[0].startswith("relation_rule_") and isinstance(rules, list):
        for index, rule in enumerate(rules):
            _found, own = relation_profiles.parse_profile(
                profile_id, {"id": profile_id, "rules": [rule]}
            )
            if own:
                path = f"$.rules[{index}]{_PROFILE_RULE_FIELDS.get(own[0], '')}"
                break
    return DocumentError(
        "write_content_invalid",
        path,
        f"Profil de relations refusé par le service : {', '.join(errors)}.",
        problems=errors,
    )


class RelationProfileStore:
    """Un profil de relations virtuelles (`governance/relation-profiles/<id>.yaml`),
    validé par le service de l'écran. Le profil **fourni** par Ossie (« Data Vault
    2.0 ») existe sans fichier ; l'écrire sous son identifiant le personnalise, et
    supprimer la personnalisation le rétablit — comme à l'écran (ticket 1bce7eb9)."""

    label = "profil de relations"

    def _builtin(self, profile_id: str) -> dict[str, Any] | None:
        found = next((p for p in relation_profiles.builtin_profiles() if p.id == profile_id), None)
        return relation_profiles.profile_document(found) if found else None

    def read(self, root: Path, location: Location) -> Document | None:
        own = _file_document(root, location.path, _readable)
        if own is not None:
            return own
        builtin = self._builtin(location.identity["id"])
        if builtin is None:
            return None
        return Document(builtin, value_version(builtin), location.path or "")

    def write(self, root: Path, location: Location, content: Any, *, create: bool) -> list[str]:
        profile_id = location.identity["id"]
        data = _mapping(content, self.label)
        _same("id", data.get("id"), profile_id, self.label)
        document = {**data, "id": profile_id}
        profile, errors = relation_profiles.parse_profile(profile_id, document)
        if profile is None:
            raise _profile_refused(profile_id, document, errors)
        relation_profiles.write_profile(root, profile)
        return []

    def delete(self, root: Path, location: Location) -> None:
        profile_id = location.identity["id"]
        if not relation_profiles.delete_profile(root, profile_id):
            raise DocumentError(
                "write_delete_unsupported",
                "delete",
                f"Le profil fourni « {profile_id} » ne se supprime pas : seule sa version "
                "personnalisée dans l'espace se supprime (pour rétablir l'original), comme à "
                "l'écran.",
            )


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
            raise DocumentError(
                "write_content_invalid", "$.info", "info est un objet.", received=info
            )
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
            raise DocumentError(
                "write_content_invalid",
                "$.levels",
                "levels est une liste d'objets.",
                received=levels,
            )
        for i, level in enumerate(levels):
            for name in level:
                if name not in LEVEL_DOCUMENT:
                    raise DocumentError(
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
