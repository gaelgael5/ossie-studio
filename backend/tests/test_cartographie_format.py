"""Format des types et des objets de cartographie dans l'espace Git (epic
« Cartographie — types et objets », enabler f8167ae1)."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from ossie_studio.cartographie.catalog import catalog
from ossie_studio.cartographie.errors import CartographieError
from ossie_studio.cartographie.history import file_dates
from ossie_studio.cartographie.model import (
    CartoObject,
    CartoType,
    dump_object,
    object_path,
    parse_object,
    type_path,
)
from ossie_studio.cartographie.service import CartographieTarget
from ossie_studio.cartographie.space import (
    delete_object,
    read_cartographie,
    save_object,
    save_type,
)
from ossie_studio.spaces.service import db_get_space
from ossie_studio.spaces.workdir import PathCommit
from tests.space_fixture import admin_workdir, make_api_space


def _codes(root: Path) -> set[tuple[str, str]]:
    return {(p.path, p.code) for p in read_cartographie(root).problems}


@pytest.fixture
def root(tmp_path: Path) -> Path:
    """Une copie de travail avec quelques types de base."""
    for slug, title, archimate, properties in (
        ("application", "Application", "ApplicationComponent", {}),
        (
            "processus-de-vente",
            "Processus de vente",
            "BusinessProcess",
            {
                "canal": {"type": "string", "enum": ["site", "magasin"]},
                "volume_jour": {"type": "integer"},
            },
        ),
        ("objet-metier", "Objet métier", "BusinessObject", {}),
        ("acces", "Accès", "Access", {}),
    ):
        save_type(tmp_path, CartoType(slug, title, archimate, properties), create=True)
    return tmp_path


# --- Critères 1 à 3 : types ----------------------------------------------------------------


def test_ajouter_un_type(root: Path) -> None:
    save_type(root, CartoType("atelier", "Atelier", "BusinessInteraction"), create=True)
    text = (root / "cartographie/types/atelier.schema.yaml").read_text()
    assert text == "title: Atelier\nx-archimate: BusinessInteraction\ntype: object\n"
    assert read_cartographie(root).types["atelier"].properties == {}


def test_deux_types_sur_le_meme_type_archimate(root: Path) -> None:
    save_type(
        root,
        CartoType("client", "Client", "BusinessActor", {"segment": {"type": "string"}}),
        create=True,
    )
    save_type(
        root,
        CartoType(
            "service-interne", "Service interne", "BusinessActor", {"direction": {"type": "string"}}
        ),
        create=True,
    )
    types = read_cartographie(root).types
    assert (types["client"].archimate, types["service-interne"].archimate) == (
        "BusinessActor",
        "BusinessActor",
    )
    assert set(types["client"].properties) == {"segment"}
    assert set(types["service-interne"].properties) == {"direction"}


def test_renommer_un_type(root: Path) -> None:
    save_object(root, CartoObject("APP-BOUTIQUE", "application", "Boutique"), create=True)
    save_type(
        root, CartoType("application", "Application métier", "ApplicationComponent"), create=False
    )
    space = read_cartographie(root)
    assert space.types["application"].title == "Application métier"
    assert (root / type_path("application")).is_file()  # même fichier, même slug
    assert space.problems == []  # ses objets restent valides
    # Le type ArchiMate est figé, le slug aussi (un nouveau slug serait un autre type).
    with pytest.raises(CartographieError) as refused:
        save_type(root, CartoType("application", "X", "BusinessActor"), create=False)
    assert refused.value.code == "type_archimate_frozen"
    with pytest.raises(CartographieError) as taken:
        save_type(root, CartoType("application", "X", "ApplicationComponent"), create=True)
    assert taken.value.code == "type_exists"


# --- Critère 4 : un document, un objet ------------------------------------------------------


def test_creer_un_objet(root: Path) -> None:
    save_object(root, CartoObject("APP-BOUTIQUE", "application", "Boutique"), create=True)
    path = root / "cartographie/objets/application/app-boutique.yaml"
    assert path.read_text() == "cle: APP-BOUTIQUE\ntype: application\nnom: Boutique\n"
    # Clé unique dans son type (ticket 421832ee) : un autre type peut la porter.
    with pytest.raises(CartographieError) as taken:
        save_object(root, CartoObject("APP-BOUTIQUE", "application", "Autre"), create=True)
    assert (taken.value.code, taken.value.detail["type"]) == ("object_key_taken", "application")
    save_object(root, CartoObject("APP-BOUTIQUE", "objet-metier", "Autre"), create=True)
    assert set(read_cartographie(root).objects) == {
        "application/APP-BOUTIQUE",
        "objet-metier/APP-BOUTIQUE",
    }
    with pytest.raises(CartographieError) as missing:
        save_object(root, CartoObject("APP-BOUTIQUE", "acces", "Autre"), create=False)
    assert missing.value.code == "object_not_found"
    with pytest.raises(CartographieError) as invalid:
        save_object(root, CartoObject("app boutique", "application", "X"), create=True)
    assert invalid.value.code == "object_invalid"


# --- Critères 5 à 8 : signalements à la lecture --------------------------------------------


def _write(root: Path, rel: str, text: str) -> None:
    (root / rel).parent.mkdir(parents=True, exist_ok=True)
    (root / rel).write_text(text)


def test_cle_et_dossier_incoherents(root: Path) -> None:
    _write(
        root,
        "cartographie/objets/application/autre-nom.yaml",
        "cle: APP-X\ntype: application\nnom: X\n",
    )
    _write(
        root,
        "cartographie/objets/objet-metier/app-y.yaml",
        "cle: APP-Y\ntype: application\nnom: Y\n",
    )
    _write(root, "cartographie/objets/perdu.yaml", "cle: PERDU\ntype: application\nnom: P\n")
    codes = _codes(root)
    assert ("cartographie/objets/application/autre-nom.yaml", "object_key_mismatch") in codes
    assert ("cartographie/objets/objet-metier/app-y.yaml", "object_wrong_folder") in codes
    assert ("cartographie/objets/perdu.yaml", "object_misplaced") in codes


def test_cle_en_double_bloque_les_ecritures(root: Path) -> None:
    """Une clé en double l'est dans un même type (ici, un second fichier rangé ailleurs
    déclare le même type) ; deux types qui portent la même clé n'en sont pas une."""
    _write(
        root,
        "cartographie/objets/application/app-a.yaml",
        "cle: APP-A\ntype: application\nnom: A\n",
    )
    _write(
        root,
        "cartographie/objets/objet-metier/app-a.yaml",
        "cle: APP-A\ntype: objet-metier\nnom: A bis\n",
    )
    assert not {c for _p, c in _codes(root)} & {"object_key_duplicate"}
    _write(
        root,
        "cartographie/objets/objet-metier/app-a.yaml",
        "cle: APP-A\ntype: application\nnom: A bis\n",
    )
    codes = _codes(root)
    assert ("cartographie/objets/application/app-a.yaml", "object_key_duplicate") in codes
    assert ("cartographie/objets/objet-metier/app-a.yaml", "object_key_duplicate") in codes
    with pytest.raises(CartographieError) as refused:
        save_object(root, CartoObject("APP-B", "application", "B"), create=True)
    assert (refused.value.code, refused.value.status) == ("object_keys_duplicate", 409)
    assert not (root / object_path("application", "APP-B")).exists()


