"""Index de l'explorateur raw, partagé par l'API de l'écran et la surface MCP.

L'index (objets, documents, tags de chaque source) est un cache dérivé de Git,
rangé en base par (espace, branche, personne, vue) et reconstruit seulement
quand sa signature change (tête publiée de la branche + révision du brouillon +
format de l'index + version des règles fournies). Le graphe des références et
son index inverse se bâtissent une fois par état de l'index.

Une clé d'API n'a personne derrière : sa vue publiée ne dépend de personne, mais
la table de l'index est rattachée à un utilisateur (clé étrangère). Pour elle,
l'index est gardé en mémoire, par état de la branche — pas de migration.
"""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

import asyncpg
import structlog

from ossie_studio.drafts.workdir import DraftWorkdir
from ossie_studio.governance.builtin_classification_rules import (
    BUILTIN_CLASSIFICATION_RULES_VERSION,
)
from ossie_studio.governance.builtin_tag_rules import BUILTIN_RULES_VERSION
from ossie_studio.governance.classification_rules import (
    active_classification_rules,
    read_classification_removals,
)
from ossie_studio.governance.repo import read_schemes
from ossie_studio.governance.tag_rules import active_rules, read_tag_removals
from ossie_studio.models import GitSpace
from ossie_studio.sources.explorer import (
    INDEX_FORMAT,
    SourceEntries,
    collect_source_entries,
    head_commit,
    list_source_slugs,
)
from ossie_studio.sources.explorer_classes import apply_classification_rules
from ossie_studio.sources.explorer_graph import ReferenceGraph
from ossie_studio.sources.explorer_store import (
    IndexKey,
    db_all_source_entries,
    db_index_signature,
    db_replace_index,
)
from ossie_studio.sources.explorer_tags import apply_tag_rules

log = structlog.get_logger(__name__)

# Identifiant « personne » d'une clé d'API (cf. mcp_surface/branch.py).
NO_PERSON = uuid.UUID(int=0)

# Une reconstruction à la fois par index : dix clics pendant qu'un index se
# reconstruit attendent celle en cours au lieu d'en lancer dix.
_BUILD_LOCKS: dict[IndexKey, asyncio.Lock] = {}

# Graphe (avec l'index inverse) par (index, signature, mode).
_GRAPH_CACHE: dict[tuple[IndexKey, str, str], ReferenceGraph] = {}
_GRAPH_CACHE_MAX = 32

# Clé d'API : sources de la vue publiée, par (espace, branche, signature).
_MEMORY_INDEX: dict[tuple[uuid.UUID, uuid.UUID, str], list[SourceEntries]] = {}
_MEMORY_INDEX_MAX = 8


def _read_sources(wc: Path) -> list[SourceEntries]:
    # Les tags se calculent ici, sur toutes les sources à la fois (une chaîne
    # d'héritage peut en traverser deux) ; les règles sont dans le dépôt, donc
    # dans la signature : les modifier reconstruit l'index.
    # Badges « nouveau » / « modifié » : écart avec la tête publiée, HEAD de la
    # copie (ticket docflow aad1d3c6). Dans la vue publiée, aucun écart.
    published = head_commit(wc)
    tagged = apply_tag_rules(
        [
            collect_source_entries(wc, slug, published_base=published)
            for slug in list_source_slugs(wc)
        ],
        active_rules(wc),
        {(r.object, r.tag) for r in read_tag_removals(wc)},
    )
    # Classification des champs (ticket cfc33d51) : après les tags, que la
    # condition « tag » regarde ; règles et schémas sont dans le dépôt, donc
    # dans la signature de l'index.
    # Retraits mémorisés (ticket 77ce0d79) : dans le dépôt, donc dans la signature.
    return apply_classification_rules(
        tagged,
        active_classification_rules(wc),
        read_schemes(wc),
        {r.key for r in read_classification_removals(wc)},
    )


