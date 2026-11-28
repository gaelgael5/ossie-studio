"""Format et rangement d'un processus BPMN (epic « Cartographie — processus BPMN »,
enabler 1d35290d) : lecture, rattachements par l'extension d'urban-bpmn, contrôles,
noms à jour, retouches ciblées qui gardent le fichier tel quel."""

from __future__ import annotations

from pathlib import Path

import pytest

from ossie_studio.cartographie.errors import CartographieError
from ossie_studio.cartographie.model import CartoObject, CartoType
from ossie_studio.cartographie.process import (
    URBAN_NS,
    delete_process,
    list_processes,
    new_process_xml,
    parse_process,
    process_path,
    processes_detailing,
    read_process,
    referenced_objects,
    save_process,
    with_definitions_id,
    with_detail,
)
from ossie_studio.cartographie.space import (
    delete_object,
    read_cartographie,
    save_object,
    save_type,
)

KEY = "PRC-VENTE-DETAIL"
# Exemples réalistes (tests/fixtures/bpmn) :
# - exporté de bpmn.io puis rattaché par urban-bpmn : collaboration, deux piscines, un
#   couloir, un objet de données ; détail de PRC-PRISE-COMMANDE ;
# - style Camunda Modeler : préfixe `bpmn2`, extensions maison, commentaire ;
# - sans préfixe (espace de noms par défaut), processus en une balise, sans id.
FIXTURES = Path(__file__).parent / "fixtures" / "bpmn"
BPMN_IO = (FIXTURES / "bpmn-io-rattache.bpmn").read_text(encoding="utf-8")
CAMUNDA = (FIXTURES / "camunda.bpmn").read_text(encoding="utf-8")
DEFAULT_NS = (FIXTURES / "sans-prefixe.bpmn").read_text(encoding="utf-8")


@pytest.fixture
def root(tmp_path: Path) -> Path:
    for slug, title, archimate in (
        ("processus-metier", "Processus métier", "BusinessProcess"),
        ("collaboration", "Collaboration", "BusinessCollaboration"),
        ("acteur", "Acteur", "BusinessActor"),
        ("role", "Rôle", "BusinessRole"),
        ("objet-metier", "Objet métier", "BusinessObject"),
    ):
        save_type(tmp_path, CartoType(slug, title, archimate), create=True)
    for key, ctype, name in (
        ("PRC-PRISE-COMMANDE", "processus-metier", "Prise de commande"),
        ("COL-VENTE-EN-LIGNE", "collaboration", "Vente en ligne"),
        ("ACT-CLIENT", "acteur", "Client"),
        ("ROL-VENDEUR", "role", "Vendeur"),
        ("OBJ-COMMANDE", "objet-metier", "Commande"),
    ):
        save_object(tmp_path, CartoObject(key, ctype, name), create=True)
    return tmp_path


def _codes(root: Path, key: str = KEY) -> set[tuple[str, str, str]]:
    return {(p.code, p.detail, p.field) for p in read_process(root, key).problems}


# --- Lecture -----------------------------------------------------------------------------


def test_lire_un_processus_de_bpmn_io() -> None:
    process = parse_process(BPMN_IO)
    assert (process.id, process.name) == (KEY, "Prise de commande")
    assert (process.participants, process.lanes, process.elements, process.flows) == (2, 1, 2, 2)
    assert process.detail_of == "PRC-PRISE-COMMANDE"
    assert [r.key for r in process.collaborations] == ["COL-VENTE-EN-LIGNE"]
    assert [(r.kind, r.element, r.key, r.type) for r in process.attached()] == [
        ("participant", "Pool_Client", "ACT-CLIENT", "acteur"),
        ("lane", "Lane_Vendeur", "ROL-VENDEUR", "role"),
        ("data_object", "Data_1", "OBJ-COMMANDE", "objet-metier"),
    ]
    assert process.has_diagram and len(process.shapes) == 3
    assert process.shapes[2].kind == "task" and process.shapes[2].name == "Saisir la commande"
    assert process.edges[0].points == ((268.0, 300.0), (320.0, 300.0))


def test_fichiers_illisibles_ou_qui_ne_sont_pas_du_bpmn() -> None:
    with pytest.raises(CartographieError) as broken:
        parse_process("<bpmn:definitions")
    assert broken.value.code == "process_unreadable" and broken.value.detail["detail"]
    with pytest.raises(CartographieError) as other:
        parse_process("<svg xmlns='http://www.w3.org/2000/svg'/>")
    assert other.value.code == "process_not_bpmn"
    with pytest.raises(CartographieError) as bomb:  # entités : refusées (defusedxml)
        parse_process('<!DOCTYPE x [<!ENTITY a "aaaa">]><x>&a;</x>')
    assert bomb.value.code == "process_unreadable"