def test_relation_vers_une_cle_absente(root: Path) -> None:
    _write(
        root,
        "cartographie/objets/acces/rel-x.yaml",
        "cle: REL-X\ntype: acces\nsource: PRC-ABSENT\ncible: OBJ-ABSENT\nnom: X\n",
    )
    problems = read_cartographie(root).problems
    found = {(p.code, p.field, p.detail) for p in problems}
    assert ("relation_endpoint_unknown", "source", "PRC-ABSENT") in found
    assert ("relation_endpoint_unknown", "cible", "OBJ-ABSENT") in found


def test_proprietes_hors_schema(root: Path) -> None:
    _write(
        root,
        "cartographie/objets/processus-de-vente/prc-x.yaml",
        "cle: PRC-X\ntype: processus-de-vente\nnom: X\n"
        "proprietes:\n  canal: telephone\n  volume_jour: beaucoup\n  inconnue: 1\n",
    )
    found = {(p.code, p.field) for p in read_cartographie(root).problems}
    assert ("property_invalid", "canal") in found
    assert ("property_invalid", "volume_jour") in found
    assert ("property_unknown", "inconnue") in found


# --- Critères 9 à 11 : valeurs ArchiMate, documentation, déterminisme -----------------------


def test_relation_acces_ecriture(root: Path) -> None:
    save_object(
        root,
        CartoObject(
            "PRC-PRISE-COMMANDE",
            "processus-de-vente",
            "Prise de commande",
            properties={"canal": "site", "volume_jour": 160},
        ),
        create=True,
    )
    save_object(root, CartoObject("OBJ-COMMANDE", "objet-metier", "Commande"), create=True)
    relation = CartoObject(
        "REL-PRISE-COMMANDE-ECRIT-COMMANDE",
        "acces",
        "Écrit la commande",
        properties={"acces": "Write"},
        source="PRC-PRISE-COMMANDE",
        target="OBJ-COMMANDE",
    )
    save_object(root, relation, create=True)
    text = (root / "cartographie/objets/acces/rel-prise-commande-ecrit-commande.yaml").read_text()
    # Les extrémités sont écrites `type/CLÉ` (ticket 421832ee), même données en clé seule.
    assert text == (
        "cle: REL-PRISE-COMMANDE-ECRIT-COMMANDE\ntype: acces\n"
        "source: processus-de-vente/PRC-PRISE-COMMANDE\ncible: objet-metier/OBJ-COMMANDE\n"
        "nom: Écrit la commande\nproprietes:\n  acces: Write\n"
    )
    # Le libellé français vient du catalogue ; seule la valeur ArchiMate est stockée.
    access = catalog().get("Access")
    assert access is not None and access.attributes[0].values["Write"] == "écriture"
    with pytest.raises(CartographieError):
        save_object(
            root,
            CartoObject(
                "REL-Y",
                "acces",
                "Y",
                properties={"acces": "écriture"},
                source="PRC-PRISE-COMMANDE",
                target="OBJ-COMMANDE",
            ),
            create=True,
        )
    # Un objet encore utilisé par une relation ne peut pas être supprimé.
    with pytest.raises(CartographieError) as used:
        delete_object(root, "objet-metier/OBJ-COMMANDE")
    assert used.value.code == "object_in_use"
    delete_object(root, "acces/REL-PRISE-COMMANDE-ECRIT-COMMANDE")
    delete_object(root, "objet-metier/OBJ-COMMANDE")
    assert not (root / object_path("objet-metier", "OBJ-COMMANDE")).exists()


