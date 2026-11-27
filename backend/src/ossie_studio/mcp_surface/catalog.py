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
            "Read a schema of the space. For the content of a kind of document, by kind and "
            "what types it, never a path: kind='carto_objet' + type (schema of an object of "
            "that map type), kind='referentiel_valeur' + referentiel (model of the reference "
            "list). A kind whose content depends on no type (models, processes, tags...) is "
            "described by describe_standard instead. A raw or asset schema: kind='schema_raw' "
            "or 'schema_asset' + identity {source, name} (list_documents). Returns "
            "kind ('raw', 'asset', 'referentiel', 'carto_type'), "
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
    # --- Écriture commune par genre (epic « Primitives MCP — écriture commune par genre »)
    Primitive(
        id="write_documents",
        domain="documents",
        access="write",
        capability="spaces:read",
        description=(
            "Write a list of documents of any kind (map types, objects and relationships, "
            "models, BPMN processes, reference-list models and values, tags, "
            "classifications, certifications) to the user's draft, all together or not at "
            "all (one draft change, never published). Each entry gives its kind and identity "
            "(list_write_kinds): the system computes where the document goes, never give a "
            "path. Absent document = creation (no version); present = complete replacement, "
            "version required (read_document); delete: true = deletion, version required. "
            "Content is the whole document as stored (describe_standard). Order is free: a "
            "relationship may target an object created later in the list; an object may be "
            "deleted with the relationships that use it. Each document is checked like the "
            "screen before anything is written, and needs the screen's right for its kind. On "
            "any refusal nothing is written and every error is returned (write_refused): "
            "JSONPath in your request (e.g. $.documents[2].content.proprietes.volume_jour), "
            "code, French message with what was received and expected, and 'aide' pointing to "
            "describe_standard or list_write_kinds for a format error. Returns each "
            "document's path and new version, and warnings."
        ),
        person_only=True,
        destructive=True,
    ),
    Primitive(
        id="list_documents",
        domain="documents",
        access="read",
        # Visible de qui lit un espace ; la capacité de lecture du genre (celle de
        # l'écran) est vérifiée à l'appel, sur l'espace visé.
        capability="spaces:read",
        description=(
            "List the documents of one kind (list_write_kinds): map types, objects and "
            "relationships, models, BPMN processes, reference-list models and values, tags, "
            "classifications, certifications. Filters: identity fields as partial filters "
            "(e.g. identity={type: 'application'} for carto_objet, {referentiel: 'pays'} for "
            "referentiel_valeur), 'search' (part of the name or identity, case and accents "
            "ignored; any simple property of a reference-list value), and the kind's own "
            "'filters' declared by list_write_kinds (e.g. {source: 'APP-CAISSE'}: the "
            "relationships starting from that object; {contains: KEY}: the models keeping "
            "it; {equals: {pays: 'FR'}}: reference-list values). Paginated (limit, offset, "
            "has_more). Each document: kind, identity, name, path, version (to give to "
            "write_documents / patch_document), unpublished when read from the draft, and a "
            "summary (ArchiMate type and nature of a map type or object, ends of a "
            "relationship, counts and views of a model, the value of a reference-list "
            "entry...). Read a whole document with read_document. Needs the screen's right "
            "to read that kind." + _DRAFT_NOTE
        ),
        idempotent=True,
    ),
    Primitive(
        id="read_document",
        domain="documents",
        access="read",
        # Visible de qui lit un espace ; la capacité de lecture du genre (celle de
        # l'écran) est vérifiée à l'appel, sur l'espace visé.
        capability="spaces:read",
        description=(
            "Read one document of any kind by its kind and identity (list_write_kinds), never "
            "a path: its raw content (an object for YAML/JSON; the XML text for a BPMN "
            "process; for a reference-list value, that value alone), its real path and its "
            "version, to give as is to write_documents or patch_document. Read with "
            "branch='draft' a document you are about to modify: writes go to the user's "
            "draft. Unknown identity: document_not_found. Needs the screen's right to read "
            "that kind." + _DRAFT_NOTE
        ),
        idempotent=True,
    ),
    Primitive(
        id="list_write_kinds",
        domain="documents",
        access="read",
        capability="spaces:read",
        description=(
            "List every kind of document you can write (map types, objects and "
            "relationships, models, BPMN processes, reference-list models and values, tags, "
            "classifications, certifications): for each, its identity fields and their "
            "format, the path rule (the system computes the path), the standard of its "
            "content (describe_standard), the syntax of patch_document paths (jsonpath or "
            "xpath), the frozen fields, the filters of list_documents, and the size limit per "
            "document. Start here before writing."
        ),
        idempotent=True,
    ),
    Primitive(
        id="describe_standard",
        domain="documents",
        access="read",
        capability="spaces:read",
        description=(
            "Explain a standard in full, in markdown, with valid examples: the format of each "
            "kind of document, the JSONPath and XPath syntaxes of patch_document, the common "
            "writing rules (an identity never changes, a batch is checked on its final state, "
            "size limit) and the shape of errors. Without argument, list the standards. Format "
            "errors of every tool point here through their 'aide' field."
        ),
        idempotent=True,
    ),
    Primitive(
        id="patch_document",
        domain="documents",
        access="write",
        capability="spaces:read",
        description=(
            "Modify one document of the user's draft, given its kind, identity and version "
            "(read_document), by operations set / add / remove. Paths are JSONPath (RFC 9535 "
            "singular query, e.g. '$.proprietes.volume_jour') or, for a BPMN process, XPath "
            "with the bpmn:, bpmndi:, dc:, di: and urban: prefixes; each path designates "
            "exactly one node. Frozen identity fields cannot change. The modified document is "
            "checked whole like the screen before writing; on refusal nothing is written and "
            "every error is returned with its path ($.operations[i].path for a malformed "
            "operation, otherwise the path in the document) and 'aide'. Returns the new "
            "version and warnings."
        ),
        person_only=True,
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
