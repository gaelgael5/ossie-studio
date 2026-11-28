"""Serveur MCP d'Ossie-studio (SDK officiel, Streamable HTTP, sans état).

Sans état : aucune session MCP n'est gardée côté serveur, chaque requête HTTP
est autonome. Les droits sont donc relus à chaque requête, et plusieurs
instances de l'application peuvent servir la surface sans partage.

Protection contre le rebinding DNS du SDK laissée désactivée : elle protège les
serveurs qu'un navigateur peut atteindre sans secret ; ici, toute requête doit
porter un jeton Bearer, qu'une page tierce ne peut pas fabriquer.
"""

from __future__ import annotations

import json
import time
import uuid
from importlib.metadata import version
from typing import Any

import structlog
from mcp.server import Server, ServerRequestContext
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.shared.exceptions import MCPError
from mcp.types import (
    INVALID_PARAMS,
    CallToolRequestParams,
    CallToolResult,
    ListToolsResult,
    PaginatedRequestParams,
    TextContent,
    Tool,
    ToolAnnotations,
)
from pydantic import ValidationError
from starlette.applications import Starlette

from ossie_studio.mcp_surface.authz import allowed_primitives, authorize, caller_user
from ossie_studio.mcp_surface.branch import check_branch
from ossie_studio.mcp_surface.caller import McpCaller, current_caller
from ossie_studio.mcp_surface.catalog import BY_ID, CATALOG, Primitive
from ossie_studio.mcp_surface.core import PrimitiveContext, PrimitiveError
from ossie_studio.mcp_surface.primitives import IMPLEMENTATIONS
from ossie_studio.mcp_surface.write_guide import aide_for

log = structlog.get_logger(__name__)

_INSTRUCTIONS = (
    "Ossie-studio builds a semantic layer from data sources versioned in Git spaces. "
    "Start with list_spaces to find a space and a branch, then use their ids with the other "
    "tools. Reads show the published content by default; pass branch='draft' to also see the "
    "unpublished draft of the user you act for (items marked unpublished are not validated "
    "yet). Writes always go to that user's draft."
)


def _caller() -> McpCaller:
    caller = current_caller.get()
    if caller is None:  # la garde l'a posé ; son absence est un défaut de montage
        raise MCPError(INVALID_PARAMS, "unauthenticated")
    return caller


def _result(payload: dict[str, Any], *, error: bool = False) -> CallToolResult:
    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps(payload, ensure_ascii=False))],
        structured_content=payload,
        is_error=error,
    )


def _refusal(code: str, **detail: object) -> CallToolResult:
    return _result({"error": code, **detail}, error=True)


def _primitive_refusal(primitive: str, code: str, detail: dict[str, Any]) -> CallToolResult:
    """Le refus d'une primitive, avec — pour une erreur de format — le renvoi vers ce
    qui en décrit la règle (`aide`), posé ici une fois pour toutes les primitives."""
    aide = aide_for(BY_ID[primitive].domain, code, detail)
    return _refusal(code, **detail, **({"aide": aide} if aide else {}))


def _annotations(primitive: Primitive) -> ToolAnnotations:
    if primitive.access == "read":
        return ToolAnnotations(read_only_hint=True)
    return ToolAnnotations(
        read_only_hint=False,
        destructive_hint=primitive.destructive,
        idempotent_hint=primitive.idempotent,
    )


def build_session_manager(app: Starlette) -> StreamableHTTPSessionManager:
    async def on_list_tools(
        _ctx: ServerRequestContext[Any], _params: PaginatedRequestParams | None
    ) -> ListToolsResult:
        caller = _caller()
        async with app.state.pool.acquire() as conn:
            allowed = await allowed_primitives(conn, caller)
        tools = [
            Tool(
                name=p.id,
                description=p.description,
                input_schema=IMPLEMENTATIONS[p.id].args_model.model_json_schema(),
                annotations=_annotations(p),
            )
            for p in CATALOG
            if p.id in allowed
        ]
        return ListToolsResult(tools=tools)

    async def on_call_tool(
        _ctx: ServerRequestContext[Any], params: CallToolRequestParams
    ) -> CallToolResult:
        caller = _caller()
        name = params.name
        if name not in BY_ID:
            log.info("mcp_call", primitive=name, outcome="unknown_tool", **caller.log_fields())
            raise MCPError(INVALID_PARAMS, f"Unknown tool: {name}")
        started = time.perf_counter()
        outcome = "ok"
        # Écriture : les chemins du brouillon touchés (jamais le contenu).
        written: list[str] | None = None
        try:
            result = await _run(caller, name, params.arguments or {})
            structured = result.structured_content
            content = structured if isinstance(structured, dict) else {}
            if result.is_error:
                outcome = str(content.get("error"))
            elif isinstance(content.get("changed_paths"), list):
                written = [str(p) for p in content["changed_paths"]]
            return result
        except Exception:
            outcome = "internal_error"
            raise
        finally:
            extra = {"changed_paths": written} if written is not None else {}
            log.info(
                "mcp_call",
                primitive=name,
                outcome=outcome,
                duration_ms=round((time.perf_counter() - started) * 1000, 1),
                **extra,
                **caller.log_fields(),
            )

    async def _run(caller: McpCaller, name: str, arguments: dict[str, Any]) -> CallToolResult:
        implementation = IMPLEMENTATIONS[name]
        async with app.state.pool.acquire() as conn:
            # Droits avant arguments : une primitive refusée ne révèle rien de plus.
            if not await authorize(conn, caller, name):
                return _refusal("primitive_forbidden", primitive=name)
            try:
                args = implementation.args_model.model_validate(arguments)
            except ValidationError as exc:
                fields = sorted({".".join(str(p) for p in err["loc"]) for err in exc.errors()})
                return _primitive_refusal(name, "invalid_arguments", {"fields": fields})
            # `branch` contrôlé ici pour toutes les primitives, y compris celles
            # qui ne lisent que le brouillon (get_my_draft, préparation) et ne
            # passent pas par open_branch : une valeur inconnue est refusée partout.
            try:
                check_branch(getattr(args, "branch", None))
            except PrimitiveError as exc:
                return _primitive_refusal(name, exc.code, exc.detail)
            space_id = getattr(args, "space_id", None)
            if isinstance(space_id, uuid.UUID) and not await authorize(
                conn, caller, name, space_id
            ):
                return _refusal("primitive_forbidden", primitive=name, space_id=str(space_id))
            ctx = PrimitiveContext(
                conn=conn, caller=caller, user=await caller_user(conn, caller), state=app.state
            )
            try:
                return _result(await implementation.run(ctx, args))
            except PrimitiveError as exc:
                return _primitive_refusal(name, exc.code, exc.detail)

    server: Server[Any] = Server(
        "ossie-studio",
        version=version("ossie-studio"),
        instructions=_INSTRUCTIONS,
        on_list_tools=on_list_tools,
        on_call_tool=on_call_tool,
    )
    return StreamableHTTPSessionManager(app=server, stateless=True, json_response=True)
