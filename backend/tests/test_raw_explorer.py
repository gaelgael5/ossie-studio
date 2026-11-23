"""Explorateur raw : index en lecture seule de la couche raw (module + API)."""

from __future__ import annotations

import json
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from ossie_studio.parsers.conventions import dump_schema
from ossie_studio.sources.collect_core import TechEntry, TechManifest, dump_tech_manifest
from ossie_studio.sources.explorer import (
    BADGE_ERROR,
    BADGE_EXCLUDED,
    BADGE_MODIFIED,
    BADGE_NEW,
    BADGE_RETAINED,
    ObjectTag,
    SearchCriteria,
    SearchFacets,
    SourceEntries,
    SourceIndex,
    build_source_index,
    clear_caches,
    collect_source_entries,
    git_blob_id,
    head_commit,
    match_counts,
    search_objects,
    subtree_file_counts,
)
from ossie_studio.sources.manifest import SourceManifest, dump_manifest
from tests.conftest import create_plain_user, login
from tests.space_fixture import ApiSpace, forge_edit, git, make_api_space


@pytest.fixture(autouse=True)
def _fresh_caches() -> None:
    clear_caches()


def _schema(title: str, kind: str = "class", fields: int = 1, **extra: object) -> str:
    data: dict[str, object] = {
        "$id": f"{title.lower()}.json",
        "title": title,
        "type": "object",
        "x-kind": kind,
        "properties": {f"f{i}": {"type": "string"} for i in range(fields)},
    }
    data.update(extra)
    return dump_schema(data)


_GIT_PARAMS = {"repository": "org/app", "branch": "main", "parsers": ["csharp"]}


def _manifest(slug: str, type_: str = "git", triage: list | None = None, **params: object) -> str:
    if type_ == "git" and not params:
        params = dict(_GIT_PARAMS)
    return dump_manifest(
        SourceManifest(
            slug=slug,
            label=slug.upper(),
            type=type_,
            connection="" if type_ == "openapi" else "c",
            parameters=dict(params),
            triage=list(triage or []),
        )
    )


def _tech(entries: dict[str, tuple[list[str], str]]) -> str:
    return dump_tech_manifest(
        TechManifest(
            parsers={},
            entries={
                src: TechEntry(checksum="x", destinations=dests, branch="main", parser=parser)
                for src, (dests, parser) in entries.items()
            },
        )
    )


def _git_files(slug: str = "app") -> dict[str, str]:
    raw = f"sources/{slug}/raw"
    return {
        f"sources/{slug}/manifest.yaml": _manifest(slug),
        f"{raw}/order.json": _schema("Order", fields=3),
        f"{raw}/status.json": _schema("Status", kind="enum", fields=0),
        f"{raw}/customer.json": _schema("Customer", kind="record", fields=2),
        f"{raw}/manifest-csharp.yaml": _tech(
            {
                "src/Domain/Order.cs": (["order.json", "status.json"], "csharp"),
                "src/Domain/Sub/Customer.cs": (["customer.json"], "csharp"),
            }
        ),
        f"{raw}/docs-index.yaml": (
            "version: 2\ndocuments:\n"
            "- {path: docs/guide.md, crc: a, title: Guide, sections: [Un, Deux],"
            " mentions: [], resources: []}\n"
        ),
        f"{raw}/docs/docs/guide.md": "# Guide\n",
    }


