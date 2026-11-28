"""Fusion à trois par propriétés."""

from __future__ import annotations

import json

from ossie_studio.drafts.merge import MISSING, merge3, merge_object, set_pointer


def test_proprietes_differentes_fusion_auto() -> None:
    base = {"title": "A", "type": "object", "x": 1}
    theirs = {"title": "A publié", "type": "object", "x": 1}
    mine = {"title": "A", "type": "object", "x": 2}
    result = merge3(base, theirs, mine)
    assert result.conflicts == []
    assert result.merged == {"title": "A publié", "type": "object", "x": 2}


def test_meme_propriete_conflit_avec_pointeur() -> None:
    base = {"properties": {"x": {"type": "string"}}}
    theirs = {"properties": {"x": {"type": "integer"}}}
    mine = {"properties": {"x": {"type": "number"}}}
    result = merge3(base, theirs, mine)
    assert [c.pointer for c in result.conflicts] == ["/properties/x/type"]
    conflict = result.conflicts[0]
    assert (conflict.base, conflict.theirs, conflict.mine) == ("string", "integer", "number")


def test_liste_sans_identifiant_conflit_atomique() -> None:
    """Éléments sans clé commune : la liste reste indivisible (f9bd4d93)."""
    base = {"l": [{"x": 1}]}
    result = merge3(base, {"l": [{"x": 1}, {"x": 2}]}, {"l": [{"x": 1}, {"x": 3}]})
    assert [c.pointer for c in result.conflicts] == ["/l"]


def test_liste_de_tags_fusionnee_comme_un_ensemble() -> None:
    result = merge3({"tags": ["a"]}, {"tags": ["a", "b"]}, {"tags": ["a", "c"]})
    assert result.conflicts == []
    assert result.merged == {"tags": ["a", "b", "c"]}


def test_identifiants_en_double_liste_atomique() -> None:
    base = {"l": [{"name": "a"}]}
    theirs = {"l": [{"name": "a"}, {"name": "a", "v": 1}]}
    mine = {"l": [{"name": "a"}, {"name": "b"}]}
    assert [c.pointer for c in merge3(base, theirs, mine).conflicts] == ["/l"]


FIELDS_BASE = {
    "datasets": [
        {
            "name": "client",
            "fields": [
                {"name": "id_client", "type": "string"},
                {"name": "segment", "description": "Segment"},
                {"name": "date_creation", "type": "date"},
            ],
        }
    ]
}


def _fields(*items: dict[str, str]) -> dict[str, object]:
    return {"datasets": [{"name": "client", "fields": list(items)}]}


ID = {"name": "id_client", "type": "string"}
DATE = {"name": "date_creation", "type": "date"}
PAYS = {"name": "pays", "type": "string"}
EMAIL = {"name": "email_contact", "type": "string"}


def test_cas_de_la_maquette_un_seul_conflit_sur_segment() -> None:
    """Maquette du 28/11 : `pays` ajouté côté publié, `email_contact` dans le
    brouillon, `segment` modifié des deux côtés."""
    theirs = _fields(ID, {"name": "segment", "description": "Niveau client A/B/C"}, PAYS, DATE)
    mine = _fields(ID, {"name": "segment", "description": "Segment commercial"}, DATE, EMAIL)
    result = merge3(FIELDS_BASE, theirs, mine)
    assert [c.pointer for c in result.conflicts] == ["/datasets/client/fields/segment/description"]
    names = [f["name"] for f in result.merged["datasets"][0]["fields"]]
    assert names == ["id_client", "segment", "pays", "date_creation", "email_contact"]
    assert sorted(result.auto) == [
        "/datasets/client/fields/email_contact",
        "/datasets/client/fields/pays",
    ]


def test_element_supprime_d_un_cote_inchange_de_l_autre() -> None:
    theirs = _fields(ID, DATE)
    mine = _fields(ID, {"name": "segment", "description": "Segment"}, DATE)
    result = merge3(FIELDS_BASE, theirs, mine)
    assert result.conflicts == []
    assert [f["name"] for f in result.merged["datasets"][0]["fields"]] == [
        "id_client",
        "date_creation",
    ]


def test_element_supprime_d_un_cote_modifie_de_l_autre_conflit() -> None:
    theirs = _fields(ID, DATE)
    mine = _fields(ID, {"name": "segment", "description": "Autre"}, DATE)
    result = merge3(FIELDS_BASE, theirs, mine)
    assert [c.pointer for c in result.conflicts] == ["/datasets/client/fields/segment"]
    assert result.conflicts[0].theirs is MISSING


def test_element_ajoute_des_deux_cotes() -> None:
    same = merge3(FIELDS_BASE, _fields(ID, DATE, PAYS), _fields(ID, DATE, PAYS))
    assert same.conflicts == []
    theirs = _fields(ID, DATE, {"name": "pays", "type": "string"})
    mine = _fields(ID, DATE, {"name": "pays", "type": "code"})
    result = merge3({"datasets": [{"name": "client", "fields": [ID, DATE]}]}, theirs, mine)
    assert [c.pointer for c in result.conflicts] == ["/datasets/client/fields/pays/type"]


