"""Index des documents (enabler 15863091, epic « Index des documents et validation
avant publication ») : empreinte par contenu, vues par branche et par brouillon,
contenu indexé par type de document, mise à jour à l'enregistrement, comparaison
entre disque et index, reconstruction depuis Git."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from ossie_studio.doc_index.extract import index_file
from ossie_studio.doc_index.store import ViewKey, db_clear_space
from ossie_studio.drafts.workdir import DraftWorkdir
from tests.conftest import create_plain_user
from tests.space_fixture import ApiSpace, admin_workdir, forge_edit, make_api_space

BRANCH = "work/principale"
APP = "cartographie/objets/application/app-01.yaml"
DOC = "cartographie/objets/document/doc-01.yaml"
REL = "cartographie/objets/acces/rel-01.yaml"
TYPES = {
    "application": ("Application", "ApplicationComponent"),
    "document": ("Document", "BusinessObject"),
    "acces": ("Accès", "Access"),
}


# --- Ce que l'index retient d'un fichier --------------------------------------------------


def test_objet_et_relation() -> None:
    obj = index_file(APP, b"cle: APP-01\ntype: application\nnom: Caisse\n")
    [doc] = obj.documents
    assert (doc.document_type, doc.identity, doc.name) == (
        "carto_objet",
        {"type": "application", "key": "APP-01"},
        "Caisse",
    )
    assert obj.keys == [f"path:{APP}", "carto_objet:application/APP-01", "carto_objet:*/APP-01"]
    assert obj.refs == ["carto_type:application"]
    rel = index_file(
        REL, b"cle: REL-01\ntype: acces\nsource: application/APP-01\ncible: DOC-01\nnom: Lit\n"
    )
    assert rel.refs == [
        "carto_type:acces",
        "carto_objet:application/APP-01",
        "carto_objet:*/DOC-01",
    ]
    # Ce que le service de la cartographie en lit (ticket 06171fd3).
    assert rel.documents[0].fields == {
        "nom": "Lit",
        "source": "application/APP-01",
        "cible": "DOC-01",
        "readable": True,
        "object": {
            "key": "REL-01",
            "type": "acces",
            "name": "Lit",
            "source": "application/APP-01",
            "cible": "DOC-01",
        },
    }


def test_types_modeles_referentiels_domaines() -> None:
    ctype = index_file(
        "cartographie/types/application.schema.yaml",
        b"title: Application\nx-archimate: ApplicationComponent\ntype: object\n"
        b"x-relations:\n  ajoutees:\n    flux: [document]\n",
    )
    assert ctype.documents[0].name == "Application"
    assert ctype.refs == ["carto_type:document"]
    extend = index_file(
        "cartographie/types/application.extend.yaml",
        b"referentiels:\n- referentiel: pays\n  proprietes:\n    pays: code\n",
    )
    assert (extend.documents, extend.refs) == ([], ["carto_type:application", "referentiel:pays"])
    model = index_file(
        "cartographie/modeles/vente.modele.yaml",
        f"id: vente\nnom: Vente\ndocuments: [{APP}]\n"
        f"regles: cartographie/regles/vente.rules.yaml\n".encode(),
    )
    assert model.refs == [f"path:{APP}", "path:cartographie/regles/vente.rules.yaml"]
    ref = index_file(
        "referentiels/pays/modele.schema.yaml",
        b"title: Pays\ntype: object\nx-key: [code]\nx-tags: [entreprise]\n",
    )
    assert ref.documents[0].fields == {"tags": ["entreprise"]}
    assert "referentiel:pays" in ref.keys
    assert index_file("referentiels/pays/valeurs.json", b"[]").keys[1] == "referentiel:pays"
    ossie = index_file(
        "domaines/ventes.ossie.yaml",
        b"version: 0.1.1\nsemantic_model:\n- name: ventes\n  datasets:\n  - name: commandes\n"
        b"  - name: clients\n  relationships:\n  - name: commande_client\n    from: commandes\n"
        b"    to: clients\n",
    )
    assert [d.document_type for d in ossie.documents] == ["dataset", "dataset", "relation_dataset"]
    # Une relation qui cite un dataset du même fichier n'en fait pas une dépendance.
    assert "dataset:ventes/clients" in ossie.keys and ossie.refs == ["domaine:ventes"]


def test_fichier_inconnu_ou_illisible() -> None:
    assert index_file("README.md", b"# x").keys == ["path:README.md"]
    broken = index_file(APP, b"cle: [ouvert\n")
    assert broken.documents[0].identity == {"type": "application", "key": "APP-01"}
    assert index_file("cartographie/types/x.schema.yaml", b"\xff\xfe").documents[0].name == "x"


# --- L'index d'un espace --------------------------------------------------------------------


def _object(key: str, ctype: str, name: str, **ends: str) -> str:
    lines = [f"cle: {key}", f"type: {ctype}"]
    lines += [f"{k}: {v}" for k, v in ends.items()]
    return "\n".join([*lines, f"nom: {name}"]) + "\n"


@pytest.fixture
async def space(app: FastAPI, client: AsyncClient, tmp_path: Path) -> ApiSpace:
    api_space = await make_api_space(client, app, tmp_path)
    files: dict[str, str | None] = {
        f"cartographie/types/{slug}.schema.yaml": f"title: {t}\nx-archimate: {a}\ntype: object\n"
        for slug, (t, a) in TYPES.items()
    }
    files[APP] = _object("APP-01", "application", "Caisse")
    files[DOC] = _object("DOC-01", "document", "Ticket")
    files[REL] = _object(
        "REL-01", "acces", "Lit", source="application/APP-01", cible="document/DOC-01"
    )
    forge_edit(api_space, files)
    return api_space


async def _compare(workdir: DraftWorkdir, space: ApiSpace) -> Any:
    return await workdir.compare_index(uuid.UUID(space.space_id), BRANCH, space.url, "x", None)


async def _write(workdir: DraftWorkdir, space: ApiSpace, edits: dict[str, str | None]) -> None:
    def write(root: Path) -> None:
        for rel, text in edits.items():
            target = root / rel
            if text is None:
                target.unlink()
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(text, encoding="utf-8")

    await workdir.commit_to_branch(
        uuid.UUID(space.space_id), BRANCH, space.url, "x", None, "a", "a", "écrit", write
    )


async def _files(app: FastAPI, key: ViewKey) -> dict[str, tuple[str, str, int]]:
    """chemin → (empreinte, état, contenu) des fichiers d'une vue."""
    async with app.state.pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT f.path, c.blob, f.status, c.id
            FROM doc_index_file f
            JOIN doc_index_view v ON v.id = f.view_id
            JOIN doc_index_content c ON c.id = f.content_id
            WHERE v.space_id = $1 AND v.branch_id = $2 AND v.user_id IS NOT DISTINCT FROM $3
            """,
            key.space_id,
            key.branch_id,
            key.user_id,
        )
    return {r["path"]: (r["blob"], r["status"], r["id"]) for r in rows}


async def _validate_all(app: FastAPI) -> None:
    """Simule une passe de validation (ticket de la file) : tout est valide."""
    async with app.state.pool.acquire() as conn:
        await conn.execute("UPDATE doc_index_file SET status = 'valide'")


def _keys(app: FastAPI, space: ApiSpace, workdir: DraftWorkdir) -> tuple[ViewKey, ViewKey]:
    draft = ViewKey(uuid.UUID(space.space_id), uuid.UUID(space.branch_id), workdir.user_id)
    return draft.published, draft


async def test_comparer_puis_enregistrer_un_objet(app: FastAPI, space: ApiSpace) -> None:
    """Critère 1 : l'objet enregistré prend sa nouvelle empreinte dans la vue du brouillon
    et passe `a_valider`, comme la relation qui le cite ; le reste et la vue publiée
    ne bougent pas."""
    workdir = await admin_workdir(app, space)
    published_cmp, _draft_cmp = await _compare(workdir, space)
    assert {APP, DOC, REL} <= set(published_cmp.added)
    published, draft = _keys(app, space, workdir)
    assert (await _files(app, draft)).keys() == (await _files(app, published)).keys()
    await _validate_all(app)
    before = await _files(app, draft)
    await _write(workdir, space, {APP: _object("APP-01", "application", "Caisse centrale")})
    after = await _files(app, draft)
    assert after[APP][0] != before[APP][0] and after[APP][1] == "a_valider"
    assert after[REL][1] == "a_valider"  # elle cite l'objet
    assert after[DOC][1] == "valide"
    assert {p: s for p, (_b, s, _c) in (await _files(app, published)).items()} == {
        p: "valide" for p in after
    }


async def test_meme_contenu_une_seule_entree(app: FastAPI, space: ApiSpace) -> None:
    """Critère 2 : deux brouillons qui ont le même contenu pour un fichier partagent
    une seule entrée de contenu (et la vue publiée aussi, pour un fichier inchangé)."""
    admin = await admin_workdir(app, space)
    await create_plain_user(app, "lea@example.org", "motdepasse-solide")
    async with app.state.pool.acquire() as conn:
        lea_id = await conn.fetchval("SELECT id FROM users WHERE email = 'lea@example.org'")
    lea = DraftWorkdir(
        app.state.workdir_manager, app.state.draft_views, app.state.pool, lea_id, admin.branch_id
    )
    await _compare(admin, space)
    await _compare(lea, space)
    same = _object("APP-01", "application", "Caisse renommée")
    await _write(admin, space, {APP: same})
    await _write(lea, space, {APP: same})
    _published, admin_view = _keys(app, space, admin)
    _published, lea_view = _keys(app, space, lea)
    admin_files, lea_files = await _files(app, admin_view), await _files(app, lea_view)
    assert admin_files[APP][2] == lea_files[APP][2]
    assert admin_files[DOC][2] == lea_files[DOC][2]
    async with app.state.pool.acquire() as conn:
        count = await conn.fetchval(
            "SELECT count(*) FROM doc_index_content WHERE path = $1 AND blob = $2",
            APP,
            admin_files[APP][0],
        )
    assert count == 1


async def test_modification_hors_application(app: FastAPI, space: ApiSpace) -> None:
    """Critère 3 : un fichier modifié hors de l'application est détecté et passe
    `a_valider` ; un fichier supprimé est retiré de la vue, et ceux qui le citaient
    passent `a_valider`."""
    workdir = await admin_workdir(app, space)
    await _compare(workdir, space)
    await _validate_all(app)
    forge_edit(space, {DOC: _object("DOC-01", "document", "Ticket de caisse"), APP: None})
    published_cmp, _draft = await _compare(workdir, space)
    assert (published_cmp.changed, published_cmp.removed) == ([DOC], [APP])
    published, draft = _keys(app, space, workdir)
    for key in (published, draft):
        files = await _files(app, key)
        assert APP not in files
        assert files[DOC][1] == "a_valider"
        assert files[REL][1] == "a_valider"  # elle citait les deux


async def test_vider_puis_reconstruire(app: FastAPI, space: ApiSpace) -> None:
    """Critères 4 et 5 : vider l'index puis le reconstruire depuis Git donne le même
    contenu indexé ; rien n'y est stocké qui ne se déduise de Git (la validation, pas
    encore faite, est vide)."""
    workdir = await admin_workdir(app, space)
    await _write(
        workdir,
        space,
        {"cartographie/objets/application/app-02.yaml": _object("APP-02", "application", "Stock")},
    )
    await _compare(workdir, space)

    async def snapshot() -> Any:
        async with app.state.pool.acquire() as conn:
            contents = await conn.fetch(
                "SELECT path, blob, documents::text AS d, keys, refs, problems, rules_version "
                "FROM doc_index_content ORDER BY path, blob"
            )
            views = await conn.fetch(
                "SELECT v.user_id, f.path, c.blob FROM doc_index_file f "
                "JOIN doc_index_view v ON v.id = f.view_id "
                "JOIN doc_index_content c ON c.id = f.content_id ORDER BY 1, 2"
            )
        return [tuple(r) for r in contents], [tuple(r) for r in views]

    first = await snapshot()
    assert all(r[5] is None and r[6] is None for r in first[0])
    assert any(r[1] == "cartographie/objets/application/app-02.yaml" for r in first[1])
    async with app.state.pool.acquire() as conn:
        await db_clear_space(conn, uuid.UUID(space.space_id))
    await _compare(workdir, space)
    assert await snapshot() == first


async def test_brouillon_avant_toute_comparaison(app: FastAPI, space: ApiSpace) -> None:
    """Un enregistrement avant toute comparaison ne fait pas échouer l'écriture : la
    vue du brouillon garde ses fichiers à valider et attend sa comparaison."""
    workdir = await admin_workdir(app, space)
    await _write(workdir, space, {APP: _object("APP-01", "application", "Caisse 2")})
    _published, draft = _keys(app, space, workdir)
    files = await _files(app, draft)
    assert set(files) == {APP} and files[APP][1] == "a_valider"
    async with app.state.pool.acquire() as conn:
        compared = await conn.fetchval(
            "SELECT compared_at FROM doc_index_view WHERE user_id = $1", workdir.user_id
        )
    assert compared is None
    await _compare(workdir, space)
    assert {APP, DOC, REL} <= set(await _files(app, draft))
