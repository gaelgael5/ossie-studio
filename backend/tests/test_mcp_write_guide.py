"""Pour qu'un agent ne soit jamais perdu (epic « Primitives MCP — écriture commune par
genre », ticket c7838a43) : `list_write_kinds` généré depuis le registre,
`describe_standard` et ses exemples vérifiés par les validateurs, et le renvoi `aide`
dans les erreurs de format de toutes les primitives."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import asyncpg
import pytest
import yaml
from fastapi import FastAPI
from httpx import AsyncClient

from ossie_studio.cartographie.process import check_bpmn_schema
from ossie_studio.mcp_surface import write_guide
from ossie_studio.mcp_surface.caller import McpCaller
from ossie_studio.mcp_surface.doc_paths import Operation, apply_json, apply_xml
from ossie_studio.mcp_surface.kinds import KINDS, write_kind
from ossie_studio.sources.manifest import SourceManifest, dump_manifest
from tests.mcp_helpers import call_tool
from tests.space_fixture import ApiSpace, make_api_space

AGENT = "guide-agent"

# --- Les exemples des standards passent leur validateur ------------------------------------

FIXTURE: dict[str, str] = {
    "cartographie/types/processus-metier.schema.yaml": (
        "title: Processus métier\nx-archimate: BusinessProcess\ntype: object\n"
    ),
    "cartographie/types/role.schema.yaml": "title: Rôle\nx-archimate: BusinessRole\ntype: object\n",
    "cartographie/types/affectation.schema.yaml": (
        "title: Affectation\nx-archimate: Assignment\ntype: object\n"
    ),
    "cartographie/types/acces.schema.yaml": "title: Accès\nx-archimate: Access\ntype: object\n",
    "cartographie/types/document-metier.schema.yaml": (
        "title: Document métier\nx-archimate: BusinessObject\ntype: object\n"
    ),
    "cartographie/objets/processus-metier/prc-prise-commande.yaml": (
        "cle: PRC-PRISE-COMMANDE\ntype: processus-metier\nnom: Prise de commande\n"
    ),
    "cartographie/objets/document-metier/obj-commande.yaml": (
        "cle: OBJ-COMMANDE\ntype: document-metier\nnom: Commande\n"
    ),
    "cartographie/regles/standard.rules.yaml": "lang: fr\n",
    # Une source, pour les règles de tri (ticket 71347227).
    "sources/ventes/manifest.yaml": dump_manifest(
        SourceManifest(
            slug="ventes",
            label="Ventes",
            type="git",
            connection="gh",
            parameters={"repository": "o/r", "branch": "main", "parsers": ["csharp"]},
        )
    ),
}


def _space(tmp_path: Path) -> Path:
    for path, text in FIXTURE.items():
        (tmp_path / path).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / path).write_text(text, encoding="utf-8")
    return tmp_path


def _apply(root: Path, entries: list[dict[str, Any]]) -> None:
    """Joue des entrées de write_documents par les validateurs du registre, dans un
    ordre libre (comme la primitive) ; lève le dernier refus si une ne passe pas."""
    pending = list(entries)
    while pending:
        failures: list[tuple[dict[str, Any], Exception]] = []
        for entry in pending:
            kind = write_kind(entry["kind"])
            try:
                location = kind.locate(root, entry["identity"])
                current = kind.store.read(root, location)
                if entry.get("delete"):
                    kind.store.delete(root, location)
                else:
                    kind.store.write(root, location, entry["content"], create=current is None)
            except Exception as exc:
                failures.append((entry, exc))
        if len(failures) == len(pending):
            raise failures[0][1]
        pending = [entry for entry, _exc in failures]


def _examples() -> list[tuple[str, str, str]]:
    return [
        (standard, nature, text)
        for standard in write_guide.standards()
        for nature, text in write_guide.examples(standard)
    ]


def test_chaque_standard_a_un_titre_et_un_resume() -> None:
    known = write_guide.standards()
    expected = {k.standard for k in KINDS} | {"jsonpath", "xpath", "writing-rules", "errors"}
    assert expected <= set(known)
    for standard, found in known.items():
        assert found["title"] and found["summary"], standard


@pytest.mark.parametrize(("standard", "nature", "text"), _examples())
def test_les_exemples_passent_leur_validateur(
    tmp_path: Path, standard: str, nature: str, text: str
) -> None:
    """Critère 3 : un exemple qui ne valide plus fait échouer les tests."""
    data = yaml.safe_load(text)
    if nature == "example":
        root = _space(tmp_path)
        _apply(root, data if isinstance(data, list) else [data])
        return
    operations = [Operation(o["op"], o["path"], o.get("value")) for o in data["operations"]]
    if write_kind(data["kind"]).syntax == "xpath":
        check_bpmn_schema(apply_xml(data["document"], operations))
    else:
        apply_json(data["document"], operations)


def test_chaque_genre_et_chaque_syntaxe_ont_un_exemple() -> None:
    """Chaque genre qu'on peut écrire a un exemple ; un genre en lecture seule
    (ticket 56f3784a) a son standard, qui nomme l'action qui le produit."""
    standards_with_examples = {s for s, _n, _t in _examples()}
    for kind in KINDS:
        if not kind.writable:
            text = write_guide.standards()[kind.standard]["markdown"]
            assert all(action in text for action in kind.produced_by), kind.name
            continue
        assert kind.standard in standards_with_examples, kind.name
    assert {"jsonpath", "xpath", "writing-rules"} <= standards_with_examples