def test_documentation_markdown_relue_a_l_identique(root: Path) -> None:
    doc = (
        "Du panier validé jusqu'à la commande **enregistrée**.\n\n- contrôle du stock ;\n- envoi.\n"
    )
    save_object(root, CartoObject("APP-DOC", "application", "Doc", documentation=doc), create=True)
    read = read_cartographie(root).objects["application/APP-DOC"][1]
    assert read.documentation == doc


def test_deux_ecritures_identiques_memes_octets() -> None:
    obj = CartoObject("PRC-A", "processus-de-vente", "A", properties={"volume_jour": 3, "x": ""})
    assert dump_object(obj) == dump_object(parse_object(dump_object(obj)))
    assert "x:" not in dump_object(obj)  # propriété facultative vide : pas écrite


# --- Dates et auteurs : l'historique Git ----------------------------------------------------


def test_dates_tirees_de_l_historique() -> None:
    a = "cartographie/objets/application/app-a.yaml"
    b = "cartographie/objets/application/app-b.yaml"
    commits = [  # du plus récent au plus ancien
        PathCommit("3", "Lea", "2026-11-25T10:00:00", "m", (("M", a), ("D", b))),
        PathCommit("2", "Gael", "2026-11-24T10:00:00", "m", (("A", b),)),
        PathCommit("1", "Gael", "2026-11-23T10:00:00", "m", (("A", a),)),
    ]
    dates = file_dates(commits)
    assert set(dates) == {a}  # b supprimé ; un fichier du seul brouillon n'y est pas
    assert (dates[a].created_by, dates[a].created_at) == ("Gael", "2026-11-23T10:00:00")
    assert (dates[a].updated_by, dates[a].updated_at) == ("Lea", "2026-11-25T10:00:00")


