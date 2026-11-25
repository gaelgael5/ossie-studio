"""Tenir à jour les fichiers `.view` des modules à la collecte (epic « Cartographie
applicative dans raw », enabler 6c97ad98).

Étape du **socle**, après les parseurs et la résolution des références : elle voit
l'ensemble final des objets module de `raw/` et met chaque `<clé>.view.yaml` à
jour **par ajouts et suppressions** (`parsers.application.sync_view`) :

- un module sans `.view` en reçoit un (un lien par dépendance, ancres `auto`) ;
- un lien disparu perd son entrée, un lien nouveau en gagne une, les autres
  entrées (les ancres déplacées à l'écran) sont gardées telles quelles ;
- le `.view` d'un module disparu est retiré avec lui ;
- un `.view` illisible n'est jamais écrasé (il reste à corriger à la main) ;
- rien n'est réécrit quand rien ne change.
"""

from __future__ import annotations

from collections.abc import Mapping

from ossie_studio.parsers.application import (
    is_module,
    is_view_name,
    key_of,
    module_links,
    sync_view,
    view_file,
)
from ossie_studio.parsers.base import OutputAction
from ossie_studio.parsers.conventions import SchemaReadError, is_schema_name, load_schema


def sync_module_views(
    existing: Mapping[str, str], writes: list[OutputAction], deletes: list[str]
) -> tuple[list[OutputAction], list[str]]:
    """`existing` : `raw/` avant la collecte ; `writes`/`deletes` : actions de la
    collecte. Rend les actions complétées des écritures et suppressions de `.view`."""
    removed = set(deletes)
    final = {path: text for path, text in existing.items() if path not in removed}
    final.update({action.path: action.content for action in writes})

    modules: dict[str, list[str]] = {}
    for path, text in final.items():
        if not is_schema_name(path):
            continue
        try:
            data = load_schema(text)
        except SchemaReadError:
            continue
        if is_module(data):
            modules[key_of(path)] = module_links(data)

    new_writes = list(writes)
    new_deletes = list(deletes)
    for key, links in sorted(modules.items()):
        # Un lien ne vaut que vers un module présent dans la source.
        present = [target for target in links if key_of(target) in modules]
        path = view_file(key)
        updated = sync_view(final.get(path), present)
        if updated is not None:
            new_writes.append(OutputAction(path=path, content=updated))
    for path in sorted(final):
        if is_view_name(path) and key_of(path) not in modules:
            new_deletes.append(path)
    return new_writes, new_deletes
