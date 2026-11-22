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
    " With view='draft', items marked unpublished come from the user's draft: "
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
            "API schema), with their field count and whether they are retained as assets."
            + _DRAFT_NOTE
        ),
    ),
    Primitive(
        id="read_raw_schema",
        domain="sources",
        access="read",
        capability="sources:read",
        description="Read one raw schema of a source, as collected (JSON Schema)." + _DRAFT_NOTE,
    ),
    # --- Assets (curation)
    Primitive(
        id="list_assets",
        domain="curation",
        access="read",
        capability="curation:read",
        description=(
            "List the assets of a source: the raw objects retained (fully or partly) for the "
            "semantic layer; orphan = its raw object disappeared." + _DRAFT_NOTE
        ),
    ),
    Primitive(
        id="read_asset",
        domain="curation",
        access="read",
        capability="curation:read",
        description=(
            "Read one asset: its fields (retained or not, type, reference) and its business "
            "enrichment (business name, description, concept, tags)." + _DRAFT_NOTE
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
            "Show the raw changes of a source not reviewed yet since its last review point: "
            "retained objects touched, objects disappeared, new objects."
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
