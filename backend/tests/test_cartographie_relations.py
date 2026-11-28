"""Relations de la cartographie côté serveur (ticket b58a0a13) : types permis
entre deux objets, créer une relation (son propre fichier), sa fiche (extrémités
et règle ArchiMate), la modifier, la supprimer."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient, Response

from tests.space_fixture import ApiSpace, forge_edit, make_api_space

TYPES = {
    "acteur": ("Acteur", "BusinessActor"),
    "processus-de-vente": ("Processus de vente", "BusinessProcess"),
    "document-metier": ("Document métier", "BusinessObject"),
    "acces": ("Accès", "Access"),
    "affectation": ("Affectation", "Assignment"),
    "composition": ("Composition", "Composition"),
    "association": ("Association", "Association"),
    "flux": ("Flux", "Flow"),
}


@pytest.fixture
async def space(app: FastAPI, client: AsyncClient, tmp_path: Path) -> ApiSpace:
    space = await make_api_space(client, app, tmp_path)
    files: dict[str, str | None] = {
        f"cartographie/types/{slug}.schema.yaml": (
            f"title: {title}\nx-archimate: {arch}\ntype: object\n"
        )
        for slug, (title, arch) in TYPES.items()
    }
    for key, ctype, name in (
        ("ACT-CLIENT", "acteur", "Client"),
        ("PRC-PRISE-COMMANDE", "processus-de-vente", "Prise de commande"),
        ("PRC-PAIEMENT", "processus-de-vente", "Paiement"),
        ("OBJ-COMMANDE", "document-metier", "Commande"),
    ):
        files[f"cartographie/objets/{ctype}/{key.lower()}.yaml"] = (
            f"cle: {key}\ntype: {ctype}\nnom: {name}\n"
        )
    files["cartographie/objets/flux/rel-flux-paiement.yaml"] = (
        "cle: REL-FLUX-PAIEMENT\ntype: flux\nsource: PRC-PRISE-COMMANDE\ncible: PRC-PAIEMENT\n"
        "nom: Transmet au paiement\n"
    )
    forge_edit(space, files)
    return space


def _url(space: ApiSpace, rest: str = "") -> str:
    return f"{space.base}/cartographie/objets{rest}"


async def _permitted(space: ApiSpace, source: str, target: str) -> dict[str, list[Any]]:
    answer = await space.client.get(
        _url(space, f"/{source}/relations-permises"), params={"cible": target}
    )
    assert answer.status_code == 200, answer.text
    data: dict[str, list[Any]] = answer.json()
    return data


async def _relate(space: ApiSpace, **body: object) -> Response:
    return await space.client.post(_url(space), json=body)


async def test_acteur_vers_processus(space: ApiSpace) -> None:
    """Critère 1 : affectation proposée, composition absente."""
    data = await _permitted(space, "acteur/ACT-CLIENT", "processus-de-vente/PRC-PRISE-COMMANDE")
    assert "Assignment" in data["archimate"] and "Composition" not in data["archimate"]
    slugs = [t["slug"] for t in data["types"]]
    assert "affectation" in slugs and "composition" not in slugs
    [affectation] = [t for t in data["types"] if t["slug"] == "affectation"]
    assert affectation["object_schema"]["additionalProperties"] is False


async def test_objet_vers_une_relation_de_flux(space: ApiSpace) -> None:
    """Critère 2 : d'un objet métier vers une relation de flux, l'association."""
    data = await _permitted(space, "document-metier/OBJ-COMMANDE", "flux/REL-FLUX-PAIEMENT")
    assert data["archimate"] == ["Association"]
    assert [t["slug"] for t in data["types"]] == ["association"]
    missing = await space.client.get(
        _url(space, "/document-metier/OBJ-COMMANDE/relations-permises"),
        params={"cible": "document-metier/ABSENT"},
    )
    assert missing.status_code == 404 and missing.json()["error"] == "carto_object_not_found"


async def test_creer_un_acces_ecriture_puis_lecture(space: ApiSpace) -> None:
    """Critères 3, 4 et 6 : le sens est écrit avec sa valeur ArchiMate, dans le
    fichier propre de la relation ; la fiche le modifie."""
    created = await _relate(
        space,
        key="REL-PRISE-COMMANDE-COMMANDE",
        type="acces",
        name="Écrit la commande",
        properties={"acces": "Write"},
        source="PRC-PRISE-COMMANDE",
        target="OBJ-COMMANDE",
    )
    assert created.status_code == 201, created.text
    data = created.json()
    assert data["path"] == "cartographie/objets/acces/rel-prise-commande-commande.yaml"
    assert data["ends"]["source"]["name"] == "Prise de commande"
    assert data["ends"]["target"]["archimate"] == "BusinessObject"
    assert data["rule"] == ["Access", "Association"]
    changed = await space.client.put(
        _url(space, "/acces/REL-PRISE-COMMANDE-COMMANDE"),
        json={"name": "Lit la commande", "properties": {"acces": "Read"}},
    )
    assert changed.status_code == 200, changed.text
    source = (await space.client.get(_url(space, "/processus-de-vente/PRC-PRISE-COMMANDE"))).json()
    [row] = [
        r for r in source["relations"]["outgoing"] if r["key"] == "REL-PRISE-COMMANDE-COMMANDE"
    ]
    assert row["properties"] == {"acces": "Read"}


async def test_relation_non_permise_refusee(space: ApiSpace) -> None:
    refused = await _relate(
        space,
        key="REL-X",
        type="composition",
        name="X",
        source="ACT-CLIENT",
        target="PRC-PRISE-COMMANDE",
    )
    assert refused.status_code == 400
    assert {p["code"] for p in refused.json()["error_args"]["problems"]} == {"relation_not_allowed"}


async def test_relation_cible_d_une_association_non_supprimable(space: ApiSpace) -> None:
    """Critère 5 : la relation cible d'une association ne se supprime pas ;
    l'association est nommée."""
    created = await _relate(
        space,
        key="REL-NOTE",
        type="association",
        name="Note",
        source="OBJ-COMMANDE",
        target="REL-FLUX-PAIEMENT",
    )
    assert created.status_code == 201, created.text
    flux = (await space.client.get(_url(space, "/flux/REL-FLUX-PAIEMENT"))).json()
    assert [r["key"] for r in flux["relations"]["incoming"]] == ["REL-NOTE"]
    refused = await space.client.delete(_url(space, "/flux/REL-FLUX-PAIEMENT"))
    assert refused.status_code == 409
    assert refused.json()["error_args"]["relations"] == ["association/REL-NOTE"]
    assert (await space.client.delete(_url(space, "/association/REL-NOTE"))).status_code == 200
    assert (await space.client.delete(_url(space, "/flux/REL-FLUX-PAIEMENT"))).status_code == 200