def test_les_regles_communes_expliquent_le_renommage() -> None:
    """Critère 6 : renommer = créer le nouveau et supprimer l'ancien, dans un même lot."""
    text = write_guide.standards()["writing-rules"]["markdown"]
    assert "create the new document and delete the" in text
    [(nature, example)] = write_guide.examples("writing-rules")
    entries = yaml.safe_load(example)
    assert nature == "example" and any(e.get("delete") for e in entries)
    assert any("content" in e for e in entries)


# --- Les primitives ---------------------------------------------------------------------


async def _no_row(*_args: Any) -> None:
    return None


def _ctx(limit: int = 5120) -> Any:
    """Un contexte sans réglage posé sur la page « Paramètres » : la limite est celle
    de la configuration."""
    return SimpleNamespace(
        conn=SimpleNamespace(fetchrow=_no_row),
        state=SimpleNamespace(settings=SimpleNamespace(document_max_bytes=limit)),
    )


async def test_list_write_kinds_genere_depuis_le_registre(monkeypatch: pytest.MonkeyPatch) -> None:
    """Critère 1 : tous les genres ; un genre ajouté au registre y apparaît."""
    listed = await write_guide.list_write_kinds(_ctx(), write_guide.ListWriteKindsArgs())
    assert [k["kind"] for k in listed["kinds"]] == [k.name for k in KINDS]
    assert listed["document_max_bytes"] == 5120
    objet = next(k for k in listed["kinds"] if k["kind"] == "carto_objet")
    assert [f["name"] for f in objet["identity"]] == ["type", "key"]
    assert objet["path"] == {"rule": "forbidden"} and objet["syntax"] == "jsonpath"
    assert objet["standard"] == "carto-objet" and "read_schema" in objet["schema"]
    assert objet["identity_fields"] == ["$.cle", "$.type"]
    bpmn = next(k for k in listed["kinds"] if k["kind"] == "processus")
    assert bpmn["syntax"] == "xpath" and bpmn["standard"] == "bpmn"
    extra = KINDS[0].__class__(**{**KINDS[0].__dict__, "name": "essai"})
    monkeypatch.setattr(write_guide, "KINDS", (*KINDS, extra))
    again = await write_guide.list_write_kinds(_ctx(), write_guide.ListWriteKindsArgs())
    assert again["kinds"][-1]["kind"] == "essai"


async def test_describe_standard() -> None:
    """Critère 2."""
    listed = await write_guide.describe_standard(_ctx(), write_guide.DescribeStandardArgs())
    assert {"carto-objet", "jsonpath", "xpath"} <= {s["standard"] for s in listed["standards"]}
    one = await write_guide.describe_standard(
        _ctx(), write_guide.DescribeStandardArgs(standard="carto-objet")
    )
    assert one["title"].startswith("Map object") and "```yaml example" in one["markdown"]


@pytest.fixture
async def space(app: FastAPI, client: AsyncClient, tmp_path: Path) -> ApiSpace:
    api_space = await make_api_space(client, app, tmp_path)
    async with app.state.pool.acquire() as conn:
        profile = await conn.fetchval(
            "INSERT INTO agent_profiles (name, primitives, all_read) VALUES ('guide', $1, true) "
            "RETURNING id",
            ["write_documents", "patch_document"],
        )
        admin_id = await conn.fetchval("SELECT id FROM users WHERE is_bootstrap")
    caller = McpCaller(
        kind="oauth", ref="g", agent_profile_id=profile, user_id=admin_id, client_id="claude"
    )

    async def resolver(_conn: asyncpg.Connection, token: str, _res: str) -> McpCaller | None:
        return caller if token == AGENT else None

    app.state.mcp_resolvers.append(resolver)
    return api_space


async def _call(app: FastAPI, tool: str, args: dict[str, Any]) -> dict[str, Any]:
    result = await call_tool(app, AGENT, tool, args)
    assert result.is_error, result
    body: dict[str, Any] = json.loads(result.content[0].text)
    return body


async def test_aide_dans_les_erreurs_de_format(app: FastAPI, space: ApiSpace) -> None:
    """Critères 4 et 5 : write_documents, read_schema, list_documents ; un genre inconnu
    renvoie vers list_write_kinds."""
    ids = {"space_id": space.space_id, "branch_id": space.branch_id}
    malformed = await _call(app, "write_documents", {**ids, "message": "x", "documents": [{}]})
    assert malformed["error"] == "invalid_arguments"
    assert malformed["aide"] == {"primitive": "list_write_kinds"}
    unknown = await _call(
        app,
        "write_documents",
        {**ids, "message": "x", "documents": [{"kind": "inconnu", "identity": {}, "content": {}}]},
    )
    assert unknown["errors"][0]["aide"] == {"primitive": "list_write_kinds"}
    schema = await _call(app, "read_schema", {**ids, "path": "pas/un/schema"})
    assert schema["aide"] == {"primitive": "list_write_kinds"}
    listing = await _call(app, "list_documents", ids)  # sans genre
    assert listing["error"] == "invalid_arguments"
    assert listing["aide"] == {"primitive": "list_write_kinds"}
    stray = await _call(app, "list_documents", {**ids, "kind": "tag", "filters": {"x": 1}})
    assert stray["error"] == "list_filter_unknown"
    assert stray["aide"] == {"primitive": "list_write_kinds"}
    standard = await _call(app, "describe_standard", {"standard": "inconnu"})
    assert standard["error"] == "standard_unknown" and "carto-objet" in standard["expected"]
    branch = await _call(app, "list_write_kinds", {"branch": "main"})
    assert branch["aide"] == {"primitive": "describe_standard", "standard": "writing-rules"}