# --- Critères 2 à 4 ------------------------------------------------------------------------


def test_les_rattachements_gardent_leur_cle(root: Path) -> None:
    """Critère 2 : couloir et collaboration gardent leur clé après écriture et relecture."""
    save_process(root, KEY, BPMN_IO, create=True)
    read = read_process(root, KEY)
    assert read.problems == [] and read.process is not None
    assert (root / process_path(KEY)).read_text(encoding="utf-8") == BPMN_IO  # tel quel
    lane = next(r for r in read.process.references if r.kind == "lane")
    assert lane.key == "ROL-VENDEUR"
    assert read.process.collaborations[0].key == "COL-VENTE-EN-LIGNE"
    assert list_processes(root) == [KEY]


def test_le_nom_a_jour_vient_de_l_objet(root: Path) -> None:
    """Critère 3 : renommer ROL-VENDEUR ; à l'ouverture, le composant reçoit le nouveau
    nom (il l'applique au couloir), même si le XML porte l'ancien."""
    save_process(root, KEY, BPMN_IO, create=True)
    save_object(root, CartoObject("ROL-VENDEUR", "role", "Vendeur conseil"), create=False)
    read = read_process(root, KEY)
    assert read.process is not None
    names = {o["cle"]: o for o in referenced_objects(read.process, read_cartographie(root))}
    assert names["ROL-VENDEUR"] == {"cle": "ROL-VENDEUR", "nom": "Vendeur conseil", "type": "role"}
    assert set(names) == {
        "PRC-PRISE-COMMANDE", "COL-VENTE-EN-LIGNE", "ACT-CLIENT", "ROL-VENDEUR", "OBJ-COMMANDE"
    }  # fmt: skip


def test_reference_vers_une_cle_supprimee(root: Path) -> None:
    """Critère 4."""
    save_process(root, KEY, BPMN_IO, create=True)
    delete_object(root, "objet-metier/OBJ-COMMANDE")
    assert _codes(root) == {("process_reference_unknown", "OBJ-COMMANDE", "Data_1")}


def test_controles_des_types_et_de_la_collaboration(root: Path) -> None:
    wrong = (
        BPMN_IO.replace('cle="PRC-PRISE-COMMANDE"', 'cle="ACT-CLIENT"')
        .replace('cle="COL-VENTE-EN-LIGNE"', 'cle="ROL-VENDEUR"')
        .replace(
            '<bpmn:participant id="Pool_Boutique"',
            '<bpmn:collaboration id="Collab_2"><bpmn:extensionElements>'
            '<urban:reference cle="COL-VENTE-EN-LIGNE" /></bpmn:extensionElements>'
            '</bpmn:collaboration><bpmn:participant id="Pool_Boutique"',
        )
    )
    path = root / process_path(KEY)
    path.parent.mkdir(parents=True)
    path.write_text(wrong.replace(f'id="{KEY}"', 'id="AUTRE"'), encoding="utf-8")
    assert _codes(root) == {
        ("process_detail_not_business_process", "ACT-CLIENT", "Process_1"),
        ("process_collaboration_not_business_collaboration", "ROL-VENDEUR", "Collab_1"),
        ("process_collaboration_multiple", "COL-VENTE-EN-LIGNE", "Collab_2"),
        ("process_id_mismatch", "AUTRE", "definitions"),
    }


def test_ecrire_refuse_ce_qui_ne_passe_pas(root: Path) -> None:
    for text, code in (("pas du xml", "process_unreadable"), ("<a/>", "process_not_bpmn")):
        with pytest.raises(CartographieError) as refused:
            save_process(root, KEY, text, create=True)
        assert refused.value.code == code
    no_diagram = BPMN_IO.split("<bpmndi:BPMNDiagram")[0] + "</bpmn:definitions>\n"
    with pytest.raises(CartographieError) as missing:
        save_process(root, KEY, no_diagram, create=True)
    assert missing.value.code == "process_invalid"
    assert {p.code for p in missing.value.problems} == {"process_no_diagram"}
    with pytest.raises(CartographieError) as unknown:
        save_process(root, KEY, BPMN_IO.replace("ACT-CLIENT", "ACT-INCONNU"), create=True)
    assert {p.detail for p in unknown.value.problems} == {"ACT-INCONNU"}
    with pytest.raises(CartographieError) as bad_key:
        save_process(root, "pas une clé", BPMN_IO, create=True)
    assert bad_key.value.code == "process_key_invalid"
    assert not (root / process_path(KEY)).exists()  # rien n'est écrit
    save_process(root, KEY, BPMN_IO, create=True)
    with pytest.raises(CartographieError) as taken:
        save_process(root, KEY, BPMN_IO, create=True)
    assert taken.value.code == "process_exists" and taken.value.status == 409


