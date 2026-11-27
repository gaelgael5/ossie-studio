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
