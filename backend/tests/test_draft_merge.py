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


def test_liste_modifiee_des_deux_cotes_conflit_atomique() -> None:
    result = merge3({"tags": ["a"]}, {"tags": ["a", "b"]}, {"tags": ["a", "c"]})
    assert [c.pointer for c in result.conflicts] == ["/tags"]


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
    merged = merge_object("sources/s/raw/O.json", base, theirs, mine)
    assert merged.kind == "auto"
    assert merged.result == b'{\n  "a": 2,\n  "b": 3\n}\n'


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
