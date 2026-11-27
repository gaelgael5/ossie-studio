"""Une propriété renommée dans un type l'est aussi dans ses imports (ticket
859579bb) : correspondances des colonnes et clés du template JSLT. Les objets du
type, eux, sont renommés par `save_type`, qui en vérifie la validité."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from ossie_studio.cartographie.catalog import catalog
from ossie_studio.cartographie.import_target import imports_dir
from ossie_studio.cartographie.model import BASE_PROPERTIES, CartoType, property_renames
from ossie_studio.imports import files
from ossie_studio.imports.columns import follow_renames, rename_template_keys
from ossie_studio.imports.engine import ExampleError, read_example


def _properties(ctype: CartoType) -> list[str]:
    """Ce qu'une colonne peut viser : la base d'un objet, les attributs ArchiMate
    et les propriétés ajoutées (même liste que l'adaptateur d'import)."""
    base = catalog().get(ctype.archimate)
    return [
        *(name for name, _kind, _required in BASE_PROPERTIES),
        *(a.name for a in (base.attributes if base else ())),
        *ctype.properties,
    ]


def follow_type_renames(root: Path, before: CartoType, after: CartoType) -> None:
    renames = property_renames(before, after)
    if not renames:
        return
    folder = imports_dir(after.slug)
    old_props, new_props = _properties(before), _properties(after)
    for imp in files.read_imports(root, folder):
        decl = imp.declaration
        mapping = decl.mapping
        if decl.header:
            try:
                columns = read_example(decl, imp.example).columns
            except ExampleError:
                columns = []
            mapping = follow_renames(columns, old_props, new_props, decl.mapping, renames)
        template = rename_template_keys(imp.template, renames)
        if mapping == decl.mapping and template == imp.template:
            continue
        files.write_import(
            root, folder, replace(decl, mapping=mapping), template, imp.example, create=False
        )
