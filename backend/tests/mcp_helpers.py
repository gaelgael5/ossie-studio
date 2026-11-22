"""Client MCP du SDK branché sur l'application en mémoire, pour les tests."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx2
from fastapi import FastAPI
from mcp import Client
from mcp.client.streamable_http import streamable_http_client


@asynccontextmanager
async def mcp_client(app: FastAPI, token: str) -> AsyncIterator[Client]:
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    ) as http:
        async with Client(streamable_http_client("http://test/mcp", http_client=http)) as client:
            yield client


async def tool_names(app: FastAPI, token: str) -> list[str]:
    async with mcp_client(app, token) as client:
        return [t.name for t in (await client.list_tools()).tools]


async def call_tool(app: FastAPI, token: str, name: str, args: dict[str, Any] | None = None) -> Any:
    async with mcp_client(app, token) as client:
        return await client.call_tool(name, args or {})


async def raw_mcp_status(app: FastAPI, token: str) -> int:
    """Statut HTTP d'une requête MCP brute (sans le client, qui lèverait sur 401)."""
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://test"
    ) as http:
        response = await http.post(
            "/mcp",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json, text/event-stream",
            },
            json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
        )
    return response.status_code
