"""Module commun d'import (enabler 4f32c8c6), critère 4 : une cible factice
(schéma, clé, éléments en mémoire) s'importe avec le module commun, essai et
import compris, sans rien des référentiels."""

from __future__ import annotations

from collections.abc import Hashable
from pathlib import Path
from typing import Any

import pytest

from ossie_studio.imports import files
from ossie_studio.imports.declaration import ImportDeclaration
from ossie_studio.imports.engine import (
    MERGE,
    REPLACE,
    imported_items,
    read_example,
    report,
    run_template,
    transform,
)
from ossie_studio.imports.errors import ImportsError
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


def test_import_fusion_et_remplacement_tout_ou_rien() -> None:
    target = FakeTarget()
    refused = transform(target, TEMPLATE, read_example(DECL, CSV))
    assert imported_items(target, CURRENT, refused, MERGE) is None  # tout ou rien
    good = read_example(DECL, CSV.replace(b"B;Beta;-2", b"B;Beta;2"))
    done = transform(target, TEMPLATE, good)
    merged = imported_items(target, CURRENT, done, MERGE)
    assert [i["code"] for i in merged or []] == ["A", "Z", "B", "C"]
    replaced = imported_items(target, CURRENT, done, REPLACE)
    assert [i["code"] for i in replaced or []] == ["A", "B", "C"]
    summary = report(target, CURRENT, done, REPLACE)
    assert (summary["new"], summary["unchanged"], summary["deleted"]) == (2, 1, 1)


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
