"""La déclaration d'un import (module commun, enabler 4f32c8c6). Un import est
trois fichiers, dans le dossier d'imports que fixe sa cible (pour un référentiel,
`referentiels/<nom>/imports/`) :

    <slug>.yaml                   la déclaration : libellé, format (csv | json),
                                  réglages de lecture (séparateur, encodage,
                                  ligne d'en-tête, pour le CSV ; feuille du
                                  classeur ; correspondances colonne → propriété)
    <slug>.jslt                   le template JSLT
    <slug>.exemple.csv | .json    le fichier d'exemple (octets tels quels)
"""

from __future__ import annotations

import codecs
from dataclasses import dataclass, field
from typing import Any, Literal

import yaml

from ossie_studio.imports.errors import ImportsError
from ossie_studio.problems import Problem

ImportFormat = Literal["csv", "json", "xlsx"]
FORMATS: tuple[ImportFormat, ...] = ("csv", "json", "xlsx")
# Formats lus en lignes et colonnes : la coche « première ligne = en-tête » s'y applique.
TABULAR: tuple[ImportFormat, ...] = ("csv", "xlsx")


@dataclass(frozen=True)
class ImportDeclaration:
    slug: str
    label: str
    format: ImportFormat
    # Réglages de lecture du CSV (sans objet pour le JSON).
    separator: str = ","
    encoding: str = "utf-8"
    header: bool = True
    # Feuille d'un classeur Excel (ticket 6f6066e9) ; vide : la seule feuille, ou à choisir.
    sheet: str = ""
    # Correspondances colonne → propriété choisies à la main (ticket 47716786) ; seuls
    # les choix qui diffèrent de la correspondance par nom sont gardés, "" = colonne
    # ignorée. Lues par « Résoudre le template ».
    mapping: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Import:
    declaration: ImportDeclaration
    template: str
    example: bytes

    @property
    def slug(self) -> str:
        return self.declaration.slug


def example_name(slug: str, fmt: ImportFormat) -> str:
    return f"{slug}.exemple.{fmt}"


def _known_encoding(name: str) -> bool:
    try:
        codecs.lookup(name)
    except LookupError:
        return False
    return True


def check_declaration(decl: ImportDeclaration) -> list[Problem]:
    problems: list[Problem] = []
    if not decl.label.strip():
        problems.append(Problem("label", "import_label_required"))
    if decl.format not in FORMATS:
        problems.append(Problem("format", "import_format_unsupported", str(decl.format)))
    if decl.format == "csv":
        if len(decl.separator) != 1:
            problems.append(Problem("separator", "import_separator_invalid"))
        if not _known_encoding(decl.encoding):
            problems.append(Problem("encoding", "import_encoding_unknown", decl.encoding))
    return problems


def dump_declaration(decl: ImportDeclaration) -> str:
    return yaml.safe_dump(
        declaration_document(decl), sort_keys=False, allow_unicode=True, default_flow_style=False
    )


def declaration_document(decl: ImportDeclaration) -> dict[str, Any]:
    data: dict[str, Any] = {"label": decl.label.strip(), "format": decl.format}
    if decl.format == "csv":
        data.update({"separator": decl.separator, "encoding": decl.encoding, "header": decl.header})
    elif decl.format == "xlsx":
        data["header"] = decl.header
        if decl.sheet:
            data["sheet"] = decl.sheet
    if decl.mapping and decl.format in TABULAR:
        data["mapping"] = dict(decl.mapping)
    return data


def parse_declaration(slug: str, text: str) -> ImportDeclaration:
    """Lit une déclaration ; ImportsError « import_unreadable » si elle est
    illisible ou incomplète."""
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ImportsError("import_unreadable", slug=slug, detail=str(exc)) from None
    if not isinstance(data, dict) or data.get("format") not in FORMATS:
        raise ImportsError("import_unreadable", slug=slug)
    header = data.get("header", True)
    raw_mapping = data.get("mapping")
    mapping = (
        {str(k): "" if v is None else str(v) for k, v in raw_mapping.items()}
        if isinstance(raw_mapping, dict)
        else {}
    )
    return ImportDeclaration(
        slug=slug,
        label=str(data.get("label") or ""),
        format=data["format"],
        separator=str(data.get("separator", ",")),
        encoding=str(data.get("encoding", "utf-8")),
        header=header if isinstance(header, bool) else True,
        sheet=str(data.get("sheet") or ""),
        mapping=mapping,
    )