def test_set_pointer_dans_une_liste_par_identifiant() -> None:
    doc = _fields(ID, {"name": "segment", "description": "x"})
    set_pointer(doc, "/datasets/client/fields/segment/description", "y")
    assert doc["datasets"][0]["fields"][1] == {"name": "segment", "description": "y"}
    set_pointer(doc, "/datasets/client/fields/segment", MISSING)
    assert doc["datasets"][0]["fields"] == [ID]
    set_pointer(doc, "/datasets/client/fields/pays", PAYS)
    assert doc["datasets"][0]["fields"] == [ID, PAYS]


def test_meme_changement_des_deux_cotes_sans_conflit() -> None:
    result = merge3({"k": 1}, {"k": 2}, {"k": 2})
    assert result.conflicts == [] and result.merged == {"k": 2}


def test_suppression_d_un_cote_sans_modification_de_l_autre() -> None:
    result = merge3({"a": 1, "b": 2}, {"a": 1}, {"a": 1, "b": 2})
    assert result.merged == {"a": 1}
    result = merge3({"a": 1, "b": 2}, {"a": 1, "b": 2}, {"a": 1})
    assert result.merged == {"a": 1}


def test_ajout_de_cle_d_un_seul_cote_garde() -> None:
    result = merge3({"a": 1}, {"a": 1, "t": 1}, {"a": 1, "m": 2})
    assert result.merged == {"a": 1, "t": 1, "m": 2}


def test_merge_object_json_serialise_comme_l_application() -> None:
    base = json.dumps({"a": 1, "b": 1}).encode()
    theirs = json.dumps({"a": 2, "b": 1}).encode()
    mine = json.dumps({"a": 1, "b": 3}).encode()
    merged = merge_object("sources/s/raw/O.schema.yaml", base, theirs, mine)
    assert merged.kind == "auto"
    assert merged.result == b"a: 2\nb: 3\n"  # schéma : YAML déterministe (dump_schema)


def test_merge_object_yaml_conflit() -> None:
    merged = merge_object("sources/s/manifest.yaml", b"label: A\n", b"label: B\n", b"label: C\n")
    assert merged.kind == "conflict"
    assert [c.pointer for c in merged.conflicts] == ["/label"]


def test_supprime_cote_publie_modifie_brouillon() -> None:
    merged = merge_object("sources/s/manifest.yaml", b"label: A\n", None, b"label: C\n")
    assert merged.kind == "deleted_upstream"
    assert merged.theirs is MISSING and merged.mine == {"label": "C"}


def test_supprime_brouillon_modifie_cote_publie() -> None:
    merged = merge_object("sources/s/manifest.yaml", b"label: A\n", b"label: B\n", None)
    assert merged.kind == "deleted_in_draft"


def test_cree_des_deux_cotes_meme_chemin() -> None:
    merged = merge_object("sources/s/manifest.yaml", None, b"label: B\nx: 1\n", b"label: C\ny: 2\n")
    assert merged.kind == "conflict"
    assert [c.pointer for c in merged.conflicts] == ["/label"]


def test_fichier_inconnu_repli_texte() -> None:
    clean = merge_object("doc.txt", b"a\nb\nc\n", b"A\nb\nc\n", b"a\nb\nC\n")
    assert clean.kind == "text" and clean.result == b"A\nb\nC\n"
    clash = merge_object("doc.txt", b"a\n", b"b\n", b"c\n")
    assert clash.kind == "text_conflict"


def test_set_pointer() -> None:
    doc = {"properties": {"x": {"type": "number"}}}
    assert set_pointer(doc, "/properties/x/type", "integer") == {
        "properties": {"x": {"type": "integer"}}
    }
    assert set_pointer(doc, "/properties/x", MISSING) == {"properties": {}}


def test_set_pointer_garde_le_type_des_cles_yaml() -> None:
    doc = {"responses": {200: {"description": "ok"}}}
    set_pointer(doc, "/responses/200/description", "OK")
    assert doc == {"responses": {200: {"description": "OK"}}}


def test_choix_sur_un_pointeur_d_element_applique() -> None:
    """Critère 6 de f9bd4d93 : `theirs` sur un conflit d'élément est appliqué."""
    from ossie_studio.drafts.preparation import resolved_content

    path = "modeles/client.json"
    theirs_doc = _fields(ID, {"name": "segment", "description": "Niveau"}, PAYS, DATE)
    mine_doc = _fields(ID, {"name": "segment", "description": "Commercial"}, DATE, EMAIL)
    theirs = json.dumps(theirs_doc).encode()
    merged = merge_object(
        path, json.dumps(FIELDS_BASE).encode(), theirs, json.dumps(mine_doc).encode()
    )
    pointer = "/datasets/client/fields/segment/description"
    content = resolved_content(path, merged, {pointer: "theirs"}, None, theirs)
    assert content is not None
    fields = json.loads(content)["datasets"][0]["fields"]
    assert fields[1] == {"name": "segment", "description": "Niveau"}
    assert [f["name"] for f in fields] == [
        "id_client",
        "segment",
        "pays",
        "date_creation",
        "email_contact",
    ]
