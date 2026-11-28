"""Règles de validation par type de document et règle d'or de la publication (ticket
d7139b17, epic « Index des documents et validation avant publication »).

Gael : « La validation dépend en premier du type de document. On valide le document,
les références et les règles entre fichiers. » et « tu ne peux pousser que des choses
valides et conformes »."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from ossie_studio.doc_index.rules import CartoRule, ReferentielRule, level_for
from ossie_studio.domains.documents import Dataset, Domain, DomainField, write_domain
from ossie_studio.mcp_surface.document_types import DOCUMENT_TYPES
from tests.space_fixture import ApiSpace, draft_write, forge_edit, make_api_space, set_credential

RELATION = "cartographie/objets/acces/rel-01.yaml"
DOCUMENT = "cartographie/objets/document/doc-01.yaml"


# --- Déclarations et niveaux ----------------------------------------------------------------


def test_chaque_type_declare_ses_regles() -> None:
    declared = {t.name: {d.rule: d.level for d in t.validation} for t in DOCUMENT_TYPES}
    assert all(declared.values()), [n for n, d in declared.items() if not d]
    assert declared["dataset"]["dataset_description"] == "error"
    assert declared["carto_objet"]["references"] == "error"
    assert declared["schema_raw"] == {"parseable": "warning", "broken_ref": "warning"}
    assert declared["schema_asset"]["orphan_asset"] == "warning"


def test_niveau_selon_le_type_de_document() -> None:
    raw = [{"document_type": "schema_raw"}]
    asset = [{"document_type": "schema_asset"}]
    assert level_for("broken_ref", "error", "sources/s/raw/A.schema.yaml", raw) == "warning"
    assert level_for("broken_ref", "error", "sources/s/assets/A.schema.yaml", asset) == "error"
    # Un fichier raw sans type (une vue de module) ne bloque jamais non plus.
    assert level_for("parseable", "error", "sources/s/raw/m.view.yaml", []) == "warning"
    # Un fichier hors registre (déclaration d'import) garde le niveau de la règle.
    assert level_for("parseable", "error", "cartographie/imports/a/f.yaml", []) == "error"
    # Le niveau déclaré est le plus haut : un avertissement reste un avertissement.
    assert (
        level_for("domain", "warning", "domaines/d.extend.yaml", [{"document_type": "domaine"}])
        == "warning"
    )


def test_regle_cartographie(tmp_path: Path) -> None:
    def put(rel: str, text: str) -> None:
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text)

    put(
        "cartographie/types/processus.schema.yaml",
        "title: P\nx-archimate: BusinessProcess\ntype: object\n",
    )
    put(
        "cartographie/types/composition.schema.yaml",
        "title: C\nx-archimate: Composition\ntype: object\n",
    )
    put(
        "cartographie/types/application.schema.yaml",
        "title: A\nx-archimate: ApplicationComponent\ntype: object\n",
    )
    put("cartographie/objets/processus/prc-x.yaml", "cle: PRC-X\ntype: processus\nnom: X\n")
    put("cartographie/objets/application/app-x.yaml", "cle: APP-X\ntype: application\nnom: A\n")
    # Un processus métier ne compose pas une application : relation non permise.
    put(
        "cartographie/objets/composition/rel-x.yaml",
        "cle: REL-X\ntype: composition\nsource: processus/PRC-X\n"
        "cible: application/APP-X\nnom: R\n",
    )
    found = {(f.path, f.message_code, f.level) for f in CartoRule().check(tmp_path)}
    assert ("cartographie/objets/composition/rel-x.yaml", "relation_not_allowed", "error") in found


def test_regle_referentiel(tmp_path: Path) -> None:
    folder = tmp_path / "referentiels/pays"
    folder.mkdir(parents=True)
    (folder / "modele.schema.yaml").write_text(
        "title: Pays\ntype: object\nx-key: [code]\nrequired: [code]\n"
        "properties:\n  code:\n    type: string\n"
    )
    (folder / "valeurs.json").write_text(json.dumps([{"code": 3}]))
    [finding] = ReferentielRule().check(tmp_path)
    assert (finding.path, finding.level) == ("referentiels/pays/valeurs.json", "error")


# --- Sur un espace : le journal de la publication ------------------------------------------


@pytest.fixture
async def space(app: FastAPI, client: AsyncClient, tmp_path: Path) -> ApiSpace:
    api_space = await make_api_space(client, app, tmp_path)
    domain_root = tmp_path / "domaine"
    write_domain(
        domain_root,
        Domain(
            name="ventes",
            label="Ventes",
            datasets=[
                Dataset("Commandes", "raw:boutique/commandes", True, False,
                        description="Les commandes.", fields=[DomainField("id", "id")]),
                Dataset("Clients", "raw:boutique/clients", True, False,
                        fields=[DomainField("id", "id")]),
            ],
        ),
    )  # fmt: skip
    files: dict[str, str | None] = {
        p.relative_to(domain_root).as_posix(): p.read_text()
        for p in domain_root.rglob("*")
        if p.is_file()
    }
    types = {
        "document": ("Document", "BusinessObject"),
        "processus": ("Processus", "BusinessProcess"),
        "acces": ("Accès", "Access"),
    }
    for slug, (title, arch) in types.items():
        files[f"cartographie/types/{slug}.schema.yaml"] = (
            f"title: {title}\nx-archimate: {arch}\ntype: object\n"
        )
    files["cartographie/objets/processus/prc-01.yaml"] = "cle: PRC-01\ntype: processus\nnom: P\n"
    files[DOCUMENT] = "cle: DOC-01\ntype: document\nnom: D\n"
    files[RELATION] = (
        "cle: REL-01\ntype: acces\nsource: processus/PRC-01\ncible: document/DOC-01\nnom: Lit\n"
    )
    forge_edit(api_space, files)
    return api_space


async def _journal(space: ApiSpace) -> dict[str, Any]:
    answer = await space.client.get(f"{space.base}/draft/validation")
    assert answer.status_code == 200, answer.text
    body: dict[str, Any] = answer.json()
    return body


async def test_dataset_sans_description_bloque(app: FastAPI, space: ApiSpace) -> None:
    """Critère 1 : un dataset sans description est en erreur, avec un message qui le
    dit, et bloque la publication."""
    log = await _journal(space)
    [entry] = [e for e in log["entries"] if e["rule"] == "dataset_description"]
    assert (entry["level"], entry["blocking"], entry["document_type"]) == ("error", True, "dataset")
    assert (entry["message_code"], entry["args"]) == (
        "dataset_description_missing",
        {"dataset": "Clients"},
    )
    await draft_write(app, space, "sources/x/manifest.json", {"a": 1})
    await set_credential(app, space)
    readiness = (await space.client.get(f"{space.base}/draft/readiness")).json()
    assert "validation_errors" in readiness["reasons"]


async def test_relation_vers_un_objet_supprime(app: FastAPI, space: ApiSpace) -> None:
    """Critère 3 : une relation qui cite un objet supprimé dans le brouillon est en
    erreur (référence cassée), avec un lien vers la relation."""
    assert not [e for e in (await _journal(space))["entries"] if e["rule"] == "references"]
    await draft_write(app, space, DOCUMENT, None)
    log = await _journal(space)
    [entry] = [e for e in log["entries"] if e["rule"] == "references"]
    assert (entry["path"], entry["level"], entry["blocking"]) == (RELATION, "error", True)
    assert entry["args"] == {"kind": "carto_objet", "ref": "document/DOC-01"}
    assert (entry["document_type"], entry["identity"]) == (
        "carto_objet",
        {"type": "acces", "key": "REL-01"},
    )


async def test_raw_illisible_jamais_bloquant(app: FastAPI, space: ApiSpace) -> None:
    """Critère 2 : un schéma raw illisible ou une vue de module illisible informent,
    sans bloquer."""
    forge_edit(
        space,
        {
            "sources/s/raw/Order.schema.yaml": "title: [ouvert\n",
            "sources/s/raw/module-a.view.yaml": "a: [b\n",
        },
    )
    log = await _journal(space)
    raw = [e for e in log["entries"] if e["path"].startswith("sources/s/raw/")]
    assert {e["path"] for e in raw} == {
        "sources/s/raw/Order.schema.yaml",
        "sources/s/raw/module-a.view.yaml",
    }
    assert all(e["level"] == "warning" and not e["blocking"] for e in raw)
