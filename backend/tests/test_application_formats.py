"""Format raw des modules, des fonctions et du fichier `.view` (epic « Cartographie
applicative dans raw », enabler 6c97ad98) : les trois formes, leur validation,
l'écriture déterministe, la mise à jour partielle des `.view` à la collecte, et
l'Explorateur qui n'en liste que les objets."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

from ossie_studio.parsers.application import (
    DEFAULT_LINK,
    dump_functions,
    extend_file,
    is_extend_name,
    is_view_name,
    key_of,
    module_key,
    module_links,
    module_of,
    module_schema,
    sync_view,
    validate_functions,
    validate_view,
    view_file,
    view_links,
)
from ossie_studio.parsers.base import OutputAction
from ossie_studio.parsers.conventions import (
    ConventionError,
    dump_schema,
    load_schema,
    schema_file,
    validate_schema,
)
from ossie_studio.sources.explorer import build_source_index
from ossie_studio.sources.module_views import sync_module_views
from tests.test_raw_explorer import _git_files, _write

DOC = Path(__file__).resolve().parents[2] / "docs" / "conventions-schemas-generes.md"

API = "src/Boutique.Api/Boutique.Api.csproj"
DOMAIN = "src/Boutique.Domain/Boutique.Domain.csproj"
INFRA = "src/Boutique.Infra/Boutique.Infra.csproj"


def _module(project: str, deps: tuple[str, ...] = (), nature: str = "library") -> dict[str, Any]:
    return module_schema(
        project=project,
        name=project.rsplit("/", 1)[-1].removesuffix(".csproj"),
        language="csharp",
        nature=nature,
        targets=["net8.0"],
        dependencies=deps,
        known_projects=[API, DOMAIN, INFRA],
    )


def _raw_module(project: str, deps: tuple[str, ...] = ()) -> tuple[str, str]:
    schema = _module(project, deps)
    return schema["$id"], dump_schema(schema)


def _doc_example(title: str) -> Any:
    """Le bloc YAML qui suit le titre `### <title>` dans les conventions."""
    text = DOC.read_text(encoding="utf-8")
    start = text.index(f"### {title}")
    block = re.search(r"```yaml\n(.*?)```", text[start:], re.S)
    assert block is not None
    return yaml.safe_load(block.group(1))


# --- Critère 1 : trois formes décrites, avec exemple et JSON Schema ---------------------------


def test_les_exemples_des_conventions_sont_valides() -> None:
    validate_schema(_doc_example("Objet module"))
    validate_functions(_doc_example("Fonctions"))
    validate_view(_doc_example("Vue d'un module"))


def test_objet_module() -> None:
    api = _module(API, (DOMAIN, "../Partage/Outils.csproj"), "web")
    validate_schema(api)
    assert api["$id"] == "module-src-boutique-api-boutique-api.schema.yaml"
    assert api["x-module"]["dependencies"] == [
        {"project": "../Partage/Outils.csproj"},
        {"project": DOMAIN, "module": schema_file(module_key(DOMAIN))},
    ]
    # Un module n'a pas de visibilité, bien qu'il porte un langage de code.
    assert "x-visibility" not in api
    assert module_links(api) == ["module-src-boutique-domain-boutique-domain.schema.yaml"]
    # Deux projets homonymes dans deux dossiers : deux clés.
    assert module_key("a/Api.csproj") != module_key("b/Api.csproj")


def test_module_mal_forme_refuse() -> None:
    bad = {**_module(API), "x-module": {"project": API, "nature": "inconnue", "dependencies": []}}
    with pytest.raises(ConventionError) as exc:
        validate_schema(bad)
    assert exc.value.rule == "module_invalid"
    assert "nature" in exc.value.detail


def test_fonctions_mal_formees_refusees() -> None:
    def lire(entry: dict[str, Any]) -> dict[str, Any]:
        return {"methods": [{"name": "Lire", "visibility": "public", "entryPoint": entry}]}

    with pytest.raises(ConventionError) as exc:
        validate_functions(lire({"kind": "http", "verb": "GET"}))
    assert exc.value.rule == "functions_invalid"
    # Route impossible à calculer : gardée, marquée inconnue (null).
    validate_functions(lire({"kind": "http", "verb": "GET", "route": None}))


def test_vue_mal_formee_refusee() -> None:
    with pytest.raises(ConventionError) as exc:
        validate_view({"links": {"x.schema.yaml": {"from": "milieu", "to": "auto"}}})
    assert exc.value.rule == "view_invalid"


# --- Critère 2 : aucune méthode dans le schéma d'une classe --------------------------------


@pytest.mark.parametrize("key", ["x-methods", "x-functions", "x-entry-point"])
def test_un_schema_de_classe_ne_porte_aucune_methode(key: str) -> None:
    schema = {"$id": "a.schema.yaml", "title": "A", key: []}
    with pytest.raises(ConventionError) as exc:
        validate_schema(schema)
    assert exc.value.rule == "functions_in_schema"


# --- Critère 3 : deux collectes identiques, mêmes octets ----------------------------------


def test_fonctions_ecriture_deterministe() -> None:
    def lire(param: str, kind: str) -> dict[str, Any]:
        parameters = [{"name": param, "type": kind}]
        return {"name": "Lire", "visibility": "public", "parameters": parameters}

    methods = [
        lire("id", "Guid"),
        lire("code", "string"),
        {"name": "Annuler", "visibility": "internal", "static": True},
    ]
    shuffled = [methods[2], methods[0], methods[1]]
    first = dump_functions({"methods": methods, "functions": []})
    assert dump_functions({"methods": shuffled}) == first
    assert [m["name"] for m in load_schema(first)["methods"]] == ["Annuler", "Lire", "Lire"]
    assert "functions" not in load_schema(first)


# --- Critères 4, 5 et 6 : le `.view` mis à jour par ajouts et suppressions -----------------

A_ID = schema_file(module_key(API))
D_ID = schema_file(module_key(DOMAIN))
I_ID = schema_file(module_key(INFRA))
MOVED = {"from": "auto", "to": {"side": "top", "at": 0.5}}


def _view(links: dict[str, Any]) -> str:
    return dump_schema({"links": links})


def test_vue_absente_valeurs_par_defaut() -> None:
    assert view_links(None, [D_ID]) == {D_ID: DEFAULT_LINK}
    # Un fichier illisible n'empêche pas l'affichage non plus.
    assert view_links("links: [", [D_ID]) == {D_ID: DEFAULT_LINK}
    assert view_links(_view({D_ID: MOVED}), [D_ID, I_ID]) == {D_ID: MOVED, I_ID: DEFAULT_LINK}


def test_sync_view_ajoute_retire_garde() -> None:
    text = _view({D_ID: MOVED, I_ID: dict(DEFAULT_LINK)})
    # Rien ne change : rien à écrire.
    assert sync_view(text, [D_ID, I_ID]) is None
    # Lien disparu : son entrée seule est retirée ; l'ancre déplacée reste.
    assert load_schema(sync_view(text, [D_ID]) or "") == {"links": {D_ID: MOVED}}
    # Lien nouveau : entrée ajoutée par défaut, les autres gardées.
    grown = load_schema(sync_view(_view({D_ID: MOVED}), [D_ID, I_ID]) or "")
    assert grown == {"links": {D_ID: MOVED, I_ID: DEFAULT_LINK}}
    # Fichier illisible : jamais écrasé.
    assert sync_view("links: [", [D_ID]) is None


def _snapshot(*modules: tuple[str, str], views: dict[str, str] | None = None) -> dict[str, str]:
    files = dict(modules)
    files.update(views or {})
    return files


def test_collecte_suppression_d_un_module() -> None:
    api_id, api = _raw_module(API, (DOMAIN, INFRA))
    existing = _snapshot(
        _raw_module(API, (DOMAIN, INFRA)),
        _raw_module(DOMAIN),
        _raw_module(INFRA),
        views={
            view_file(key_of(api_id)): _view({D_ID: MOVED, I_ID: dict(DEFAULT_LINK)}),
            view_file(key_of(I_ID)): _view({}),
        },
    )
    # Boutique.Infra disparaît, et la dépendance d'Api vers lui avec.
    _, api_without_infra = _raw_module(API, (DOMAIN,))
    writes, deletes = sync_module_views(existing, [OutputAction(api_id, api_without_infra)], [I_ID])
    by_path = {w.path: w.content for w in writes}
    assert load_schema(by_path[view_file(key_of(api_id))]) == {"links": {D_ID: MOVED}}
    assert view_file(key_of(I_ID)) in deletes
    assert api  # le module Api garde son ancre déplacée vers Domain


def test_collecte_ajout_d_un_module() -> None:
    api_id, _ = _raw_module(API, (DOMAIN,))
    existing = _snapshot(
        _raw_module(API, (DOMAIN,)),
        _raw_module(DOMAIN),
        views={
            view_file(key_of(api_id)): _view({D_ID: MOVED}),
            view_file(key_of(D_ID)): _view({}),
        },
    )
    infra_id, infra = _raw_module(INFRA)
    _, api_with_infra = _raw_module(API, (DOMAIN, INFRA))
    writes, deletes = sync_module_views(
        existing, [OutputAction(infra_id, infra), OutputAction(api_id, api_with_infra)], []
    )
    by_path = {w.path: w.content for w in writes}
    assert load_schema(by_path[view_file(key_of(api_id))]) == {
        "links": {D_ID: MOVED, I_ID: DEFAULT_LINK}
    }
    assert load_schema(by_path[view_file(key_of(infra_id))]) == {"links": {}}
    # Le `.view` de Domain, inchangé, n'est pas réécrit.
    assert view_file(key_of(D_ID)) not in by_path
    assert deletes == []


def test_collecte_sans_changement_ne_reecrit_rien() -> None:
    api_id, api = _raw_module(API, (DOMAIN,))
    existing = _snapshot(
        (api_id, api),
        _raw_module(DOMAIN),
        views={view_file(key_of(api_id)): _view({D_ID: MOVED}),
               view_file(key_of(D_ID)): _view({})},
    )  # fmt: skip
    writes, deletes = sync_module_views(existing, [OutputAction(api_id, api)], [])
    assert [w.path for w in writes] == [api_id]
    assert deletes == []


# --- Critère 7 : l'Explorateur ignore `.extend.yaml` et `.view.yaml` ------------------------


def test_l_explorateur_ignore_fonctions_et_vues(tmp_path: Path) -> None:
    files = _git_files()
    api_id, api = _raw_module(API, (DOMAIN,))
    raw = "sources/app/raw"
    files[f"{raw}/{api_id}"] = api
    files[f"{raw}/{extend_file(key_of(api_id))}"] = dump_functions({"functions": []}) or "{}\n"
    files[f"{raw}/order.extend.yaml"] = dump_functions(
        {"methods": [{"name": "Lire", "visibility": "public"}]}
    )
    files[f"{raw}/{view_file(key_of(api_id))}"] = _view({D_ID: MOVED})
    _write(tmp_path, files)
    index = build_source_index(tmp_path, "app", "origin")
    names = {o.file for o in index.objects}
    assert api_id in names
    assert not any(is_extend_name(n) or is_view_name(n) for n in names)


# --- Appartenance : le fichier de projet le plus proche en remontant -----------------------


def test_appartenance_au_module() -> None:
    projects = {
        module_key(API): API,
        module_key(DOMAIN): DOMAIN,
        "module-racine": "Racine.csproj",
        "module-sous": "src/Boutique.Api/Outils/Outils.csproj",
    }
    assert module_of("src/Boutique.Api/Controllers/CommandesController.cs", projects) == (
        module_key(API)
    )
    assert module_of("src/Boutique.Api/Outils/Texte.cs", projects) == "module-sous"
    assert module_of("scripts/build.cs", projects) == "module-racine"
    assert module_of("src/Boutique.Api/Controllers/X.cs", {module_key(DOMAIN): DOMAIN}) is None
