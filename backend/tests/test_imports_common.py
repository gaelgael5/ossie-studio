"""Module commun d'import (enabler 4f32c8c6), critère 4 : une cible factice
(schéma, clé, éléments en mémoire) s'importe avec le module commun, essai et
import compris, sans rien des référentiels."""

from __future__ import annotations

from collections.abc import Hashable
from pathlib import Path
from typing import Any

import pytest

from ossie_studio.imports import files
from ossie_studio.imports.declaration import (
    ALL,
    CREATED,
    KEEP,
    ImportDeclaration,
    dump_declaration,
    parse_declaration,
)
from ossie_studio.imports.engine import (
    KEPT_IN_USE,
    KEPT_NOT_CREATED,
    KEPT_STRATEGY,
    created_after,
    deletion_plan,
    imported_items,
    read_example,
    report,
    run_template,
    transform,
)
from ossie_studio.imports.errors import ImportsError
from ossie_studio.imports.provenance import read_created, write_created
from ossie_studio.imports.target import ItemProblem

CSV = b"code;nom;poids\nA;Alpha;1\nB;Beta;-2\nC;Gamma;3\n"
TEMPLATE = '{"code": $.code, "nom": $.nom, "poids": $.poids#integer}'


class FakeTarget:
    """Un élément : un objet avec un `code` et un `poids` positif ; clé = code."""

    def check(self, produced: list[Any]) -> list[ItemProblem]:
        problems = []
        seen: set[str] = set()
        for i, item in enumerate(produced):
            if not isinstance(item.get("poids"), int | float) or item["poids"] <= 0:
                problems.append(
                    ItemProblem(i, "poids_invalide", "positif attendu", "poids", item["code"])
                )
            if item["code"] in seen:
                problems.append(ItemProblem(i, "cle_en_double", "", "", item["code"]))
            seen.add(item["code"])
        return problems

    def key(self, item: Any) -> Hashable | None:
        code = item.get("code") if isinstance(item, dict) else None
        return code if isinstance(code, str) else None

    def label(self, item: Any, index: int) -> str:
        return str(self.key(item) or f"#{index}")


DECL = ImportDeclaration("poids", "Poids", "csv", separator=";")
CURRENT = [{"code": "A", "nom": "Alpha", "poids": 1}, {"code": "Z", "nom": "Zêta", "poids": 9}]


def test_essai_rend_compte_par_la_cible() -> None:
    example = read_example(DECL, CSV)
    result = run_template(FakeTarget(), CURRENT, TEMPLATE, example)
    assert result["ok"] is False and result["lines"] == 3
    assert (result["new"], result["unchanged"], result["errors"]) == (1, 1, 1)
    [error] = result["error_details"]
    assert (error["where"], error["code"], error["field"], error["key"]) == (
        "ligne 3",
        "poids_invalide",
        "poids",
        "B",
    )


GOOD = CSV.replace(b"B;Beta;-2", b"B;Beta;2")


def test_fusion_et_strategies_de_suppression() -> None:
    """Ticket 6d36073b : fusion seulement ; l'absent (Z) suit la stratégie."""
    target = FakeTarget()
    refused = transform(target, TEMPLATE, read_example(DECL, CSV))
    assert imported_items(target, CURRENT, refused, deletion_plan(target, [], refused)) is None
    done = transform(target, TEMPLATE, read_example(DECL, GOOD))
    keep = deletion_plan(target, CURRENT, done, KEEP)
    assert [(a.key, a.reason) for a in keep.kept] == [("Z", KEPT_STRATEGY)] and not keep.deleted
    merged = imported_items(target, CURRENT, done, keep)
    assert [i["code"] for i in merged or []] == ["A", "Z", "B", "C"]
    everything = deletion_plan(target, CURRENT, done, ALL)
    assert [a.key for a in everything.deleted] == ["Z"]
    assert [i["code"] for i in imported_items(target, CURRENT, done, everything) or []] == [
        "A",
        "B",
        "C",
    ]
    summary = report(target, CURRENT, done, everything)
    assert (summary["new"], summary["unchanged"], summary["deleted"]) == (2, 1, 1)
    # « Cet import a créé » : Z part s'il vient de cet import, reste sinon.
    mine = deletion_plan(target, CURRENT, done, CREATED, frozenset({"Z"}))
    assert [a.key for a in mine.deleted] == ["Z"]
    other = deletion_plan(target, CURRENT, done, CREATED, frozenset())
    assert [(a.key, a.reason) for a in other.kept] == [("Z", KEPT_NOT_CREATED)]
    kept = report(target, CURRENT, done, other)
    assert (kept["kept"], kept["deleted"]) == (1, 0)
    last = kept["differences"][-1]
    assert (last["key"], last["kind"], last["reason"]) == ("Z", "kept", KEPT_NOT_CREATED)


