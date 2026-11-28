"""Service des sources commun à l'écran et à l'agent (ticket 1920df67) : les
primitives MCP n'appellent plus de fonction interne de `api/sources.py`, et le
service ne dépend ni de FastAPI ni de MCP. Le comportement visible est couvert par
les tests existants de l'écran (`test_sources.py`) et des primitives
(`test_mcp_source_writes.py`), inchangés."""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "ossie_studio"


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            out.add(node.module)
            out |= {f"{node.module}.{alias.name}" for alias in node.names}
        elif isinstance(node, ast.Import):
            out |= {alias.name for alias in node.names}
    return out


def test_les_primitives_des_sources_n_appellent_que_le_service() -> None:
    """Critère 1 : plus aucune fonction de `api/sources.py` côté agent."""
    imports = _imports(SRC / "mcp_surface" / "source_writes.py")
    assert not {i for i in imports if i.startswith("ossie_studio.api.sources")}
    assert "ossie_studio.sources.source_actions" in imports


def test_le_service_ne_connait_ni_fastapi_ni_mcp() -> None:
    imports = _imports(SRC / "sources" / "source_actions.py")
    assert not {i for i in imports if i.startswith(("fastapi", "mcp", "ossie_studio.mcp_surface"))}
    assert not {i for i in imports if i.startswith("ossie_studio.api.sources")}
