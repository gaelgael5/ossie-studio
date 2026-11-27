"""Registre des genres d'écriture (epic « Primitives MCP — écriture commune par genre »,
enabler 7b4b8f3a).

Les primitives communes (`write_documents`, `patch_document`, `read_document`,
`read_schema`, `list_write_kinds`) savent toutes, pour un genre, la même chose :
quelle identité demander, où ranger, si un chemin peut être fourni, quel standard
suit le contenu, quelle syntaxe sert à le modifier et quelle capacité il faut. Cette
déclaration vit **ici, une seule fois** ; les primitives la lisent, aucune ne la
recopie.

Décision de Gael : l'agent ne choisit pas l'emplacement d'un document (« pour écrire un
objet Application Component, il y a une place dédiée dans le repo. L'agent n'a pas son
mot à dire »). Le chemin se déduit du genre et de l'identité, par **les fonctions des
services** — celles qu'utilise l'écran : aucune constante d'emplacement n'est recopiée.
Le paramètre `path` reste possible, mais chaque genre déclare s'il est interdit ou
autorisé ; il est interdit pour tous les genres aujourd'hui (le premier cas autorisé
prévu : les datasets, le jour où les domaines deviendront des répertoires).

Une identité invalide (slug mal formé, type inconnu, clé vide…) est refusée avec une
erreur qui nomme le champ (`write_identity_invalid`).

Décisions de Gael (27 novembre) : un processus BPMN s'identifie par **sa** clé (celle de
son fichier ; un processus métier peut en avoir plusieurs) ; une certification par son
**slug** — son fichier porte un identifiant interne, retrouvé dans l'espace, et c'est le
service qui le nomme à la création, comme à l'écran (`Location.path` vaut alors None).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Literal

from ossie_studio.cartographie.diagram import diagram_path, graphic_path, rules_path
from ossie_studio.cartographie.model import is_valid_key, object_path, type_path
from ossie_studio.cartographie.process import process_path
from ossie_studio.cartographie.space import read_cartographie
from ossie_studio.governance import classification_rules as classification_rule_service
from ossie_studio.governance import tag_rules as tag_rule_service
from ossie_studio.governance.builtin_classification_rules import builtin_classification_rules
from ossie_studio.governance.builtin_tag_rules import builtin_rules
from ossie_studio.governance.repo import (
    InvalidGovernanceId,
    cert_path,
    read_certs,
    scheme_path,
    tag_path,
)
from ossie_studio.mcp_surface import kind_domains as domains
from ossie_studio.mcp_surface import kind_lists as lists
from ossie_studio.mcp_surface import kind_stores as stores
from ossie_studio.mcp_surface.kind_base import (
    IdentityField,
    KindError,
    Lister,
    ListFilter,
    Location,
    Store,
)
from ossie_studio.parsers.openapi import is_valid_version_name
from ossie_studio.referentiels.errors import ReferentielError
from ossie_studio.referentiels.model import load_model, model_key
from ossie_studio.referentiels.repo import model_path, values_path
from ossie_studio.slugs import is_valid_slug
from ossie_studio.sources.asset_files import is_extend_name
from ossie_studio.sources.collect_core import RAW_DIR
from ossie_studio.sources.contracts import read_contracts_view
from ossie_studio.sources.curation import is_schema_file
from ossie_studio.sources.paths import (
    ASSETS_DIR,
    CONTRATS_DIR,
    MANIFEST_FILENAME,
    is_valid_source_slug,
    source_rel_dir,
)

PathRule = Literal["forbidden", "allowed"]
Syntax = Literal["jsonpath", "xpath"]


Locator = Callable[[Path | None, Mapping[str, Any]], Location]


@dataclass(frozen=True)
class WriteKind:
    name: str
    description: str  # en anglais, pour l'agent
    label: str  # en français, pour l'écran et les messages
    identity: tuple[IdentityField, ...]
    standard: str  # l'identifiant à passer à describe_standard
    syntax: Syntax  # la syntaxe des chemins de patch_document
    capability: str  # pour écrire : la même que l'écran
    read_capability: str  # pour lire
    locator: Locator
    store: Store  # le validateur : le service de l'écran
    lister: Lister  # la liste du genre (list_documents), par le service de l'écran
    # Les champs du contenu qui portent l'identité : ils ne changent jamais (Gael :
    # « il faut recréer un objet et supprimer le précédent »).
    identity_paths: tuple[str, ...] = ()
    # Les autres champs figés (JSONPath dans le contenu).
    frozen: tuple[str, ...] = ()
    # Les filtres propres au genre que list_documents accepte (en plus de l'identité
    # partielle et de la recherche texte, communes).
    list_filters: tuple[ListFilter, ...] = ()
    path_rule: PathRule = "forbidden"
    path_root: str = ""  # sous laquelle doit rester un chemin autorisé
    # Écriture permise, ou interdite (ticket 56f3784a) : un genre en lecture seule est
    # produit par une action, que nomme le refus (`produced_by`).
    writable: bool = True
    produced_by: tuple[str, ...] = ()
    # Le document est lui-même un schéma : read_schema le lit par son identité.
    is_schema: bool = False
    # Le schéma du contenu, défini par le code (ticket b4aeae83) : read_schema(kind) le
    # rend tel quel (un dataset : la part Ossie 0.1.1 officielle et la part extension).
    content_schema: Callable[[], dict[str, Any]] | None = None
    # Le champ d'identité qui donne le schéma exact du contenu (read_schema), et le
    # genre du document qui porte ce schéma : son identité (un seul champ) prend la
    # valeur de ce champ (le type d'un objet → le document carto_type de ce slug).
    typed_by: str | None = None
    schema_kind: str | None = None

    def locate(
        self, root: Path | None, identity: Mapping[str, Any], path: str | None = None
    ) -> Location:
        """L'emplacement d'un document de ce genre, calculé depuis son identité ; un
        `path` fourni est soumis à la règle du genre. `root` (l'espace) sert aux
        contrôles qui le lisent : type de cartographie connu, clé d'un référentiel."""
        self._check_fields(identity)
        location = self.locator(root, identity)
        given = (path or "").strip()
        if not given:
            return location
        if self.path_rule == "forbidden":
            raise KindError(
                "write_path_forbidden",
                "path",
                f"Le chemin est fixé par le système pour le genre {self.name} ; ne pas le fournir.",
                expected=location.path,
            )
        return Location(self.name, location.identity, self._allowed(given), location.entry)

    def _check_fields(self, identity: Mapping[str, Any]) -> None:
        names = [f.name for f in self.identity]
        for f in self.identity:
            value = identity.get(f.name)
            if value is None or (isinstance(value, str) and not value.strip()):
                raise KindError(
                    "write_identity_invalid",
                    f.name,
                    f"Le champ « {f.name} » est obligatoire pour le genre {self.name} "
                    f"({f.format}).",
                )
        for name in identity:
            if name not in names:
                raise KindError(
                    "write_identity_invalid",
                    name,
                    f"Le genre {self.name} ne connaît pas le champ « {name} » ; son identité "
                    f"est : {', '.join(names)}.",
                )

    def _allowed(self, given: str) -> str:
        pure = PurePosixPath(given)
        root = PurePosixPath(self.path_root)
        if pure.is_absolute() or ".." in pure.parts or not pure.is_relative_to(root):
            raise KindError(
                "write_path_outside_root",
                "path",
                f"Le chemin d'un document du genre {self.name} reste sous {self.path_root}/, "
                "sans « .. » ni chemin absolu.",
                received=given,
            )
        return pure.as_posix()


# --- Contrôles d'identité (ceux des services, jamais recopiés) ---------------------------


def _bad(kind: str, field: str, value: Any, expected: str) -> KindError:
    return KindError(
        "write_identity_invalid",
        field,
        f"« {field} » invalide pour le genre {kind} : {expected} ; reçu « {value} ».",
        received=value,
    )


def _slug(kind: str, field: str, identity: Mapping[str, Any]) -> str:
    value = identity[field]
    if not isinstance(value, str) or not is_valid_slug(value):
        raise _bad(kind, field, value, "minuscules, chiffres et tirets (ex. « application »)")
    return value


def _key(kind: str, field: str, identity: Mapping[str, Any]) -> str:
    value = identity[field]
    if not isinstance(value, str) or not is_valid_key(value):
        raise _bad(kind, field, value, "majuscules, chiffres et tirets (ex. « APP-CAISSE »)")
    return value


def _ids(identity: Mapping[str, Any], *names: str) -> dict[str, Any]:
    return {name: identity[name] for name in names}


# --- Les genres ------------------------------------------------------------------------


def _carto_type(root: Path | None, identity: Mapping[str, Any]) -> Location:
    slug = _slug("carto_type", "slug", identity)
    return Location("carto_type", _ids(identity, "slug"), type_path(slug))


def _carto_objet(root: Path | None, identity: Mapping[str, Any]) -> Location:
    type_slug = _slug("carto_objet", "type", identity)
    key = _key("carto_objet", "key", identity)
    if root is not None and type_slug not in read_cartographie(root).types:
        raise KindError(
            "write_identity_invalid",
            "type",
            f"Le type « {type_slug} » n'existe pas dans la cartographie de l'espace ; un objet "
            "(ou une relation) prend un type existant (list_documents, genre carto_type).",
            received=type_slug,
        )
    return Location("carto_objet", _ids(identity, "type", "key"), object_path(type_slug, key))


def _diagram_file(kind: str, path_of: Callable[[str], str]) -> Locator:
    def locate(root: Path | None, identity: Mapping[str, Any]) -> Location:
        return Location(kind, _ids(identity, "slug"), path_of(_slug(kind, "slug", identity)))

    return locate


def _processus(root: Path | None, identity: Mapping[str, Any]) -> Location:
    return Location(
        "processus", _ids(identity, "key"), process_path(_key("processus", "key", identity))
    )


def _referentiel(kind: str, identity: Mapping[str, Any]) -> str:
    name = identity["referentiel"]
    try:
        model_path(str(name))
    except ReferentielError:
        raise _bad(kind, "referentiel", name, "le slug d'un référentiel (ex. « pays »)") from None
    return str(name)


def _referentiel_modele(root: Path | None, identity: Mapping[str, Any]) -> Location:
    name = _referentiel("referentiel_modele", identity)
    return Location("referentiel_modele", _ids(identity, "referentiel"), model_path(name))


def _referentiel_valeur(root: Path | None, identity: Mapping[str, Any]) -> Location:
    """Une valeur dans `valeurs.json` : sa clé suit le modèle du référentiel (un champ,
    ou plusieurs pour une clé composée)."""
    kind = "referentiel_valeur"
    name = _referentiel(kind, identity)
    given = identity["key"]
    fields: list[str] | None = None
    if root is not None:
        file = root / model_path(name)
        if not file.is_file():
            raise KindError(
                "write_identity_invalid",
                "referentiel",
                f"Le référentiel « {name} » n'existe pas dans l'espace (list_documents, "
                "genre referentiel_modele).",
                received=name,
            )
        fields = model_key(load_model(file.read_bytes()))
    entry = _entry(kind, given, fields)
    return Location(kind, _ids(identity, "referentiel", "key"), values_path(name), entry)


def _entry(kind: str, given: Any, fields: list[str] | None) -> tuple[Any, ...]:
    """Les valeurs de la clé, dans l'ordre du modèle. Une clé d'un seul champ peut se
    donner seule ; une clé composée se donne en objet {champ: valeur}."""
    if isinstance(given, Mapping):
        if fields is None:
            return tuple(given.values())
        missing = [f for f in fields if f not in given]
        extra = [f for f in given if f not in fields]
        if missing or extra:
            raise KindError(
                "write_identity_invalid",
                "key",
                f"« key » : la clé d'une valeur de ce référentiel est faite de : "
                f"{', '.join(fields)}"
                + (f" ; manque : {', '.join(missing)}" if missing else "")
                + (f" ; en trop : {', '.join(extra)}" if extra else "")
                + ".",
                expected=fields,
                received=dict(given),
            )
        return tuple(given[f] for f in fields)
    if fields is not None and len(fields) != 1:
        raise KindError(
            "write_identity_invalid",
            "key",
            f"« key » : la clé de ce référentiel est composée ({', '.join(fields)}) : la donner "
            "en objet {champ: valeur}.",
            expected=fields,
            received=given,
        )
    return (given,)


def _governance(kind: str, path_of: Callable[[str], str], example: str) -> Locator:
    def locate(root: Path | None, identity: Mapping[str, Any]) -> Location:
        value = identity["id"]
        try:
            path = path_of(str(value))
        except InvalidGovernanceId:
            raise _bad(kind, "id", value, f"un identifiant sûr (ex. « {example} »)") from None
        return Location(kind, _ids(identity, "id"), path)

    return locate


def _certification(root: Path | None, identity: Mapping[str, Any]) -> Location:
    """Une certification, par son slug : le fichier de celle qui le porte ; aucun
    pour une nouvelle (le service le nomme)."""
    slug = _slug("certification", "slug", identity)
    found = next((c for c in read_certs(root) if c.slug == slug), None) if root else None
    return Location("certification", {"slug": slug}, cert_path(found.id) if found else None)


def _source(root: Path | None, identity: Mapping[str, Any]) -> Location:
    """Une source : son manifeste, `sources/<slug>/manifest.yaml`."""
    source = identity["source"]
    if not isinstance(source, str) or not is_valid_source_slug(source):
        raise _bad("source", "source", source, "le slug d'une source (list_documents)")
    return Location("source", {"source": source}, f"{source_rel_dir(source)}/{MANIFEST_FILENAME}")


def _contract(root: Path | None, identity: Mapping[str, Any]) -> Location:
    """Un contrat OpenAPI déposé : le fichier que l'index `contrats.yaml` de la source
    range pour cette version (aucun si la version n'y est pas)."""
    source = identity["source"]
    if not isinstance(source, str) or not is_valid_source_slug(source):
        raise _bad("contrat", "source", source, "le slug d'une source (list_documents)")
    version = identity["version"]
    if not isinstance(version, str) or not is_valid_version_name(version):
        raise _bad("contrat", "version", version, "une version déposée (ex. « v1 »)")
    views = read_contracts_view(root, source) if root else []
    found = next((v.entry for v in views if v.entry.version == version), None)
    path = f"{source_rel_dir(source)}/{CONTRATS_DIR}/{found.file}" if found else None
    return Location("contrat", {"source": source, "version": version}, path)


def _schema_file(kind: str, folder: str) -> Locator:
    """Un schéma d'une source : `<source>/<folder>/<nom>` (raw/ ou assets/). Le nom est
    celui que rend la liste (un fichier de schéma, jamais « .. »)."""

    def locate(root: Path | None, identity: Mapping[str, Any]) -> Location:
        source = identity["source"]
        if not isinstance(source, str) or not is_valid_source_slug(source):
            raise _bad(
                kind, "source", source, "le slug d'une source (list_documents, genre « source »)"
            )
        name = identity["name"]
        pure = PurePosixPath(str(name))
        valid = (
            isinstance(name, str)
            and not pure.is_absolute()
            and ".." not in pure.parts
            and is_schema_file(pure.name)
            and not (folder == ASSETS_DIR and is_extend_name(name))
        )
        if not valid:
            raise _bad(kind, "name", name, "le nom d'un schéma, tel que la liste le rend")
        path = f"{source_rel_dir(source)}/{folder}/{pure.as_posix()}"
        return Location(kind, _ids(identity, "source", "name"), path)

    return locate


_SLUG = "lowercase letters, digits and dashes"
_SOURCE_SLUG = "Slug of the source (list_documents, kind 'source')."
_KEY = "uppercase letters, digits and dashes"

KINDS: tuple[WriteKind, ...] = (
    WriteKind(
        name="carto_type",
        description="A type of the map: its ArchiMate type and its added properties.",
        label="type de cartographie",
        identity=(IdentityField("slug", "Slug of the type.", f"{_SLUG}, e.g. 'application'"),),
        standard="carto-type",
        syntax="jsonpath",
        capability="cartographie:write",
        read_capability="cartographie:read",
        locator=_carto_type,
        store=stores.CartoTypeStore(),
        lister=lists.carto_types,
        list_filters=(lists.NATURE, lists.ARCHIMATE),
    ),
    WriteKind(
        name="carto_objet",
        description=(
            "An object of the map, or a relationship (an object of a relationship type, "
            "with source and cible)."
        ),
        label="objet de cartographie",
        identity=(
            IdentityField("type", "Slug of an existing type of the map.", f"{_SLUG}"),
            IdentityField("key", "Functional key of the object.", f"{_KEY}, e.g. 'APP-CAISSE'"),
        ),
        standard="carto-objet",
        syntax="jsonpath",
        capability="cartographie:write",
        read_capability="cartographie:read",
        locator=_carto_objet,
        store=stores.CartoObjectStore(),
        lister=lists.carto_objects,
        list_filters=(lists.NATURE, lists.ARCHIMATE, lists.SOURCE, lists.CIBLE),
        identity_paths=("$.cle", "$.type"),
        frozen=("$.source", "$.cible"),
        typed_by="type",
        schema_kind="carto_type",
    ),
    WriteKind(
        name="carto_modele",
        description="A model of the map: the objects it keeps and its views.",
        label="modèle de cartographie",
        identity=(IdentityField("slug", "Slug of the model.", _SLUG),),
        standard="carto-modele",
        syntax="jsonpath",
        capability="cartographie:write",
        read_capability="cartographie:read",
        locator=_diagram_file("carto_modele", diagram_path),
        store=stores.CartoModelStore(),
        lister=lists.carto_models,
        list_filters=(lists.CONTAINS,),
        identity_paths=("$.id",),
    ),
    WriteKind(
        name="carto_graphique",
        description="The drawing of a model: positions and sizes per view.",
        label="graphique d'un modèle",
        identity=(IdentityField("slug", "Slug of the model.", _SLUG),),
        standard="carto-graphique",
        syntax="jsonpath",
        capability="cartographie:write",
        read_capability="cartographie:read",
        locator=_diagram_file("carto_graphique", graphic_path),
        store=stores.CartoGraphicStore(),
        lister=lists.carto_graphics,
    ),
    WriteKind(
        name="carto_regles",
        description="A rules file: how the types of the space translate to ArchiMate.",
        label="règles de cartographie",
        identity=(IdentityField("slug", "Slug of the rules file.", f"{_SLUG}, e.g. 'standard'"),),
        standard="carto-regles",
        syntax="jsonpath",
        capability="cartographie:write",
        read_capability="cartographie:read",
        locator=_diagram_file("carto_regles", rules_path),
        store=stores.CartoRulesStore(),
        lister=lists.carto_rules,
    ),
    WriteKind(
        name="processus",
        description=(
            "A BPMN 2.0 process (XML, diagram included) detailing a business process of the map."
        ),
        label="processus BPMN",
        identity=(
            IdentityField(
                "key", "Key of the BPMN process.", f"{_KEY}, e.g. 'PRC-PRISE-COMMANDE-DETAIL'"
            ),
        ),
        standard="bpmn",
        syntax="xpath",
        capability="cartographie:write",
        read_capability="cartographie:read",
        locator=_processus,
        store=stores.ProcessStore(),
        lister=lists.processes,
        list_filters=(lists.DETAIL_OF,),
    ),
    WriteKind(
        name="referentiel_modele",
        description="The model (JSON Schema) of a reference list.",
        label="modèle de référentiel",
        identity=(IdentityField("referentiel", "Slug of the reference list.", _SLUG),),
        standard="referentiel-modele",
        syntax="jsonpath",
        capability="referentiels:write",
        read_capability="referentiels:read",
        locator=_referentiel_modele,
        store=stores.ReferentielModelStore(),
        lister=lists.referentiel_models,
    ),
    WriteKind(
        name="referentiel_valeur",
        description=(
            "One value of a reference list, designated by its key (never the whole values file)."
        ),
        label="valeur de référentiel",
        identity=(
            IdentityField("referentiel", "Slug of the reference list.", _SLUG),
            IdentityField(
                "key",
                "Key of the value, as the model defines it.",
                "the value of the key field, or {field: value} for a composite key",
            ),
        ),
        standard="referentiel-valeur",
        syntax="jsonpath",
        capability="referentiels:write",
        read_capability="referentiels:read",
        locator=_referentiel_valeur,
        store=stores.ReferentielValueStore(),
        lister=lists.referentiel_values,
        list_filters=(lists.EQUALS,),
        typed_by="referentiel",
        schema_kind="referentiel_modele",
    ),
    WriteKind(
        name="tag",
        description="A governance tag (hierarchy by '/').",
        label="tag",
        identity=(IdentityField("id", "Id of the tag.", "segments [a-z0-9_-] joined by '/'"),),
        standard="tag",
        syntax="jsonpath",
        capability="governance:write",
        read_capability="governance:read",
        locator=_governance("tag", tag_path, "pii/email"),
        store=stores.TagStore(),
        lister=lists.tags,
        identity_paths=("$.id",),
    ),
    WriteKind(
        name="classification",
        description="A classification scheme with its ordered levels.",
        label="classification",
        identity=(IdentityField("id", "Id of the classification.", "a safe name"),),
        standard="classification",
        syntax="jsonpath",
        capability="governance:write",
        read_capability="governance:read",
        locator=_governance("classification", scheme_path, "confidentialite"),
        store=stores.ClassificationStore(),
        lister=lists.classifications,
        identity_paths=("$.id",),
    ),
    WriteKind(
        name="certification",
        description="A certification that can be granted to assets.",
        label="certification",
        identity=(IdentityField("slug", "Slug of the certification.", f"{_SLUG}, e.g. 'or'"),),
        standard="certification",
        syntax="jsonpath",
        capability="governance:write",
        read_capability="governance:read",
        locator=_certification,
        store=stores.CertificationStore(),
        lister=lists.certifications,
        identity_paths=("$.slug", "$.id"),
    ),
)

# --- Les règles (ticket 71347227) -----------------------------------------------------------


def _rule(kind: str, service: Any, builtin_ids: Callable[[], set[str]]) -> Locator:
    """Une règle de tags ou de classification, par son identifiant : son fichier, ou,
    pour une règle native (fournie par Ossie), le fichier d'état des natives."""

    def locate(root: Path | None, identity: Mapping[str, Any]) -> Location:
        rule_id = identity["id"]
        if not isinstance(rule_id, str) or not service.is_valid_rule_id(rule_id):
            raise _bad(kind, "id", rule_id, "minuscules, chiffres, « _ » et « - »")
        if rule_id in builtin_ids():
            return Location(kind, {"id": rule_id}, service.BUILTIN_STATE_FILE)
        return Location(kind, {"id": rule_id}, service.rule_path(rule_id))

    return locate


def _tri_rules(root: Path | None, identity: Mapping[str, Any]) -> Location:
    source = identity["source"]
    if not isinstance(source, str) or not is_valid_source_slug(source):
        raise _bad(
            "regles_tri",
            "source",
            source,
            "le slug d'une source (list_documents, genre « source »)",
        )
    return Location(
        "regles_tri", {"source": source}, f"{source_rel_dir(source)}/{MANIFEST_FILENAME}"
    )


def _tag_rule_ids() -> set[str]:
    return {r.id for r in builtin_rules()}


def _classification_rule_ids() -> set[str]:
    return {r.id for r in builtin_classification_rules()}


_RULE_ID = IdentityField(
    "id", "Id of the rule.", "lowercase letters, digits, '_' and '-'; 'ossie-…' for native rules"
)
KINDS = (
    *KINDS,
    WriteKind(
        name="regle_tag",
        description=(
            "A tag rule: puts governed tags on raw objects from what the code says of them. "
            "Native rules (provided by Ossie) are listed too: only 'enabled' changes."
        ),
        label="règle de tags",
        identity=(_RULE_ID,),
        standard="regle-tag",
        syntax="jsonpath",
        capability="governance:write",
        read_capability="governance:read",
        locator=_rule("regle_tag", tag_rule_service, _tag_rule_ids),
        store=stores.TagRuleStore(),
        lister=lists.tag_rule_items,
        identity_paths=("$.id",),
    ),
    WriteKind(
        name="regle_classification",
        description=(
            "A classification rule: gives a classification level to fields or objects. "
            "Native rules (provided by Ossie) are listed too: only 'enabled' changes."
        ),
        label="règle de classification",
        identity=(_RULE_ID,),
        standard="regle-classification",
        syntax="jsonpath",
        capability="governance:write",
        read_capability="governance:read",
        locator=_rule(
            "regle_classification", classification_rule_service, _classification_rule_ids
        ),
        store=stores.ClassificationRuleStore(),
        lister=lists.classification_rule_items,
        identity_paths=("$.id",),
    ),
    WriteKind(
        name="regles_tri",
        description=(
            "The sorting rules of a source (they set noise aside in the curation): the "
            "whole list, in the source's manifest. Nothing else of the manifest is touched."
        ),
        label="règles de tri",
        identity=(IdentityField("source", _SOURCE_SLUG, _SLUG),),
        standard="regles-tri",
        syntax="jsonpath",
        capability="curation:rules",
        read_capability="curation:rules",
        locator=_tri_rules,
        store=stores.TriRulesStore(),
        lister=lists.tri_rules,
    ),
)

# --- Le modèle sémantique Ossie (ticket b4aeae83) -------------------------------------------

_DOMAIN_FIELD = IdentityField("domain", "Name of the domain (list_documents, kind domaine).", _SLUG)
_MEMBER_NAME = IdentityField("name", "Name, unique in its domain.", "e.g. 'client'")
KINDS = (
    *KINDS,
    WriteKind(
        name="domaine",
        description=(
            "A domain: an Ossie semantic model (its datasets, relationships and references "
            "are kinds of their own). Its label and description."
        ),
        label="domaine",
        identity=(IdentityField("name", "Name of the domain.", f"{_SLUG}, e.g. 'ventes'"),),
        standard="domaine",
        syntax="jsonpath",
        capability="curation:write",
        read_capability="curation:read",
        locator=domains.locate_domain,
        store=domains.DomainStore(),
        lister=domains.domain_items,
        identity_paths=("$.name",),
        content_schema=domains.domain_schema,
    ),
    WriteKind(
        name="dataset",
        description=(
            "A dataset of a domain: its Ossie 0.1.1 part (as the Ossie document writes it) "
            "and its extension part. Ossie stores it in the right file."
        ),
        label="dataset",
        identity=(_DOMAIN_FIELD, _MEMBER_NAME),
        standard="dataset",
        syntax="jsonpath",
        capability="curation:write",
        read_capability="curation:read",
        locator=domains.locate_dataset,
        store=domains.DatasetStore(),
        lister=domains.dataset_items,
        content_schema=domains.dataset_schema,
    ),
    WriteKind(
        name="relation_dataset",
        description=(
            "An Ossie relationship (foreign key) between two datasets of a domain, "
            "consultable both or virtual both."
        ),
        label="relation de datasets",
        identity=(_DOMAIN_FIELD, _MEMBER_NAME),
        standard="relation-dataset",
        syntax="jsonpath",
        capability="curation:write",
        read_capability="curation:read",
        locator=domains.locate_relation,
        store=domains.RelationStore(),
        lister=domains.relation_items,
        identity_paths=("$.name",),
        content_schema=domains.relation_schema,
    ),
    WriteKind(
        name="reference_dataset",
        description=(
            "An object link between two datasets without key columns (a property pointing "
            "to another object): an extension, Ossie 0.1.1 cannot express it."
        ),
        label="référence de datasets",
        identity=(_DOMAIN_FIELD, _MEMBER_NAME),
        standard="reference-dataset",
        syntax="jsonpath",
        capability="curation:write",
        read_capability="curation:read",
        locator=domains.locate_reference,
        store=domains.ReferenceStore(),
        lister=domains.reference_items,
        identity_paths=("$.name",),
        content_schema=domains.reference_schema,
    ),
)

# Les sources et leurs schémas, en lecture seule (tickets 56f3784a et 88fd2cc5).
_SOURCE_FIELD = IdentityField("source", _SOURCE_SLUG, _SLUG)
_SCHEMA_NAME = IdentityField(
    "name", "Name of the schema file, as list_documents gives it.", "the listed file name"
)
_SOURCE_ACTIONS = ("declare_source", "update_source", "delete_source")
_CONTRACT_ACTIONS = ("deposit_contract", "remove_contract")
KINDS = (
    *KINDS,
    WriteKind(
        name="source",
        description=(
            "A data source declared in the space: its manifest (label, type, connection name, "
            "parameters, relation profiles, sorting rules). Read-only: produced by "
            "declare_source, update_source and delete_source; the sorting rules are the "
            "kind 'regles_tri'."
        ),
        label="source",
        identity=(_SOURCE_FIELD,),
        standard="source",
        syntax="jsonpath",
        capability="sources:declare",
        read_capability="sources:read",
        locator=_source,
        store=stores.ReadOnlyStore("source", _SOURCE_ACTIONS),
        lister=lists.sources,
        list_filters=(lists.SOURCE_TYPE,),
        writable=False,
        produced_by=_SOURCE_ACTIONS,
    ),
    WriteKind(
        name="contrat",
        description=(
            "An OpenAPI contract deposited on an OpenAPI source, one per version: its content "
            "as deposited. Read-only: produced by deposit_contract and remove_contract."
        ),
        label="contrat",
        identity=(
            _SOURCE_FIELD,
            IdentityField("version", "Version name the contract was deposited under.", "e.g. 'v1'"),
        ),
        standard="contrat",
        syntax="jsonpath",
        capability="sources:declare",
        read_capability="sources:read",
        locator=_contract,
        store=stores.ReadOnlyStore("contrat", _CONTRACT_ACTIONS),
        lister=lists.contracts,
        writable=False,
        produced_by=_CONTRACT_ACTIONS,
    ),
    WriteKind(
        name="schema_raw",
        description=(
            "A raw schema collected from a source (one per object: table, entity, API "
            "schema). Read-only: produced by collect_source."
        ),
        label="schéma raw",
        identity=(_SOURCE_FIELD, _SCHEMA_NAME),
        standard="schema-raw",
        syntax="jsonpath",
        capability="sources:collect",
        read_capability="sources:read",
        locator=_schema_file("schema_raw", RAW_DIR),
        store=stores.ReadOnlyStore("schéma raw", ("collect_source",)),
        lister=lists.raw_schemas,
        list_filters=(lists.RAW_KIND, lists.RAW_MODULE),
        writable=False,
        produced_by=("collect_source",),
        is_schema=True,
    ),
    WriteKind(
        name="schema_asset",
        description=(
            "The schema of an asset retained from a raw object for the semantic layer. "
            "Read-only: produced by retain_asset, enrich_asset and refresh_assets."
        ),
        label="schéma d'asset",
        identity=(_SOURCE_FIELD, _SCHEMA_NAME),
        standard="schema-asset",
        syntax="jsonpath",
        capability="curation:write",
        read_capability="curation:read",
        locator=_schema_file("schema_asset", ASSETS_DIR),
        store=stores.ReadOnlyStore(
            "schéma d'asset", ("retain_asset", "enrich_asset", "refresh_assets")
        ),
        lister=lists.asset_schemas,
        writable=False,
        produced_by=("retain_asset", "enrich_asset", "refresh_assets"),
        is_schema=True,
    ),
)

_BY_NAME = {k.name: k for k in KINDS}


def write_kind(name: str) -> WriteKind:
    """Le genre de ce nom ; refus nommé s'il n'existe pas."""
    found = _BY_NAME.get(name)
    if found is None:
        raise KindError(
            "write_kind_unknown",
            "kind",
            f"Genre inconnu « {name} » ; genres possibles : {', '.join(_BY_NAME)}.",
            received=name,
            expected=list(_BY_NAME),
        )
    return found
