"""Aides communes aux extracteurs de catalogue (regroupement des clefs). La
cartographie des types et l'assemblage des objets restent propres à chaque moteur
(leurs catalogues diffèrent) ; seul le regroupement des lignes de clefs est
partagé."""

from __future__ import annotations

from typing import Any

from ossie_studio.databases.base import CatalogForeignKey


def grouped_keys(
    rows: list[dict[str, Any]], name_key: str
) -> dict[tuple[str, str], list[list[str]]]:
    """Regroupe des lignes de clefs (primaire / unicité) par (schéma, table) puis
    par contrainte, en respectant l'ordre des colonnes (`seq`)."""
    by_constraint: dict[tuple[str, str, str], list[tuple[int, str]]] = {}
    for r in rows:
        key = (str(r["schema"]), str(r["table"]), str(r.get(name_key) or ""))
        by_constraint.setdefault(key, []).append((int(r.get("seq", 0)), str(r["column"])))
    out: dict[tuple[str, str], list[list[str]]] = {}
    for (schema, table, _name), cols in by_constraint.items():
        out.setdefault((schema, table), []).append([c for _, c in sorted(cols)])
    return out


def fk_groups(
    rows: list[dict[str, Any]], *, enforced: bool
) -> dict[tuple[str, str], list[CatalogForeignKey]]:
    """Regroupe des lignes de clefs étrangères par (schéma, table) puis par
    contrainte, en respectant l'ordre des colonnes."""
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for r in rows:
        key = (str(r["schema"]), str(r["table"]), str(r.get("fk_name") or ""))
        grouped.setdefault(key, []).append(r)
    out: dict[tuple[str, str], list[CatalogForeignKey]] = {}
    for (schema, table, _name), items in grouped.items():
        items.sort(key=lambda r: int(r.get("seq", 0)))
        out.setdefault((schema, table), []).append(
            CatalogForeignKey(
                columns=tuple(str(r["column"]) for r in items),
                ref_schema=str(items[0].get("ref_schema", "")),
                ref_object=str(items[0].get("ref_table", "")),
                ref_columns=tuple(str(r.get("ref_column", "")) for r in items),
                enforced=enforced,
            )
        )
    return out
