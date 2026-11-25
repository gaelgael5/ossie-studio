"""Primitive MCP `get_reference_graph` : le voisinage d'un objet raw.

Le modèle trouve les références SORTANTES d'un objet dans son schéma, mais pas
les ENTRANTES (il lui faudrait relire tout le raw). Cette primitive sert les
deux sens depuis l'index de l'explorateur raw et son index inverse — la même
vérité que l'écran (vue graphe, onglet « Référencé par »), sans relire la
branche à chaque appel (ticket docflow 9aaea7a2).
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from ossie_studio.mcp_surface.branch import ObjectArgs, open_branch, source_dir
from ossie_studio.mcp_surface.core import PrimitiveContext, PrimitiveError
from ossie_studio.sources.curation import is_schema_file
from ossie_studio.sources.explorer import DEFAULT_MODE
from ossie_studio.sources.explorer_graph import MAX_DEPTH
from ossie_studio.sources.explorer_service import ensure_index, reference_graph
from ossie_studio.spaces.workdir import WorkdirError

Direction = Literal["both", "outgoing", "incoming"]


class ReferenceGraphArgs(ObjectArgs):
    depth: int = Field(
        default=1, ge=1, le=MAX_DEPTH, description="Levels to follow from the object (1 to 3)."
    )
    direction: Direction = Field(
        default="both",
        description=(
            "'outgoing' = what the object references; 'incoming' = who references the "
            "object (all sources of the space); 'both' = the two."
        ),
    )


def _node(node: dict[str, Any]) -> dict[str, Any]:
    if node.get("external"):
        return {"id": node["id"], "title": node["title"], "external": True, "level": node["level"]}
    return {
        "id": node["id"],
        "source": node["slug"],
        "path": node["file"],
        "title": node["title"],
        "kind": node["kind"],
        "level": node["level"],
    }


async def get_reference_graph(ctx: PrimitiveContext, args: ReferenceGraphArgs) -> dict[str, Any]:
    source_dir(args.source)  # slug invalide → erreur nommée
    if not is_schema_file(args.path):
        raise PrimitiveError("schema_not_found", path=args.path)
    bv = await open_branch(ctx, args, args.view)
    try:
        key, signature = await ensure_index(
            ctx.conn, bv.workdir, bv.space, bv.branch.id, bv.branch.git_ref, bv.username, bv.token
        )
    except WorkdirError as exc:
        raise PrimitiveError(exc.code) from None
    graph = await reference_graph(ctx.conn, key, DEFAULT_MODE, signature)
    built = graph.build((args.source, args.path), args.depth, args.direction)
    if built is None:
        raise PrimitiveError("schema_not_found", source=args.source, path=args.path)
    return {
        "view": bv.view,
        "center": built["center"],
        "depth": built["depth"],
        "direction": args.direction,
        "truncated": built["truncated"],
        "nodes": [_node(n) for n in built["nodes"]],
        "edges": [
            {
                "from": e["from"],
                "to": e["to"],
                "field": e["field"],
                "kind": e["kind"],
                "cardinality": e["cardinality"],
                "direction": e["direction"],
            }
            for e in built["edges"]
        ],
    }