# --- Écriture dans le brouillon de la personne ----------------------------------------------


async def test_ecriture_dans_le_brouillon(
    app: FastAPI, client: AsyncClient, tmp_path: Path
) -> None:
    space = await make_api_space(client, app, tmp_path)
    workdir = await admin_workdir(app, space)
    async with app.state.pool.acquire() as conn:
        git_space = await db_get_space(conn, uuid.UUID(space.space_id))
    assert git_space is not None
    target = CartographieTarget(workdir, git_space, "work/principale", "x", None, "a@a")
    _ctype, revision = await target.write(
        "type atelier",
        lambda r: save_type(r, CartoType("atelier", "Atelier", "BusinessInteraction"), create=True),
    )
    assert revision is not None
    space_read = await target.cartographie()
    assert space_read.types["atelier"].archimate == "BusinessInteraction"


def test_exemples_des_conventions() -> None:
    """Les exemples de `docs/conventions-cartographie.md` sont ceux que le code écrit."""
    import re

    doc = (Path(__file__).resolve().parents[2] / "docs" / "conventions-cartographie.md").read_text()
    blocks = re.findall(r"```yaml\n(.*?)```", doc, re.DOTALL)
    objects = [b for b in blocks if b.startswith("cle:")]
    assert len(objects) == 2
    for block in objects:
        assert dump_object(parse_object(block)) == block
    [ctype_block] = [b for b in blocks if b.startswith("title:")]
    from ossie_studio.cartographie.model import dump_type, parse_type

    assert dump_type(parse_type("processus-de-vente", ctype_block)) == ctype_block


def test_relation_en_cle_seule_ambigue_signalee_sans_bloquer(root: Path) -> None:
    """Ticket 421832ee : une relation écrite en clé seule (forme d'avant) se lit tant
    que la clé n'existe que dans un type ; présente dans deux types, elle est signalée
    sur la relation, et le reste de l'espace s'écrit toujours."""
    _write(root, "cartographie/objets/processus-de-vente/prc-a.yaml",
           "cle: PRC-A\ntype: processus-de-vente\nnom: A\n")  # fmt: skip
    _write(root, "cartographie/objets/objet-metier/smo.yaml",
           "cle: SMO\ntype: objet-metier\nnom: Fiche\n")  # fmt: skip
    _write(root, "cartographie/objets/acces/rel-lit.yaml",
           "cle: REL-LIT\ntype: acces\nsource: PRC-A\ncible: SMO\nnom: Lit\n")  # fmt: skip
    space = read_cartographie(root)
    assert space.problems == []
    assert space.users("objet-metier/SMO") == ["acces/REL-LIT"]
    save_object(root, CartoObject("SMO", "application", "SMO"), create=True)
    assert ("cartographie/objets/acces/rel-lit.yaml", "relation_endpoint_ambiguous") in _codes(root)
    save_object(root, CartoObject("APP-B", "application", "B"), create=True)  # rien ne bloque
    # Désignée par `type/CLÉ`, l'extrémité n'est plus ambiguë.
    relation = CartoObject("REL-LIT", "acces", "Lit", source="PRC-A", target="objet-metier/SMO")
    written = save_object(root, relation, create=False)
    assert (written.source, written.target) == ("processus-de-vente/PRC-A", "objet-metier/SMO")
    assert not {c for _p, c in _codes(root)} & {"relation_endpoint_ambiguous"}
