"""Impacts d'un brouillon : raw → asset, $ref, décisions « vérifié »."""

from __future__ import annotations

import json
import time
from pathlib import Path

from ossie_studio.drafts.impacts import compute_impacts, fingerprint
from ossie_studio.drafts.store import DraftObject


def _put(view: Path, rel: str, value: object) -> None:
    target = view / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(value))


def _obj(path: str, content: object | None, base: str | None = "b") -> DraftObject:
    data = json.dumps(content).encode() if content is not None else None
    return DraftObject(path=path, base_blob=base, content=data)


def test_raw_modifie_impacte_son_asset(tmp_path: Path) -> None:
    _put(tmp_path, "sources/s/raw/User.schema.yaml", {"title": "User", "v": 2})
    _put(tmp_path, "sources/s/assets/User.schema.yaml", {"title": "User"})
    impacts = compute_impacts(tmp_path, [_obj("sources/s/raw/User.schema.yaml", {"v": 2})], {})
    assert [(i.path, i.reads, i.rules) for i in impacts] == [
        ("sources/s/assets/User.schema.yaml", ["sources/s/raw/User.schema.yaml"], ["raw_asset"])
    ]


def test_cible_ref_supprimee_impacte_le_referent(tmp_path: Path) -> None:
    _put(
        tmp_path,
        "sources/s/raw/Order.schema.yaml",
        {"properties": {"u": {"$ref": "User.schema.yaml"}}},
    )
    impacts = compute_impacts(tmp_path, [_obj("sources/s/raw/User.schema.yaml", None)], {})
    assert [(i.path, i.rules) for i in impacts] == [("sources/s/raw/Order.schema.yaml", ["ref"])]


def test_objet_deja_dans_le_brouillon_n_est_pas_un_impact(tmp_path: Path) -> None:
    _put(tmp_path, "sources/s/raw/User.schema.yaml", {"v": 2})
    _put(tmp_path, "sources/s/assets/User.schema.yaml", {"title": "User corrigé"})
    objects = [
        _obj("sources/s/raw/User.schema.yaml", {"v": 2}),
        _obj("sources/s/assets/User.schema.yaml", {}),
    ]
    assert compute_impacts(tmp_path, objects, {}) == []


def test_sans_dependance_aucun_impact(tmp_path: Path) -> None:
    _put(tmp_path, "sources/s/raw/Seul.schema.yaml", {"title": "Seul"})
    assert compute_impacts(tmp_path, [_obj("sources/s/raw/Seul.schema.yaml", {"v": 1})], {}) == []


def test_verifie_puis_redevient_actif_si_l_objet_lu_change(tmp_path: Path) -> None:
    _put(tmp_path, "sources/s/assets/User.schema.yaml", {"title": "User"})
    first = [_obj("sources/s/raw/User.schema.yaml", {"v": 2})]
    decided = {
        "sources/s/assets/User.schema.yaml": fingerprint(["sources/s/raw/User.schema.yaml"], first)
    }
    assert compute_impacts(tmp_path, first, decided)[0].verified is True
    again = [_obj("sources/s/raw/User.schema.yaml", {"v": 3})]
    assert compute_impacts(tmp_path, again, decided)[0].verified is False


def test_collecte_500_objets_temps(tmp_path: Path) -> None:
    objects = []
    for i in range(500):
        _put(
            tmp_path,
            f"sources/s/raw/O{i}.schema.yaml",
            {"properties": {"n": {"$ref": f"O{(i + 1) % 500}.schema.yaml"}}},
        )
        _put(tmp_path, f"sources/s/assets/O{i}.schema.yaml", {"title": f"O{i}"})
        objects.append(_obj(f"sources/s/raw/O{i}.schema.yaml", {"i": i}))
    started = time.perf_counter()
    impacts = compute_impacts(tmp_path, objects, {})
    elapsed = time.perf_counter() - started
    print(f"impacts de 500 objets : {elapsed:.2f}s")
    assert len(impacts) == 500  # chaque asset ; les raw référents sont dans le brouillon
    assert elapsed < 5