def _write(root: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")


# --- Module ------------------------------------------------------------------


def test_git_arborescence_d_origine(tmp_path: Path) -> None:
    _write(tmp_path, _git_files())
    index = build_source_index(tmp_path, "app", "origin")
    assert index.collected and index.type == "git"
    assert set(index.folders) == {"", "src", "src/Domain", "src/Domain/Sub", "docs"}
    domain = index.folders["src/Domain"]
    assert {o.file for o in domain.objects} == {"order.json", "status.json"}
    assert domain.folders == {"src/Domain/Sub"}
    order = next(o for o in domain.objects if o.file == "order.json")
    assert (order.title, order.kind, order.fields) == ("Order", "class", 3)
    assert order.origin == "src/Domain/Order.cs" and order.parser == "csharp"
    assert [d.title for d in index.folders["docs"].documents] == ["Guide"]
    assert index.folders["docs"].documents[0].sections == 2


def test_trois_modes_regroupent_les_memes_objets(tmp_path: Path) -> None:
    _write(tmp_path, _git_files())
    by_mode = {m: build_source_index(tmp_path, "app", m) for m in ("origin", "kind", "parser")}
    for index in by_mode.values():
        files = sorted(o.file for f in index.folders.values() for o in f.objects)
        assert files == ["customer.json", "order.json", "status.json"]
    assert set(by_mode["kind"].folders) == {"", "class", "enum", "record", "documents"}
    assert set(by_mode["parser"].folders) == {"", "csharp", "markdown"}


def test_base_de_donnees_base_schema(tmp_path: Path) -> None:
    raw = "sources/entrepot/raw"
    _write(
        tmp_path,
        {
            "sources/entrepot/manifest.yaml": _manifest(
                "entrepot", "postgresql", database="ventes"
            ),
            f"{raw}/public.orders.json": _schema("orders", kind="table"),
            f"{raw}/audit.v_log.json": _schema("v_log", kind="view"),
            f"{raw}/manifest-postgresql.yaml": _tech(
                {
                    "public.orders": (["public.orders.json"], "postgresql"),
                    "audit.v_log": (["audit.v_log.json"], "postgresql"),
                }
            ),
        },
    )
    index = build_source_index(tmp_path, "entrepot", "origin")
    assert index.folders[""].folders == {"ventes"}
    assert index.folders["ventes"].folders == {"ventes/public", "ventes/audit"}
    assert [o.title for o in index.folders["ventes/public"].objects] == ["orders"]


def test_openapi_par_contrat(tmp_path: Path) -> None:
    raw = "sources/paiement/raw"
    _write(
        tmp_path,
        {
            "sources/paiement/manifest.yaml": _manifest("paiement", "openapi"),
            f"{raw}/v1-payment.json": _schema(
                "Payment",
                kind="contract-schema",
                **{"x-source": {"file": "v1.yaml", "pointer": "#/components/schemas/Payment"}},
            ),
        },
    )
    index = build_source_index(tmp_path, "paiement", "origin")
    assert [o.title for o in index.folders["v1.yaml"].objects] == ["Payment"]


def test_badges_retenu_ecarte_erreur_et_absents_sans_information(tmp_path: Path) -> None:
    files = _git_files()
    files["sources/app/assets/order.json"] = _schema("Order")
    files["sources/app/raw/broken.json"] = "{ pas du json"
    _write(tmp_path, files)
    plain = build_source_index(tmp_path, "app", "kind")
    badges = {o.file: o.badges for f in plain.folders.values() for o in f.objects}
    assert badges["order.json"] == (BADGE_RETAINED,)
    assert badges["status.json"] == ()  # hors git ni règle : aucun badge
    assert badges["broken.json"] == (BADGE_ERROR,)  # illisible, mais les autres s'affichent

    # Règle de tri + fichier d'origine en erreur à la dernière collecte.
    (tmp_path / "sources/app/manifest.yaml").write_text(
        _manifest("app", triage=[{"target": "object-name", "kind": "glob", "pattern": "Status"}])
    )
    index = build_source_index(
        tmp_path, "app", "kind", error_origins={"src/Domain/Sub/Customer.cs"}
    )
    badges = {o.file: o for f in index.folders.values() for o in f.objects}
    assert BADGE_EXCLUDED in badges["status.json"].badges
    assert badges["status.json"].excluded_by
    assert badges["customer.json"].badges == (BADGE_ERROR,)


def test_badges_nouveau_et_modifie_par_rapport_a_la_tete_publiee(tmp_path: Path) -> None:
    files = _git_files()
    _write(tmp_path, files)
    git(tmp_path, "init", "-q", "-b", "main")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-q", "-m", "collecte")
    base = git(tmp_path, "rev-parse", "HEAD")
    # Copie publiée telle quelle : aucun badge.
    assert head_commit(tmp_path) == base
    published = build_source_index(tmp_path, "app", "kind", published_base=base)
    assert all(not o.badges for f in published.folders.values() for o in f.objects)
    # Ma vue = publié + brouillon (fichiers modifiés par-dessus, sans commit).
    (tmp_path / "sources/app/raw/order.json").write_text(_schema("Order", fields=4))
    (tmp_path / "sources/app/raw/invoice.json").write_text(_schema("Invoice"))
    index = build_source_index(tmp_path, "app", "kind", published_base=base)
    badges = {o.file: o.badges for f in index.folders.values() for o in f.objects}
    assert badges["order.json"] == (BADGE_MODIFIED,)
    assert badges["invoice.json"] == (BADGE_NEW,)
    assert badges["status.json"] == ()
    # Commit inconnu : pas de badge, pas d'erreur.
    index = build_source_index(tmp_path, "app", "kind", published_base="0" * 40)
    assert all(not o.badges for f in index.folders.values() for o in f.objects)


def test_git_blob_id_identique_a_git(tmp_path: Path) -> None:
    (tmp_path / "f").write_bytes(b"bonjour\n")
    expected = subprocess.run(
        ["git", "hash-object", str(tmp_path / "f")], capture_output=True, text=True, check=True
    ).stdout.strip()
    assert git_blob_id(b"bonjour\n") == expected


def test_recherche_insensible_a_la_casse(tmp_path: Path) -> None:
    _write(tmp_path, _git_files())
    index = build_source_index(tmp_path, "app", "origin")
    found = search_objects(index, SearchCriteria(text="ORD"))
    assert [(folder, o.file) for folder, o in found] == [("src/Domain", "order.json")]
    assert search_objects(index, SearchCriteria(text="  ")) == []


def _tagged(index: SourceIndex, tags: dict[str, str], visibility: dict[str, str]) -> None:
    """Pose des tags et des visibilités sur l'index (les règles ne sont pas l'objet ici)."""
    for folder in index.folders.values():
        folder.objects = [
            replace(
                o,
                tags=(ObjectTag(tag=tags[o.file], rule="r", rule_name="R"),)
                if o.file in tags
                else (),
                visibility=visibility.get(o.file, o.visibility),
            )
            for o in folder.objects
        ]


def test_recherche_criteres_combines_portee_et_facettes(tmp_path: Path) -> None:
    _write(tmp_path, _git_files())
    index = build_source_index(tmp_path, "app", "origin")
    _tagged(
        index,
        {"order.json": "technique/service", "customer.json": "technique/service"},
        {"order.json": "public", "customer.json": "internal", "status.json": "public"},
    )
    files = lambda found: sorted(o.file for _, o in found)  # noqa: E731
    # Un filtre seul, sans texte, lance la recherche.
    tag = SearchCriteria(tag="technique/service")
    assert files(search_objects(index, tag)) == ["customer.json", "order.json"]
    # Tag ET visibilité ET texte.
    both = SearchCriteria(tag="technique/service", visibilities=frozenset({"public"}))
    assert files(search_objects(index, both)) == ["order.json"]
    assert search_objects(index, replace(both, text="cust")) == []
    # Portée : le dossier `src` (sans objet direct) et tous ses sous-dossiers.
    facets = SearchFacets()
    assert files(search_objects(index, tag, "src", facets)) == ["customer.json", "order.json"]
    assert facets.tags == {"technique/service"}
    assert facets.visibilities == {"public", "internal"}
    assert facets.kinds == {"class", "enum", "record"}
    assert files(search_objects(index, tag, "src/Domain/Sub")) == ["customer.json"]
    # Un préfixe de nom n'est pas un sous-dossier.
    assert search_objects(index, tag, "sr") == []
    # Aucun critère : rien (l'écran montre le dossier), mais les facettes sont là.
    facets = SearchFacets()
    assert search_objects(index, SearchCriteria(), "src", facets) == []
    assert facets.tags == {"technique/service"}


def test_compteurs_de_l_arbre_filtre(tmp_path: Path) -> None:
    _write(tmp_path, _git_files())
    index = build_source_index(tmp_path, "app", "origin")
    _tagged(index, {"customer.json": "donnees/adresse"}, {})
    # Cumul vers les parents ; les dossiers sans objet correspondant sont absents.
    assert match_counts(index, SearchCriteria(tag="donnees/adresse")) == {
        "": 1,
        "src": 1,
        "src/Domain": 1,
        "src/Domain/Sub": 1,
    }
    counts = match_counts(index, SearchCriteria(kind="enum"))
    assert counts == {"": 1, "src": 1, "src/Domain": 1}
    assert "docs" not in counts  # les documents ne comptent pas
    assert match_counts(index, SearchCriteria(text="zzz")) == {}


def test_critere_objets_modifies(tmp_path: Path) -> None:
    _write(tmp_path, _git_files())
    index = build_source_index(tmp_path, "app", "origin")
    badges = {"order.json": (BADGE_NEW,), "status.json": (BADGE_MODIFIED,)}
    for folder in index.folders.values():
        folder.objects = [replace(o, badges=badges.get(o.file, ())) for o in folder.objects]
    found = search_objects(index, SearchCriteria(changed=True))
    assert sorted(o.file for _, o in found) == ["order.json", "status.json"]
    combined = SearchCriteria(changed=True, kind="enum")
    assert [o.file for _, o in search_objects(index, combined)] == ["status.json"]
    assert match_counts(index, SearchCriteria(changed=True))["src/Domain"] == 2


def test_recherche_limite_appliquee_apres_filtrage(tmp_path: Path) -> None:
    _write(tmp_path, _git_files())
    index = build_source_index(tmp_path, "app", "origin")
    _tagged(index, {"status.json": "x"}, {})
    # La limite à 1 ne coupe pas avant le filtre : Status reste trouvé.
    found = search_objects(index, SearchCriteria(tag="x"), limit=1)
    assert [o.file for _, o in found] == ["status.json"]


def test_manifeste_non_conforme_reste_explorable(tmp_path: Path) -> None:
    files = _git_files()
    files["sources/app/manifest.yaml"] = "version: 2\nslug: app\nlabel: App\ntype: git\n"
    _write(tmp_path, files)
    index = build_source_index(tmp_path, "app", "origin")
    assert index.type == "git" and "src/Domain" in index.folders


def test_source_jamais_collectee(tmp_path: Path) -> None:
    _write(tmp_path, {"sources/app/manifest.yaml": _manifest("app")})
    index = build_source_index(tmp_path, "app", "origin")
    assert not index.collected and index.objects == [] and set(index.folders) == {""}


def test_schema_relu_seulement_s_il_change(tmp_path: Path) -> None:
    _write(tmp_path, _git_files())
    first = build_source_index(tmp_path, "app", "kind")
    assert any(o.title == "Order" for o in first.objects)
    (tmp_path / "sources/app/raw/order.json").write_text(_schema("Commande", fields=5))
    second = build_source_index(tmp_path, "app", "kind")
    assert any(o.title == "Commande" and o.fields == 5 for o in second.objects)


# --- API ---------------------------------------------------------------------


@pytest.fixture
async def sp(client: AsyncClient, app: FastAPI, tmp_path: Path) -> ApiSpace:
    return await make_api_space(client, app, tmp_path)


async def test_api_sources_arbre_dossier_recherche(sp: ApiSpace) -> None:
    forge_edit(sp, _git_files())
    base = f"{sp.base}/raw-explorer"

    r = await sp.client.get(base)
    assert r.status_code == 200, r.text
    (source,) = r.json()["sources"]
    assert source["slug"] == "app" and source["collected"] is True
    assert source["objects"] == 3 and source["documents"] == 1

    r = await sp.client.get(f"{base}/app/tree", params={"mode": "origin"})
    assert r.status_code == 200, r.text
    folders = {f["path"]: f for f in r.json()["folders"]}
    assert folders["src/Domain"]["files"] == 3  # sous-arbre compris
    assert folders[""]["files"] == 4  # 3 objets + 1 document
    assert folders["src/Domain"]["items"] == 3  # 2 objets + 1 sous-dossier

    r = await sp.client.get(f"{base}/app/folder", params={"mode": "origin", "path": "src/Domain"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert [f["path"] for f in body["folders"]] == ["src/Domain/Sub"]
    assert [o["title"] for o in body["objects"]] == ["Order", "Status"]
    assert body["objects"][0]["badges"] == []

    r = await sp.client.get(f"{base}/search", params={"q": "cust", "source": "app"})
    assert [(x["folder"], x["file"]) for x in r.json()["results"]] == [
        ("src/Domain/Sub", "customer.json")
    ]


async def test_api_recherche_plusieurs_sources_et_portee(sp: ApiSpace) -> None:
    forge_edit(sp, {**_git_files("app"), **_git_files("crm")})
    url = f"{sp.base}/raw-explorer/search"

    # Aucune source demandée : toutes ; chaque résultat dit sa source.
    r = await sp.client.get(url, params={"kind": "record"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert [(x["source"], x["folder"], x["file"]) for x in body["results"]] == [
        ("app", "src/Domain/Sub", "customer.json"),
        ("crm", "src/Domain/Sub", "customer.json"),
    ]
    assert body["truncated"] is False
    assert body["tree_counts"]["app"] == {"": 1, "src": 1, "src/Domain": 1, "src/Domain/Sub": 1}
    assert body["facets"]["kinds"] == ["class", "enum", "record"]

    # Une source et un dossier : le dossier et ses sous-dossiers seulement.
    params: dict[str, str | list[str]] = {"source": "app", "path": "src/Domain/Sub", "q": "o"}
    r = await sp.client.get(url, params=params)
    assert [x["file"] for x in r.json()["results"]] == ["customer.json"]
    assert r.json()["facets"]["kinds"] == ["record"]

    # Plusieurs sources : le dossier ne vaut plus (un chemin n'a pas de sens
    # d'une source à l'autre) ; une source inconnue est ignorée.
    params = {"source": ["app", "crm", "inconnue"], "path": "src/Domain/Sub", "kind": "enum"}
    r = await sp.client.get(url, params=params)
    assert r.status_code == 200, r.text
    assert [x["source"] for x in r.json()["results"]] == ["app", "crm"]

    # Visibilité à choix multiple (aucun objet n'en porte ici) et mode invalide.
    r = await sp.client.get(url, params={"visibility": ["public", "private"]})
    assert r.json()["results"] == []
    r = await sp.client.get(url, params={"mode": "nimporte"})
    assert r.status_code == 400


async def test_api_erreurs(sp: ApiSpace) -> None:
    forge_edit(sp, _git_files())
    base = f"{sp.base}/raw-explorer"
    r = await sp.client.get(f"{base}/app/tree", params={"mode": "nimporte"})
    assert r.status_code == 400 and r.json()["error"] == "raw_explorer_mode_invalid"
    r = await sp.client.get(f"{base}/app/folder", params={"path": "absent"})
    assert r.status_code == 404 and r.json()["error"] == "raw_explorer_folder_not_found"
    r = await sp.client.get(f"{base}/inconnue/tree")
    assert r.status_code == 404 and r.json()["error"] == "source_not_found"
    r = await sp.client.get(f"{base}/..%2Fx/tree")
    assert r.status_code == 404


async def test_api_espace_sans_source(sp: ApiSpace) -> None:
    r = await sp.client.get(f"{sp.base}/raw-explorer")
    assert r.status_code == 200 and r.json()["sources"] == []


async def test_api_reservee_a_la_capacite(sp: ApiSpace, app: FastAPI) -> None:
    await create_plain_user(app, "lambda@example.org", "mot-de-passe-solide")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as other:
        await login(other, "lambda@example.org", "mot-de-passe-solide")
        r = await other.get(f"{sp.base}/raw-explorer")
    assert r.status_code == 403


def test_tech_manifest_illisible_ne_bloque_pas(tmp_path: Path) -> None:
    files = _git_files()
    files["sources/app/raw/manifest-csharp.yaml"] = ": : pas du yaml ["
    _write(tmp_path, files)
    index = build_source_index(tmp_path, "app", "origin")
    # Sans manifeste technique, les objets restent visibles (à la racine).
    assert len(index.folders[""].objects) == 3
    assert json.loads((tmp_path / "sources/app/raw/order.json").read_text())["title"] == "Order"


def test_comptes_de_fichiers_incluent_les_documents(tmp_path: Path) -> None:
    _write(tmp_path, _git_files())
    counts = subtree_file_counts(build_source_index(tmp_path, "app", "origin"))
    assert counts["docs"] == 1  # un dossier de documents seuls n'affiche pas 0
    assert counts["src"] == 3 and counts[""] == 4


def test_entrees_aller_retour_json(tmp_path: Path) -> None:
    _write(tmp_path, _git_files())
    entries = collect_source_entries(tmp_path, "app")
    back = SourceEntries.from_json(
        "app",
        entries.label,
        entries.type,
        entries.collected,
        json.loads(json.dumps(entries.to_json())),
    )
    assert back == entries


async def test_api_index_reutilise_puis_reconstruit(sp: ApiSpace, app: FastAPI) -> None:
    forge_edit(sp, _git_files())
    base = f"{sp.base}/raw-explorer"
    assert (await sp.client.get(base)).status_code == 200
    async with app.state.pool.acquire() as conn:
        first = await conn.fetchval("SELECT max(built_at) FROM raw_explorer_index")
    # Rien n'a changé : l'index est relu, pas reconstruit.
    r = await sp.client.get(f"{base}/app/folder", params={"path": "src/Domain"})
    assert r.status_code == 200
    async with app.state.pool.acquire() as conn:
        assert await conn.fetchval("SELECT max(built_at) FROM raw_explorer_index") == first
    # Un commit sur la forge : la signature change, l'index suit.
    forge_edit(sp, {"sources/app/raw/invoice.json": _schema("Invoice")})
    app.state.workdir_manager._head_cache.clear()  # pas d'attente du délai de fetch
    r = await sp.client.get(f"{base}/search", params={"q": "invoice"})
    assert [x["file"] for x in r.json()["results"]] == ["invoice.json"]


def test_namespace_garde_dans_l_index(tmp_path: Path) -> None:
    files = _git_files()
    files["sources/app/raw/order.json"] = _schema("Order", **{"x-namespace": "App.Domain"})
    _write(tmp_path, files)
    entries = collect_source_entries(tmp_path, "app")
    reread = SourceEntries.from_json("app", "APP", "git", True, entries.to_json())
    order = next(o for o in reread.objects if o.title == "Order")
    assert order.namespace == "App.Domain"
    assert next(o for o in reread.objects if o.title == "Status").namespace == ""
