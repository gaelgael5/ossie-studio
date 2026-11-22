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


# L'ordre fixe l'affichage. Une primitive ajoutée ici n'est donnée à aucun
# profil agent existant, sauf à ceux marqués « toutes les primitives de lecture »
# s'il s'agit d'une lecture.
CATALOG: tuple[Primitive, ...] = (
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
)

BY_ID: Mapping[str, Primitive] = {p.id: p for p in CATALOG}
ALL_PRIMITIVES: frozenset[str] = frozenset(BY_ID)
READ_PRIMITIVES: frozenset[str] = frozenset(p.id for p in CATALOG if p.access == "read")


def is_known_primitive(primitive_id: str) -> bool:
    return primitive_id in BY_ID


def catalog_payload() -> list[dict[str, object]]:
    return [
        {"id": p.id, "domain": p.domain, "access": p.access, "capability": p.capability}
        for p in CATALOG
    ]