class UsingTarget(FakeTarget):
    """Un élément peut en utiliser d'autres (`utilise`, liste de codes), comme une
    relation vise ses extrémités."""

    def in_use(self, produced: list[Any], deleting: set[Hashable]) -> dict[Hashable, list[Any]]:
        final = [*produced, *(i for i in self.current if i["code"] not in deleting)]
        found: dict[Hashable, list[Any]] = {}
        for key in deleting:
            users = [i["code"] for i in final if key in i.get("utilise", [])]
            if users:
                found[key] = [{"key": u} for u in users]
        return found

    def __init__(self, current: list[Any]) -> None:
        self.current = current


def test_element_utilise_garde_et_etat_final() -> None:
    """Un absent encore utilisé par un élément qui reste est gardé ; utilisé par un
    élément supprimé dans le même import, il part ; un gardé retient ce qu'il utilise."""
    current = [
        {"code": "A", "nom": "Alpha", "poids": 1},
        {"code": "U", "nom": "Utilisé", "poids": 1},
        {"code": "V", "nom": "Utilisé par un absent", "poids": 1},
        {"code": "W", "nom": "Absent qui utilise V", "poids": 1, "utilise": ["V"]},
        {"code": "M", "nom": "Main", "poids": 1, "utilise": ["U"]},
    ]
    target = UsingTarget(current)
    file = b"code;nom;poids\nA;Alpha;1\n"
    done = transform(target, TEMPLATE, read_example(DECL, file))
    plan = deletion_plan(target, current, done, ALL)
    # M (absent) part ; donc U, qu'il utilisait, part aussi : l'état final compte.
    assert sorted(a.key for a in plan.deleted) == ["M", "U", "V", "W"]
    # M créé à la main, gardé (« cet import a créé ») : il retient U.
    created = frozenset({"U", "V", "W"})
    plan = deletion_plan(target, current, done, CREATED, created)
    assert sorted(a.key for a in plan.deleted) == ["V", "W"]
    assert {(a.key, a.reason) for a in plan.kept} == {
        ("M", KEPT_NOT_CREATED),
        ("U", KEPT_IN_USE),
    }
    [used] = plan.in_use
    assert used.usages == ({"key": "M"},)
    summary = report(target, current, done, plan)
    assert (summary["deleted"], summary["kept"], summary["kept_in_use"]) == (2, 2, 1)
    items = imported_items(target, current, done, plan) or []
    assert sorted(i["code"] for i in items) == ["A", "M", "U"]
    # Provenance : ce qui existe encore de ce qu'il avait créé, plus ses créations.
    assert created_after(target, current, items, created) == {"U"}
    assert created_after(target, [], items, frozenset()) == {"A", "M", "U"}


def test_provenance_et_ancien_remplacement(tmp_path: Path) -> None:
    write_created(tmp_path, "x/imports", "poids", {"B", "A"})
    assert (tmp_path / "x/imports/poids.provenance.yaml").read_text() == "created:\n- A\n- B\n"
    assert read_created(tmp_path, "x/imports", "poids") == {"A", "B"}
    write_created(tmp_path, "r/imports", "pays", {("FR", "75")})
    assert read_created(tmp_path, "r/imports", "pays") == {("FR", "75")}
    write_created(tmp_path, "x/imports", "poids", set())
    assert not (tmp_path / "x/imports/poids.provenance.yaml").exists()
    # Un import enregistré en remplacement garde son comportement : tous les absents.
    assert parse_declaration("p", "label: P\nformat: csv\nmode: replace\n").deletion == ALL
    assert parse_declaration("p", "label: P\nformat: csv\n").deletion == KEEP
    created = parse_declaration(
        "p", dump_declaration(ImportDeclaration("p", "P", "csv", deletion=CREATED))
    )
    assert created.deletion == CREATED


def test_fichiers_d_un_import_dans_un_dossier_quelconque(tmp_path: Path) -> None:
    folder = "domaine/x/imports"
    written = files.write_import(tmp_path, folder, DECL, TEMPLATE, CSV, create=True)
    assert files.read_import(tmp_path, folder, "poids") == written
    assert files.import_paths(folder, DECL) == [
        "domaine/x/imports/poids.yaml",
        "domaine/x/imports/poids.jslt",
        "domaine/x/imports/poids.exemple.csv",
    ]
    with pytest.raises(ImportsError) as taken:
        files.write_import(tmp_path, folder, DECL, TEMPLATE, CSV, create=True)
    assert taken.value.code == "import_slug_taken"
    files.delete_import(tmp_path, folder, "poids")
    assert files.read_imports(tmp_path, folder) == []


# --- Excel (ticket 6f6066e9) et colonnes (ticket 221d832b) -------------------------------


def _xlsx(sheets: dict[str, list[list[Any]]]) -> bytes:
    import io

    import openpyxl

    book = openpyxl.Workbook()
    book.remove(book.active)
    for name, rows in sheets.items():
        sheet = book.create_sheet(name)
        for row in rows:
            sheet.append(row)
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()


