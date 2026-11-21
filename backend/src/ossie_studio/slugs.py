from __future__ import annotations

import re
import unicodedata

import asyncpg

# Slug « strict » des objets référencés par d'autres (connexions, sources) :
# minuscules, chiffres, tirets simples, 64 caractères au plus. C'est ce que
# l'interface produit à partir d'un libellé (frontend/src/slug.ts).
SLUG_MAX_LENGTH = 64
_STRICT_SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def is_valid_slug(slug: str) -> bool:
    return len(slug) <= SLUG_MAX_LENGTH and bool(_STRICT_SLUG.fullmatch(slug))


def slugify(label: str, *, fallback: str = "item") -> str:
    """Transforme un libellé en slug : minuscules, accents retirés, tout ce qui
    n'est pas alphanumérique devient un tiret, sans tiret en bord. Un libellé
    sans caractère exploitable retombe sur `fallback`."""
    normalized = unicodedata.normalize("NFKD", label).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", normalized.lower()).strip("-")
    return slug or fallback


# Tables qui portent un slug unique (noms de code, jamais saisis : interpolation sûre).
_SLUG_TABLES = frozenset({"git_spaces", "profiles", "oidc_connectors", "teams"})


async def free_slug(conn: asyncpg.Connection, table: str, base: str) -> str:
    """Slug libre dérivé de `base` (suffixe -2, -3… au besoin). Pour un slug
    DÉDUIT du libellé ; un slug saisi et déjà pris est refusé, jamais suffixé."""
    assert table in _SLUG_TABLES
    slug, counter = base, 2
    while await conn.fetchval(f"SELECT 1 FROM {table} WHERE slug = $1", slug):  # noqa: S608
        slug, counter = f"{base}-{counter}", counter + 1
    return slug
