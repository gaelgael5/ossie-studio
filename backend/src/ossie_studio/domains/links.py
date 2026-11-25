"""Liens de référentiel sur les champs des datasets d'un domaine (tickets
docflow 65235f64 et 80bb56af, epic « Référentiels »).

Gael, 24/11/2026 : « il faut que dans les assets & datasets on puisse faire en
sorte qu'une propriété soit connectée sur un champ d'un référentiel. » Même lien
que sur un asset (`{schema, property, group?}`, voir `sources/referentiel_links`),
rangé sur le champ du dataset dans `domaines/<nom>.extend.yaml` : la spécification
Ossie n'a pas cette notion, aucun document Ossie n'en porte trace.

- **dataset dérivé** : le lien est **hérité** de l'asset, en lecture seule ; il le
  suit (`sync_domains_for_asset`, appelé à chaque lien ou déliement dans la
  Curation, dans le même enregistrement du brouillon) ;
- l'état d'un lien (complet, partiel « clé 1/2 », cassé) se calcule comme pour un
  asset, sur les champs du dataset : un groupe de clé composée dont le dataset ne
  reprend qu'une propriété est partiel, la propriété absente nommée.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ossie_studio.domains.documents import (
    AssetRef,
    Dataset,
    Domain,
    DomainError,
    list_domains,
    write_domain,
)
from ossie_studio.sources.referentiel_links import LINK_X, LinkTarget, LinkView, link_of, link_views


def _pseudo_asset(ds: Dataset) -> dict[str, Any]:
    """Les champs du dataset sous la forme d'un asset, pour réutiliser les règles
    des liens d'asset sans les réécrire."""
    return {
        "properties": {
            f.name: ({LINK_X: dict(f.referentiel)} if f.referentiel else {}) for f in ds.fields
        }
    }


def field_link_views(ds: Dataset, targets: Mapping[str, LinkTarget]) -> dict[str, LinkView]:
    """État du lien de chaque champ lié (nom du champ → vue)."""
    return link_views(_pseudo_asset(ds), targets)


def sync_asset_links(domain: Domain, key: AssetRef, asset: dict[str, Any]) -> bool:
    """Recopie les liens de l'asset `key` sur les champs des datasets qui en
    dérivent ; rend vrai si quelque chose a changé."""
    props = asset.get("properties")
    props = props if isinstance(props, dict) else {}
    changed = False
    for ds in domain.datasets:
        if not ds.derived or ds.asset != key:
            continue
        for f in ds.fields:
            link = link_of(props.get(f.property or f.name))
            if link != f.referentiel:
                f.referentiel = link
                changed = True
    return changed


def sync_domains_for_asset(root: Path, source: str, path: str, asset: dict[str, Any]) -> list[str]:
    """Met à jour les domaines de la vue qui contiennent un dataset dérivé de
    l'asset ; rend leurs noms. Un domaine illisible est laissé tel quel : la
    Curation n'a pas à échouer pour lui, il sera signalé à son ouverture."""
    key = AssetRef(source, path)
    touched: list[str] = []
    # `list_domains` rend vide (sans dataset) un domaine illisible : rien à y
    # recopier, il n'est donc jamais réécrit.
    for domain in list_domains(root):
        if not sync_asset_links(domain, key, asset):
            continue
        try:
            write_domain(root, domain)
        except DomainError:
            continue
        touched.append(domain.name)
    return touched
