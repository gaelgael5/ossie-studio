"""Catalogue des primitives MCP : la source de vérité, déclarée dans le code.

Pendant du catalogue des capacités (`authz/catalog.py`). Chaque primitive a un
identifiant stable (le nom de l'outil vu par l'agent, jamais renommé : il est
stocké dans les profils agents), un domaine, la mention lecture ou écriture, et
**la capacité humaine qu'elle suppose**. Cette dernière rend calculable la borne
d'un agent connecté pour une personne : il ne fait jamais plus qu'elle.

Règle de correspondance : une primitive suppose la même capacité que l'écran
équivalent de l'IHM.

Les libellés d'écran sont traduits côté interface (clés i18n `mcpPrim.<id>`) ;
la description ci-dessous est celle que lit l'agent. Les arguments et leur
exécution vivent dans `primitives.py` (un test vérifie que les deux concordent).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

Access = Literal["read", "write"]


@dataclass(frozen=True)
class Primitive:
    id: str
    domain: str
    access: Access
    capability: str
    description: str
    # Propre au brouillon d'une personne : sans objet pour une clé d'API (aucune
    # personne derrière), donc jamais proposé à une clé. Toute écriture l'est.
    person_only: bool = False
    # Annoncés au client MCP (annotations) : un geste destructif lui fait demander
    # confirmation à la personne ; idempotent = le rejeu ne change rien.
    destructive: bool = False
    idempotent: bool = False


# L'ordre fixe l'affichage. Une primitive ajoutée ici n'est donnée à aucun
# profil agent existant, sauf à ceux marqués « toutes les primitives de lecture »
# s'il s'agit d'une lecture.
_DRAFT_NOTE = (
    " With branch='draft', items marked unpublished come from the user's draft: "
    "not validated, not yet published."
)

CATALOG: tuple[Primitive, ...] = (
    # --- Se repérer
    Primitive(
        id="list_spaces",
        domain="spaces",
        access="read",
        capability="spaces:read",
        description=(
            "List the Git spaces you can see, each with its branches. "
            "Use the returned space and branch ids with the other tools."
        ),
    ),
    Primitive(
        id="list_versions",
        domain="spaces",
        access="read",
        capability="spaces:read",
        description="List the published versions (numbered snapshots) of a branch.",
    ),
    # --- Le brouillon de la personne
    Primitive(
        id="get_my_draft",
        domain="drafts",
        access="read",
        capability="spaces:read",
        description=(
            "Show what the user changed on a branch and has not published yet (created, "
            "modified or deleted files), and which of them also changed on the published side "
            "since. Only when acting for a user."
        ),
        person_only=True,
    ),
    Primitive(
        id="get_publish_readiness",
        domain="drafts",
        access="read",
        capability="spaces:read",
        description=(
            "Tell whether the user's draft on a branch can be published now, and if not why "
            "(conflicts, impacts to check, validation errors, missing credential, no right). "
            "Only when acting for a user."
        ),
        person_only=True,
    ),
    # --- Préparer la publication du brouillon (rien ne se publie ici)
    Primitive(
        id="get_merge_conflicts",
        domain="drafts",
        access="read",
        capability="spaces:read",
        description=(
            "List the objects of the user's draft that changed on the published side and need a "
            "decision: for each conflicting property, the base, published ('theirs') and draft "
            "('mine') values; also the objects auto_merge can merge alone. Give a path to get "
            "that object's three full versions and the proposed merge."
        ),
        person_only=True,
    ),
    Primitive(
        id="resolve_merge",
        domain="drafts",
        access="write",
        capability="spaces:read",
        description=(
            "Resolve one object in conflict: per conflicting JSON pointer keep 'theirs', 'mine' "
            "or a value; or, for an object deleted on one side, accept the deletion or not. "
            "Every conflict must be decided. Keeping the published version discards the user's "
            "change. Written to the user's draft (not published)."
        ),
        person_only=True,
        destructive=True,
    ),
    Primitive(
        id="auto_merge",
        domain="drafts",
        access="write",
        capability="spaces:read",
        description=(
            "Merge into the user's draft every object changed on the published side that merges "
            "without conflict; the others stay in conflict (see get_merge_conflicts)."
        ),
        person_only=True,
        idempotent=True,
    ),
    Primitive(
        id="list_impacts",
        domain="drafts",
        access="read",
        capability="spaces:read",
        description=(
            "List the impacts of the user's draft: objects that read what the draft changed, "
            "verified or not. Every impact must be verified before publishing."
        ),
        person_only=True,
    ),
    Primitive(
        id="verify_impact",
        domain="drafts",
        access="write",
        capability="spaces:read",
        description=(
            "Attest that an impact was checked and needs no change. Valid while the objects it "
            "reads do not change; recorded in the publication commit as made by you, the agent."
        ),
        person_only=True,
        idempotent=True,
    ),
    Primitive(
        id="get_validation_log",
        domain="drafts",
        access="read",
        capability="spaces:read",
        description=(
            "Validation log of the branch as the user sees it (with the draft): warnings and "
            "errors, whether they block publishing, accepted or pre-existing."
        ),
        person_only=True,
    ),
    Primitive(
        id="accept_validation_warning",
        domain="drafts",
        access="write",
        capability="spaces:read",
        description=(
            "Accept a validation warning so it no longer blocks; an error cannot be accepted "
            "(fix the object). Valid while the object does not change; recorded in the "
            "publication commit as made by you, the agent."
        ),
        person_only=True,
        idempotent=True,
    ),
    # --- Lire un schéma, quel que soit son domaine (ticket 06ef8679)
    Primitive(
        id="read_schema",
        domain="spaces",
        access="read",
        # Visible de qui lit un espace ; la capacité du domaine désigné par le
        # chemin (sources, curation, référentiels, cartographie) est vérifiée à
        # l'appel, sur l'espace visé.
        capability="spaces:read",
        description=(
            "Read any schema of the space by its path, as the lists give it ('schema_path' "
            "of list_raw_schemas and list_assets, 'path' of list_referentiels and "
            "list_carto_types). Returns kind ('raw', 'asset', 'referentiel', 'carto_type'), "
            "version (content id in the branch read), unpublished, schema and extras. By "
            "default (resolved=true) the effective schema: a raw schema as collected "
            "(references resolved); an asset merged with its human work, extras carrying "
            "its business enrichment (business name, description, concept, tags per field) "
            "and status (orphan); a reference data set model with its key under 'x-key'; "
            "a cartography type as the schema of one object file (key, name, documentation, "
            "source and target for a relationship, then 'proprietes': ArchiMate attributes "
            "and added properties), extras carrying the ArchiMate type, layer and nature. "
            "resolved=false: the file as written. Needs the same right as the screen of "
            "that domain." + _DRAFT_NOTE
        ),
        idempotent=True,
    ),
    # --- Sources et schémas bruts
    Primitive(
        id="list_sources",
        domain="sources",
        access="read",
        capability="sources:read",
        description=(
            "List the data sources declared on a branch (type, connection, parameters)."
            + _DRAFT_NOTE
        ),
    ),
    Primitive(
        id="list_raw_schemas",
        domain="sources",
        access="read",
        capability="sources:read",
        description=(
            "List the raw schemas collected from a source (one per object: table, entity, "
            "API schema), with their field count, whether they are retained as assets, and "
            "schema_path to read one with read_schema." + _DRAFT_NOTE
        ),
    ),
    Primitive(
        id="get_reference_graph",
        domain="sources",
        access="read",
        capability="sources:read",
        description=(
            "Neighbourhood of a raw object in the reference graph: what it references "
            "(direction='outgoing'), who references it across all sources of the space "
            "(direction='incoming'), or both, over 1 to 3 levels. Edges carry the field, the "
            "kind (field, list, map, inherits, foreign_key) and the cardinality. Use it to find "
            "the objects that reference an object instead of reading every schema. Limits: a "
            "reference left unresolved (external type) has no incoming side; across two "
            "sources only SQL foreign keys are linked. Truncated beyond 40 nodes." + _DRAFT_NOTE
        ),
        idempotent=True,
    ),
    # --- Cartographie applicative (modules, fonctions, points d'entrée ; ticket 72ce32b3)
    Primitive(
        id="list_modules",
        domain="sources",
        access="read",
        capability="sources:read",
        description=(
            "List the modules of a source (one per project file: .csproj, .sqlproj, Gradle or "
            "Maven module, package.json, pyproject.toml) with their nature (library, web, "
            "worker, console, test, sql), targets, language, project file, counts (classes, "
            "entry points, public methods) and dependencies in both directions (depends_on, "
            "used_by; a project outside the source is external). Empty list if the source has "
            "no module. Truncated beyond 200." + _DRAFT_NOTE
        ),
        idempotent=True,
    ),
    Primitive(
        id="get_module",
        domain="sources",
        access="read",
        capability="sources:read",
        description=(
            "One module of a source (path or title from list_modules): the raw objects that "
            "belong to it (nearest project file up the tree), its free functions (minimal API, "
            "module-level functions) and its entry points (HTTP verb and route, background "
            "task, message consumer; method and owner; contract status: linked, absent or "
            "not_compared). Truncated beyond 200 items per list." + _DRAFT_NOTE
        ),
        idempotent=True,
    ),
    Primitive(
        id="get_functions",
        domain="sources",
        access="read",
        capability="sources:read",
        description=(
            "Methods of a raw object (a class): name, parameters and types, return type, "
            "visibility (public, internal, protected; private methods are not collected), "
            "static, async, and the entry point when the method is one (HTTP verb and route). "
            "Read from the object's .extend.yaml; its schema stays standard JSON Schema. "
            "Truncated beyond 200." + _DRAFT_NOTE
        ),
        idempotent=True,
    ),
    Primitive(
        id="list_entry_points",
        domain="sources",
        access="read",
        capability="sources:read",
        description=(
            "Entry points of a source or of one module: HTTP endpoints (verb, route), "
            "background tasks, message consumers, with the method and owner. "
            "contract_status='absent' answers 'which routes of the code are missing from the "
            "OpenAPI contract?'; 'linked' returns those matched to a contract operation. "
            "Truncated beyond 200." + _DRAFT_NOTE
        ),
        idempotent=True,
    ),
    # --- Assets (curation)
    Primitive(
        id="list_assets",
        domain="curation",
        access="read",
        capability="curation:read",
        description=(
            "List the assets of a source: the raw objects retained (fully or partly) for the "
            "semantic layer; orphan = its raw object disappeared. schema_path: to read one "
            "with read_schema." + _DRAFT_NOTE
        ),
    ),
    Primitive(
        id="get_tri_rules",
        domain="curation",
        access="read",
        capability="curation:rules",
        description="Read the triage rules of a source (patterns that set noise aside).",
    ),
    Primitive(
        id="get_review",
        domain="curation",
        access="read",
        capability="curation:review",
        description=(
            "Show what needs a decision on a source, compared with the published branch: "
            "retained objects whose raw changed or no longer matches the asset, retained "
            "objects that disappeared from raw, new objects of the user's draft."
        ),
    ),
    # --- Écriture : curation (toujours dans le brouillon de la personne)
    Primitive(
        id="retain_asset",
        domain="curation",
        access="write",
        capability="curation:write",
        description=(
            "Retain a raw object as an asset of the semantic layer, whole or only some fields. "
            "Written to the user's draft (not published)."
        ),
        person_only=True,
        idempotent=True,
    ),
    Primitive(
        id="remove_asset",
        domain="curation",
        access="write",
        capability="curation:write",
        description=(
            "Remove an asset (its raw object stays). Written to the user's draft (not published)."
        ),
        person_only=True,
        destructive=True,
        idempotent=True,
    ),
    Primitive(
        id="enrich_asset",
        domain="curation",
        access="write",
        capability="curation:write",
        description=(
            "Set the business enrichment of an asset and of its fields: business name, "
            "description, concept, tags. Only the given keys change; an empty value removes "
            "the key. Written to the user's draft (not published)."
        ),
        person_only=True,
        idempotent=True,
    ),
    Primitive(
        id="refresh_assets",
        domain="curation",
        access="write",
        capability="curation:write",
        description=(
            "Refresh the retained assets of a source from their raw objects (all, or the given "
            "ones): structure from the raw, enrichment kept. Written to the user's draft."
        ),
        person_only=True,
    ),
    Primitive(
        id="set_tri_rules",
        domain="curation",
        access="write",
        capability="curation:rules",
        description=(
            "Replace the triage rules of a source (patterns that set noise aside). Give the "
            "complete list; an empty list removes all rules. Written to the user's draft."
        ),
        person_only=True,
        idempotent=True,
    ),
    Primitive(
        id="apply_review",
        domain="curation",
        access="write",
        capability="curation:review",
        description=(
            "Apply decisions to the review read with get_review on branch='draft' (same "
            "head_sha): refresh changed fields, add appeared fields, remove disappeared fields "
            "or objects, retain new objects. Undecided changes are left as they are. Every "
            "decision must match the review. Written to the user's draft."
        ),
        person_only=True,
        destructive=True,
    ),
    # --- Sources (lectures, puis écritures dans le brouillon de la personne)
    Primitive(
        id="list_contracts",
        domain="sources",
        access="read",
        capability="sources:read",
        description="List the OpenAPI contract versions deposited on an OpenAPI source.",
    ),
    Primitive(
        id="get_collect_status",
        domain="sources",
        access="read",
        capability="sources:collect",
        description=(
            "State of the last collection of a source launched by the user (running, done, "
            "failed), with its progress and report."
        ),
        person_only=True,
    ),
    Primitive(
        id="declare_source",
        domain="sources",
        access="write",
        capability="sources:declare",
        description=(
            "Declare a data source on a branch: Git repository, database, or OpenAPI (contracts "
            "deposited later). Same checks as the screen. Written to the user's draft."
        ),
        person_only=True,
    ),
    Primitive(
        id="update_source",
        domain="sources",
        access="write",
        capability="sources:declare",
        description=(
            "Change the label, connection or parameters of a source (its triage rules are "
            "kept). Written to the user's draft."
        ),
        person_only=True,
        idempotent=True,
    ),
    Primitive(
        id="delete_source",
        domain="sources",
        access="write",
        capability="sources:declare",
        description=(
            "Delete a source from the branch, with its downloaded code and collection index. "
            "Written to the user's draft."
        ),
        person_only=True,
        destructive=True,
    ),
    Primitive(
        id="collect_source",
        domain="sources",
        access="write",
        capability="sources:collect",
        description=(
            "Collect a source: read it and write its raw schemas to the user's draft. Long task: "
            "returns the report if it finishes quickly, otherwise its state; then follow it "
            "with get_collect_status. A collection already running is not started again."
        ),
        person_only=True,
    ),
    Primitive(
        id="deposit_contract",
        domain="sources",
        access="write",
        capability="sources:declare",
        description=(
            "Deposit an OpenAPI contract (YAML or JSON text) on an OpenAPI source, under a "
            "version name. Never replaces an existing version: remove it first. Written to "
            "the user's draft."
        ),
        person_only=True,
    ),
    Primitive(
        id="remove_contract",
        domain="sources",
        access="write",
        capability="sources:declare",
        description=(
            "Remove a contract version from an OpenAPI source. Written to the user's draft."
        ),
        person_only=True,
        destructive=True,
    ),
    # --- Référentiels (lecture de la version publiée, jamais d'un brouillon)
    Primitive(
        id="list_referentiels",
        domain="referentiels",
        access="read",
        capability="referentiels:read",
        description=(
            "List the reference data sets (référentiels) of a space: lists of business keys and "
            "values maintained in Ossie-studio (countries, delivery modes, codes...), as "
            "published. Each has a key of one or more properties (a composite key needs all of "
            "them). Returns name, title, key properties, value count, path of the model (to read "
            "it with read_schema) and the published commit."
        ),
    ),
    Primitive(
        id="search_referentiel_values",
        domain="referentiels",
        access="read",
        capability="referentiels:read",
        description=(
            "Search the values of a published reference data set, sorted by key, with equality "
            "filters on simple top-level properties. The result is bounded: 'has_more' tells "
            "whether more values remain (use offset for the next page)."
        ),
    ),
    Primitive(
        id="get_referentiel_value",
        domain="referentiels",
        access="read",
        capability="referentiels:read",
        description=(
            "Get one value of a published reference data set by its key; for a composite key, "
            "give every key property."
        ),
    ),
    # --- Cartographie : types et objets ArchiMate (ticket 41bf680e)
    Primitive(
        id="list_carto_types",
        domain="cartographie",
        access="read",
        capability="cartographie:read",
        description=(
            "List the object types of the space's business cartography: each is an ArchiMate 3.2 "
            "type plus a name given by the people (slug, name, ArchiMate type, layer, nature "
            "'object' or 'relation', object count, path of its schema). With catalogue=true, "
            "also every ArchiMate 3.2 type of the catalogue, used or not." + _DRAFT_NOTE
        ),
        idempotent=True,
    ),
    Primitive(
        id="list_carto_objects",
        domain="cartographie",
        access="read",
        capability="cartographie:read",
        description=(
            "List the objects and relationships of the cartography (a relationship is an object "
            "with a source and a target), optionally filtered by type (a space type slug, or an "
            "ArchiMate type such as 'ApplicationComponent'), nature ('object' or 'relation') "
            "and name (part of the name or key, case and accents ignored). Summaries: key, "
            "name, type, ArchiMate type, nature, path, and source and target (with their path) "
            "for a relationship. "
            "Paged: 'total' and 'has_more' (use offset for the next page). Use it before "
            "creating anything, to find what already exists." + _DRAFT_NOTE
        ),
        idempotent=True,
    ),
    Primitive(
        id="get_carto_object",
        domain="cartographie",
        access="read",
        capability="cartographie:read",
        description=(
            "Read one object or relationship of the cartography by its path, its key, or its "
            "type and key (same result): name, type, "
            "ArchiMate type, nature, documentation (markdown), properties (ArchiMate attributes "
            "as ArchiMate values, with their French label in property_labels), source and target "
            "for a relationship, and its incoming and outgoing relationships (key, type, "
            "direction, the other end) to explore step by step, and the models that keep it "
            "(slug, name, path). Returns its path and its version (content id in the branch "
            "read), to give the write primitives to replace, modify or delete it." + _DRAFT_NOTE
        ),
        idempotent=True,
    ),
    # --- Cartographie : modèles et vues (ticket be89037a)
    Primitive(
        id="list_carto_models",
        domain="cartographie",
        access="read",
        capability="cartographie:read",
        description=(
            "List the models (ArchiMate diagrams) of the cartography: slug, name, description, "
            "number of objects and relationships kept, views (name and ArchiMate viewpoint), "
            "rules file, number of problems, path of the model file. With contains (an object "
            "or relationship key), only the models that keep it: where does an object appear."
            + _DRAFT_NOTE
        ),
        idempotent=True,
    ),
    Primitive(
        id="get_carto_model",
        domain="cartographie",
        access="read",
        capability="cartographie:read",
        description=(
            "Read one model by its slug or the path of its file (same result), with its version "
            "(content id in the branch read) and the paths of its rules and graphic files: the "
            "objects and relationships it keeps (key, name, "
            "type, ArchiMate type; source and target for a relationship), its views with their "
            "nodes (an object by its key and name, a note, a frame; nested nodes) and "
            "connections (a relationship between two nodes). Never the drawing: no coordinate. "
            "Use get_carto_object on a key for its details." + _DRAFT_NOTE
        ),
        idempotent=True,
    ),
    # --- Gouvernance
    Primitive(
        id="list_tags",
        domain="governance",
        access="read",
        capability="governance:read",
        description="List the governed tags of a branch (owner, description, links).",
    ),
    Primitive(
        id="list_classifications",
        domain="governance",
        access="read",
        capability="governance:read",
        description="List the classification schemes of a branch and their levels.",
    ),
    Primitive(
        id="list_certifications",
        domain="governance",
        access="read",
        capability="governance:read",
        description=(
            "List the certifications of a branch: name, rank, the tag that links it to assets, "
            "restricted or not." + _DRAFT_NOTE
        ),
    ),
    # --- Écriture : gouvernance (toujours dans le brouillon de la personne). Les
    # modifications sont partielles : seuls les champs donnés changent.
    Primitive(
        id="create_tag",
        domain="governance",
        access="write",
        capability="governance:write",
        description=(
            "Create a governed tag (identifier like 'gouvernance/pii', owner, description, "
            "links, properties). Written to the user's draft (not published)."
        ),
        person_only=True,
    ),
    Primitive(
        id="update_tag",
        domain="governance",
        access="write",
        capability="governance:write",
        description=(
            "Change a governed tag. Only the given fields change; links and properties replace "
            "the whole map when given. Written to the user's draft (not published)."
        ),
        person_only=True,
        idempotent=True,
    ),
    Primitive(
        id="delete_tag",
        domain="governance",
        access="write",
        capability="governance:write",
        description="Delete a governed tag. Written to the user's draft (not published).",
        person_only=True,
        destructive=True,
    ),
    Primitive(
        id="create_classification",
        domain="governance",
        access="write",
        capability="governance:write",
        description=(
            "Create a classification scheme (e.g. Sensitivity), optionally with its levels. "
            "Written to the user's draft (not published)."
        ),
        person_only=True,
    ),
    Primitive(
        id="update_classification",
        domain="governance",
        access="write",
        capability="governance:write",
        description=(
            "Change a classification scheme (not its levels). Only the given fields change. "
            "Written to the user's draft (not published)."
        ),
        person_only=True,
        idempotent=True,
    ),
    Primitive(
        id="delete_classification",
        domain="governance",
        access="write",
        capability="governance:write",
        description=(
            "Delete a classification scheme with all its levels. Written to the user's draft "
            "(not published)."
        ),
        person_only=True,
        destructive=True,
    ),
    Primitive(
        id="create_level",
        domain="governance",
        access="write",
        capability="governance:write",
        description=(
            "Add a level at the end of a classification scheme (name and short name unique in "
            "the scheme). Written to the user's draft (not published)."
        ),
        person_only=True,
    ),
    Primitive(
        id="update_level",
        domain="governance",
        access="write",
        capability="governance:write",
        description=(
            "Change a level of a classification scheme. Only the given fields change. Written "
            "to the user's draft (not published)."
        ),
        person_only=True,
        idempotent=True,
    ),
    Primitive(
        id="delete_level",
        domain="governance",
        access="write",
        capability="governance:write",
        description=(
            "Delete a level of a classification scheme. Written to the user's draft "
            "(not published)."
        ),
        person_only=True,
        destructive=True,
    ),
    Primitive(
        id="reorder_levels",
        domain="governance",
        access="write",
        capability="governance:write",
        description=(
            "Set the order of the levels of a classification scheme: give all its level ids, "
            "each once. Written to the user's draft (not published)."
        ),
        person_only=True,
        idempotent=True,
    ),
    Primitive(
        id="create_certification",
        domain="governance",
        access="write",
        capability="governance:write",
        description=(
            "Create a certification (e.g. Gold), linked to assets by its own tag. Written to "
            "the user's draft (not published)."
        ),
        person_only=True,
    ),
    Primitive(
        id="update_certification",
        domain="governance",
        access="write",
        capability="governance:write",
        description=(
            "Change a certification (its slug stays). Only the given fields change. Written to "
            "the user's draft (not published)."
        ),
        person_only=True,
        idempotent=True,
    ),
    Primitive(
        id="delete_certification",
        domain="governance",
        access="write",
        capability="governance:write",
        description="Delete a certification. Written to the user's draft (not published).",
        person_only=True,
        destructive=True,
    ),
)

BY_ID: Mapping[str, Primitive] = {p.id: p for p in CATALOG}
ALL_PRIMITIVES: frozenset[str] = frozenset(BY_ID)
READ_PRIMITIVES: frozenset[str] = frozenset(p.id for p in CATALOG if p.access == "read")
PERSON_ONLY: frozenset[str] = frozenset(p.id for p in CATALOG if p.person_only)


def is_known_primitive(primitive_id: str) -> bool:
    return primitive_id in BY_ID


def catalog_payload() -> list[dict[str, object]]:
    return [
        {
            "id": p.id,
            "domain": p.domain,
            "access": p.access,
            "capability": p.capability,
            "person_only": p.person_only,
        }
        for p in CATALOG
    ]