# --- Retouches ciblées : le reste du fichier ne bouge pas ---------------------------------


def test_id_du_document_aligne_sur_la_cle() -> None:
    changed = with_definitions_id(CAMUNDA, "PRC-RETOUR")
    assert changed == CAMUNDA.replace("id='Definitions_9'", "id='PRC-RETOUR'")
    added = with_definitions_id(DEFAULT_NS, "PRC-VIDE")
    assert added == DEFAULT_NS.replace("<definitions ", '<definitions id="PRC-VIDE" ', 1)
    assert parse_process(added).id == "PRC-VIDE"


def test_rattacher_au_processus_metier() -> None:
    # Extensions déjà là (Camunda) : la référence s'y ajoute, le reste est gardé.
    attached = with_detail(CAMUNDA, "PRC-PRISE-COMMANDE", "processus-metier")
    assert parse_process(attached).detail_of == "PRC-PRISE-COMMANDE"
    assert '<camunda:property name="sla" value="2j" />' in attached
    assert (
        attached.replace(
            f'<urban:reference xmlns:urban="{URBAN_NS}" cle="PRC-PRISE-COMMANDE" '
            'type="processus-metier"/>',
            "",
        )
        == CAMUNDA
    )
    # Rattacher ailleurs remplace ; retirer rend le fichier d'origine.
    moved = with_detail(attached, "PRC-AUTRE")
    assert parse_process(moved).detail_of == "PRC-AUTRE" and moved.count("urban:reference") == 1
    assert with_detail(moved, None) == CAMUNDA
    # Sans extension, processus en une balise, sans préfixe.
    single = with_detail(DEFAULT_NS, "PRC-X")
    assert parse_process(single).detail_of == "PRC-X"
    assert '<process id="P1" name="Vide"><extensionElements>' in single
    # bpmn.io : la référence existante est remplacée, pas doublée.
    replaced = with_detail(BPMN_IO, "PRC-NOUVEAU")
    assert parse_process(replaced).detail_of == "PRC-NOUVEAU"
    assert replaced.count('cle="PRC-PRISE-COMMANDE"') == 0


def test_un_processus_neuf(root: Path) -> None:
    text = new_process_xml("PRC-NEUF", 'Prise de commande — "détail"', "PRC-PRISE-COMMANDE")
    process = parse_process(text)
    assert (process.id, process.name, process.detail_of) == (
        "PRC-NEUF",
        'Prise de commande — "détail"',
        "PRC-PRISE-COMMANDE",
    )
    assert process.elements == 1 and process.has_diagram
    save_process(root, "PRC-NEUF", text, create=True)
    assert read_process(root, "PRC-NEUF").problems == []
    assert processes_detailing(root, "PRC-PRISE-COMMANDE") == ["PRC-NEUF"]
    delete_process(root, "PRC-NEUF")
    assert list_processes(root) == [] and "role/ROL-VENDEUR" in read_cartographie(root).objects
    with pytest.raises(CartographieError) as gone:
        read_process(root, "PRC-NEUF")
    assert gone.value.code == "process_not_found"


def test_conformite_au_schema_bpmn_20(tmp_path: Path) -> None:
    """Décision de Gael (27 novembre) : un fichier non conforme au schéma BPMN 2.0 est
    refusé par le service, pour tout écrivain ; le motif nomme la ligne."""
    from ossie_studio.cartographie.process import check_bpmn_schema

    good = new_process_xml("PRC-A", "A")
    check_bpmn_schema(good)  # conforme
    bad = good.replace("</bpmn:process>", "<bpmn:inconnu/></bpmn:process>")
    with pytest.raises(CartographieError) as refused:
        save_process(tmp_path, "PRC-A", bad, create=True)
    assert refused.value.code == "process_schema_invalid"
    assert "inconnu" in str(refused.value.detail["detail"])
    assert isinstance(refused.value.detail["line"], int)
    assert not (tmp_path / "cartographie/processus/prc-a.bpmn").exists()