async def ensure_index(
    conn: asyncpg.Connection,
    workdir: DraftWorkdir,
    space: GitSpace,
    branch_id: uuid.UUID,
    branch_ref: str,
    username: str,
    token: str | None,
) -> tuple[IndexKey, str]:
    """Clé d'un index à jour et sa signature. Le cas courant — rien n'a bougé —
    ne lit que la base. Lève WorkdirError (à traduire par l'appelant)."""
    view, raw_signature = await workdir.view_signature(
        space.id, branch_ref, space.repo_url, username, token
    )
    # Les règles fournies par Ossie font partie du produit : leur version entre
    # dans la signature (une nouvelle livraison de règles reconstruit l'index).
    signature = (
        f"v{INDEX_FORMAT}.{BUILTIN_RULES_VERSION}.{BUILTIN_CLASSIFICATION_RULES_VERSION}"
        f":{raw_signature}"
    )
    key = IndexKey(space_id=space.id, branch_id=branch_id, user_id=workdir.user_id, view=view)
    if workdir.user_id == NO_PERSON:
        await _ensure_memory_index(
            workdir, space, branch_id, branch_ref, username, token, signature
        )
        return key, signature
    if await db_index_signature(conn, key) == signature:
        return key, signature
    lock = _BUILD_LOCKS.setdefault(key, asyncio.Lock())
    async with lock:
        if await db_index_signature(conn, key) == signature:
            return key, signature
        sources = await workdir.read_branch(
            space.id, branch_ref, space.repo_url, username, token, _read_sources
        )
        await db_replace_index(conn, key, signature, sources)
        log.info(
            "raw_explorer_index_rebuilt",
            space_id=str(space.id),
            branch=branch_ref,
            view=view,
            sources=len(sources),
            objects=sum(len(s.objects) for s in sources),
        )
    return key, signature


async def _ensure_memory_index(
    workdir: DraftWorkdir,
    space: GitSpace,
    branch_id: uuid.UUID,
    branch_ref: str,
    username: str,
    token: str | None,
    signature: str,
) -> None:
    mem_key = (space.id, branch_id, signature)
    if mem_key in _MEMORY_INDEX:
        return
    lock = _BUILD_LOCKS.setdefault(
        IndexKey(space_id=space.id, branch_id=branch_id, user_id=NO_PERSON, view="published"),
        asyncio.Lock(),
    )
    async with lock:
        if mem_key in _MEMORY_INDEX:
            return
        sources = await workdir.read_branch(
            space.id, branch_ref, space.repo_url, username, token, _read_sources
        )
        if len(_MEMORY_INDEX) >= _MEMORY_INDEX_MAX:
            _MEMORY_INDEX.clear()
        _MEMORY_INDEX[mem_key] = sources
        log.info(
            "raw_explorer_memory_index_built",
            space_id=str(space.id),
            branch=branch_ref,
            sources=len(sources),
        )


async def space_entries(
    conn: asyncpg.Connection, key: IndexKey, signature: str
) -> list[SourceEntries]:
    """Toutes les sources de l'index (en base, ou en mémoire pour une clé d'API)."""
    if key.user_id == NO_PERSON:
        return _MEMORY_INDEX.get((key.space_id, key.branch_id, signature), [])
    return await db_all_source_entries(conn, key)


async def reference_graph(
    conn: asyncpg.Connection, key: IndexKey, mode: str, signature: str | None = None
) -> ReferenceGraph:
    """Graphe des références de l'espace (avec l'index inverse), gardé tant que
    l'index ne change pas : un clic ne relit pas toutes les sources."""
    if signature is None:
        signature = await db_index_signature(conn, key) or ""
    cache_key = (key, signature, mode)
    cached = _GRAPH_CACHE.get(cache_key)
    if cached is not None:
        return cached
    graph = ReferenceGraph(await space_entries(conn, key, signature), mode)
    if len(_GRAPH_CACHE) >= _GRAPH_CACHE_MAX:
        _GRAPH_CACHE.clear()
    _GRAPH_CACHE[cache_key] = graph
    return graph
