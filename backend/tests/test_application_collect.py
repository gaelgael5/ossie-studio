"""Les `.view` des modules suivent la collecte (enabler 6c97ad98), de bout en bout :
création, ancre déplacée gardée, module supprimé, collecte sans changement.

Les modules sont fournis ici par le parseur de démonstration (`*.schema.json`) :
les vrais producteurs (`.csproj`, `.sqlproj`…) arrivent avec leurs tickets."""

from __future__ import annotations

import json
from pathlib import Path

from ossie_studio.models import GitSpace
from ossie_studio.parsers.conventions import dump_schema, load_schema
from ossie_studio.sources.collect import SourceCollector
from tests.test_collect_service import _base_branch, _collect, _make_bare_with, _push, _raw
from tests.test_collect_service import setup as setup  # fixture réutilisée

MOVED = {"from": "auto", "to": {"side": "top", "at": 0.5}}


def _module(name: str, deps: list[str]) -> str:
    return json.dumps(
        {
            "title": name,
            "x-kind": "module",
            "x-language": "csharp",
            "x-source": [{"file": f"src/{name}/{name}.csproj"}],
            "x-module": {
                "project": f"src/{name}/{name}.csproj",
                "nature": "library",
                "dependencies": [
                    {"project": f"src/{d}/{d}.csproj", "module": f"module-{d.lower()}.schema.yaml"}
                    for d in deps
                ],
            },
        }
    )


def _view(raw: Path, name: str) -> object:
    file = raw / f"module-{name}.view.yaml"
    return load_schema(file.read_text(encoding="utf-8")) if file.is_file() else None


async def test_les_vues_suivent_les_modules(
    setup: tuple[SourceCollector, GitSpace, Path, Path],
) -> None:
    collector, space, tmp_path, _ = setup
    await _base_branch(collector, space)
    src, work = _make_bare_with(
        tmp_path,
        "src",
        {
            "module-api.schema.json": _module("Api", ["Domain", "Infra"]),
            "module-domain.schema.json": _module("Domain", []),
            "module-infra.schema.json": _module("Infra", []),
        },
    )
    await _collect(collector, space, src)
    raw = _raw(tmp_path, space)
    assert _view(raw, "api") == {
        "links": {
            "module-domain.schema.yaml": {"from": "auto", "to": "auto"},
            "module-infra.schema.yaml": {"from": "auto", "to": "auto"},
        }
    }
    assert _view(raw, "domain") == {"links": {}}

    # Une personne déplace l'ancre d'arrivée du lien Api → Domain (dans son brouillon).
    moved = {
        "links": {
            "module-domain.schema.yaml": MOVED,
            "module-infra.schema.yaml": {"from": "auto", "to": "auto"},
        }
    }

    def move(root: Path) -> None:
        target = root / "sources" / "api" / "raw" / "module-api.view.yaml"
        target.write_text(dump_schema(moved), encoding="utf-8")

    await collector.dw.commit_to_branch(  # type: ignore[attr-defined]
        space.id, "work/principale", space.repo_url, "x", None, "a", "a", "ancre", move
    )

    # Collecte sans changement : rien n'est réécrit, l'ancre reste.
    again = await _collect(collector, space, src)
    assert again.local_commit is None
    assert _view(raw, "api") == moved

    # Infra disparaît de la source : son entrée et son `.view` partent, le reste reste.
    (work / "module-infra.schema.json").unlink()
    (work / "module-api.schema.json").write_text(_module("Api", ["Domain"]), encoding="utf-8")
    _push(work)
    await _collect(collector, space, src)
    assert _view(raw, "api") == {"links": {"module-domain.schema.yaml": MOVED}}
    assert _view(raw, "infra") is None
    assert _view(raw, "domain") == {"links": {}}
