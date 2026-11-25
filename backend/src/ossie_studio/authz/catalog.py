"""Catalogue des capacités : la source de vérité, déclarée dans le code.

Chaque capacité a un identifiant stable « domaine:action » et appartient à un
domaine. Les libellés sont traduits côté interface (clés i18n `cap.<id>` et
`capDomain.<domaine>`). Les capacités sont des **cases indépendantes**, jamais
une échelle : aucune n'en implique une autre. Pour chaque fonction, « consulter »
(`read`) et « modifier » (`write`) sont deux capacités distinctes.

Certains domaines (sources, curation) sont déclarés d'avance : leurs écrans
viendront, mais le catalogue les porte déjà pour que les profils puissent les
cocher.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Domain:
    key: str
    capabilities: tuple[str, ...]


# L'ordre fixe l'affichage de la grille. Les ids ne changent jamais (ils sont
# vérifiés par le code des routes et stockés dans les profils).
CATALOG: tuple[Domain, ...] = (
    Domain("secrets", ("secrets:read", "secrets:write")),
    Domain("connections", ("connections:read", "connections:write")),
    Domain("oidc", ("oidc:read", "oidc:write")),
    Domain(
        "spaces",
        ("spaces:read", "spaces:create", "spaces:branch", "spaces:publish", "spaces:version"),
    ),
    # Référentiels : données de référence tenues dans l'espace (epic « Référentiels »).
    Domain("referentiels", ("referentiels:read", "referentiels:write")),
    Domain("governance", ("governance:read", "governance:write", "governance:certify")),
    Domain("sources", ("sources:read", "sources:declare", "sources:collect")),
    Domain("parsing", ("parsing:read", "parsing:manage")),
    Domain("curation", ("curation:read", "curation:write", "curation:review", "curation:rules")),
    Domain("profiles", ("profiles:read", "profiles:write")),
    Domain("users", ("users:read", "users:write")),
    Domain("agent_profiles", ("agent_profiles:read", "agent_profiles:write")),
    Domain("api_keys", ("api_keys:read", "api_keys:write")),
    # Toutes les connexions OAuth d'agents (chacun gère toujours les siennes).
    Domain("agent_connections", ("agent_connections:read", "agent_connections:write")),
    # Journal d'audit et page Historique (ticket cf4434da) : lecture seule, aucune
    # capacité d'écriture — aucune route ne modifie une ligne d'audit.
    Domain("audit", ("audit:read",)),
)

ALL_CAPABILITIES: frozenset[str] = frozenset(
    cap for domain in CATALOG for cap in domain.capabilities
)

# Capacités « déclinables par espace » : une équipe rattachée à un espace peut
# les donner sur cet espace seulement. Ce sont les gestes faits DANS un espace
# (sa lecture, ses branches, sa publication, ses versions, et le travail qu'il
# porte : référentiels, sources, curation, gouvernance). Toutes les autres sont globales :
# administration (secrets, connexions, OIDC, profils, utilisateurs, parseurs,
# profils agents, clés d'API, connexions d'agents) et création d'un espace, qui
# ne se fait pas depuis l'intérieur d'un espace.
# Une capacité ajoutée au catalogue déclare ici sa portée.
SPACE_SCOPED: frozenset[str] = frozenset(
    {"spaces:read", "spaces:branch", "spaces:publish", "spaces:version"}
    | {
        cap
        for domain in CATALOG
        if domain.key in ("referentiels", "sources", "curation", "governance")
        for cap in domain.capabilities
    }
)


def is_known_capability(capability: str) -> bool:
    return capability in ALL_CAPABILITIES


def catalog_payload() -> list[dict[str, object]]:
    return [{"domain": d.key, "capabilities": list(d.capabilities)} for d in CATALOG]