def test_xlsx_une_feuille_avec_en_tete() -> None:
    from datetime import date

    raw = _xlsx(
        {"Feuil1": [["cle", "poids", "le"], ["A", 1, date(2026, 11, 27)], ["B", 2.5, None]]}
    )
    decl = ImportDeclaration("x", "X", "xlsx")
    example = read_example(decl, raw)
    assert example.columns == ["cle", "poids", "le"]
    assert [r.data for r in example.records] == [
        {"cle": "A", "poids": 1, "le": "2026-11-27T00:00:00"},
        {"cle": "B", "poids": 2.5, "le": ""},
    ]
    assert example.records[0].where == "ligne 2"
    no_header = read_example(ImportDeclaration("x", "X", "xlsx", header=False), raw)
    assert no_header.columns == ["col1", "col2", "col3"] and len(no_header.records) == 3


def test_xlsx_plusieurs_feuilles_demande_laquelle() -> None:
    from ossie_studio.imports.engine import ExampleError, sheet_names

    raw = _xlsx({"Pays": [["code"], ["FR"]], "Modes": [["mode"], ["site"], ["relais"]]})
    assert sheet_names(raw) == ["Pays", "Modes"]
    with pytest.raises(ExampleError) as missing:
        read_example(ImportDeclaration("x", "X", "xlsx"), raw)
    assert missing.value.code == "example_sheet_required" and "Modes" in missing.value.detail
    modes = read_example(ImportDeclaration("x", "X", "xlsx", sheet="Modes"), raw)
    assert [r.data["mode"] for r in modes.records] == ["site", "relais"]
    with pytest.raises(ExampleError) as bad:
        read_example(ImportDeclaration("x", "X", "xlsx"), b"pas un classeur")
    assert bad.value.code == "example_xlsx"


def test_colonnes_comparees_aux_proprietes() -> None:
    from ossie_studio.imports.columns import compare, infer_type, property_name

    assert property_name("Délai (jours)") == "delai_jours"
    assert property_name("2e adresse") == "c_2e_adresse"
    rows = compare(["Pays", "Mode", "Délai (jours)"], ["pays", "mode"])
    assert [(r["column"], r["property"], r["proposed"]) for r in rows] == [
        ("Pays", "pays", "pays"),
        ("Mode", "mode", "mode"),
        ("Délai (jours)", None, "delai_jours"),
    ]
    assert infer_type(["3", "12", ""]) == "integer"
    assert infer_type([1, 2.5]) == "number"
    assert infer_type(["true", "false"]) == "boolean"
    assert infer_type(["FR", "3"]) == "string"


def test_resoudre_le_template() -> None:
    """Ticket 0598c41b : toutes les propriétés, la colonne qui correspond, sinon null ;
    la conversion quand le type diffère ; le template s'analyse."""
    from ossie_studio.imports.columns import resolve_template
    from ossie_studio.imports.engine import check_template

    flat = resolve_template(
        [("pays", "string"), ("mode", "string"), ("delai", "integer")],
        None,
        {"Pays": ["FR"], "Mode": ["site"], "Delai": ["2"]},
    )
    assert flat == '{\n  "pays": $.Pays,\n  "mode": $.Mode,\n  "delai": $.Delai#integer\n}\n'
    assert check_template(flat) == []
    # xlsx : les nombres arrivent comme nombres, aucune conversion ; un texte
    # attendu pour un nombre lu est converti.
    nested = resolve_template(
        [("cle", "string"), ("nom", "string"), ("documentation", "string")],
        ("proprietes", [("criticite", "string"), ("poids", "number"), ("code", "string")]),
        {"cle": ["A"], "Nom": ["x"], "Poids": [3], "Code": [12]},
    )
    assert '"documentation": null' in nested and '"criticite": null' in nested
    assert '"poids": $.Poids,' in nested and '"code": $.Code#string' in nested
    assert check_template(nested) == []
    empty = resolve_template([("a", "string")], None, {"Autre": ["x"]})
    assert '"a": null' in empty


def test_correspondances_choisies_a_la_main() -> None:
    """Ticket 47716786 : un choix l'emporte sur le nom, "" ignore la colonne, un choix
    vers une propriété disparue est ignoré ; la résolution suit les choix."""
    from ossie_studio.imports.columns import compare, effective_mapping, resolve_template

    props = ["pays", "mode", "delai"]
    cols = ["Pays", "Mode", "Durée"]
    assert effective_mapping(cols, props, {"Durée": "delai", "Mode": ""}) == {
        "Pays": "pays",
        "Mode": None,
        "Durée": "delai",
    }
    # La propriété prise par un choix n'est plus attribuée par le nom.
    assert effective_mapping(["Pays", "Code"], props, {"Code": "pays"}) == {
        "Pays": None,
        "Code": "pays",
    }
    assert effective_mapping(cols, props, {"Durée": "disparue"})["Durée"] is None
    rows = compare(cols, props, {"Durée": "delai"})
    assert [(r["property"], r["chosen"]) for r in rows] == [
        ("pays", False),
        ("mode", False),
        ("delai", True),
    ]
    resolved = resolve_template(
        [("pays", "string"), ("mode", "string"), ("delai", "integer")],
        None,
        {"Pays": ["FR"], "Mode": ["site"], "Durée": ["2"]},
        {"Durée": "delai", "Mode": ""},
    )
    assert resolved == '{\n  "pays": $.Pays,\n  "mode": null,\n  "delai": $["Durée"]#integer\n}\n'
