"""Routes d'import communes à toutes les cibles (enabler 4f32c8c6).

Reprises telles quelles de l'onglet « Imports » des référentiels (tickets
cda9c43a et a909dc66) : déclarer un import, déposer un fichier d'exemple, régler
sa lecture, écrire et essayer le template JSLT, puis importer. Ce qui dépend de
la cible passe par un **adaptateur** (`ImportAdapter`) : où vivent ses imports,
ses éléments actuels, sa validation (`ImportTarget`), l'écriture, le type d'objet
de son journal et les droits.

**Fusion seulement** (ticket 6d36073b) : un import corrige et ajoute ; la
suppression des éléments absents du fichier suit la **stratégie de suppression**
enregistrée avec l'import (`PUT …/suppression`). Les éléments gardés car encore
utilisés d'une exécution s'expliquent sur sa page (`GET …/executions/{id}`).

Routes de l'écran, pour une personne connectée (session) : une clé d'API
n'importe rien. Les fichiers sont écrits dans le brouillon de la personne
(jamais dans la version publiée : la publication reste un geste humain).

Ce module n'utilise pas `from __future__ import annotations` : les routes sont
définies dans `import_router`, et FastAPI doit résoudre leurs annotations (les
dépendances de droits de l'adaptateur) au moment de la définition.
"""

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Annotated, Any, Literal, Protocol

from fastapi import APIRouter, Depends, Form, Request, UploadFile
from pydantic import BaseModel, ConfigDict, Field

from ossie_studio.api.governance_common import CryptoDep, DraftWorkdirDep
from ossie_studio.deps import ConnDep
from ossie_studio.doc_index.queue import IMPORT, request_pass
from ossie_studio.doc_index.store import ViewKey
from ossie_studio.errors import ApiError
from ossie_studio.imports import files
from ossie_studio.imports.columns import (
    compare,
    effective_mapping,
    infer_type,
    minimal_mapping,
    property_name,
    resolve_template,
)
from ossie_studio.imports.declaration import (
    TABULAR,
    Import,
    ImportDeclaration,
    example_name,
)
from ossie_studio.imports.engine import (
    DELETION_LABELS,
    Absent,
    Example,
    ExampleError,
    check_template,
    created_after,
    deletion_plan,
    detect_reading,
    imported_items,
    preview,
    read_example,
    report,
    run_template,
    sheet_names,
    transform,
)
from ossie_studio.imports.journal import (
    IMPORTED,
    REFUSED,
    UNCHANGED,
    LastImport,
    last_imports,
    read_run,
    record_import,
)
from ossie_studio.imports.provenance import read_created, write_created
from ossie_studio.imports.target import ImportTarget

MAX_EXAMPLE_BYTES = 5 * 1024 * 1024
MAX_IMPORT_BYTES = 10 * 1024 * 1024
MAX_TEMPLATE_LENGTH = 256 * 1024
STARTER_TEMPLATE = "{\n}\n"


class CreateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str = ""
    slug: str = ""
    format: Literal["csv", "json", "xlsx"] = "csv"


class RenameBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str = ""


class ReadingBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    separator: str = ","
    encoding: str = "utf-8"
    header: bool = True
    # Feuille d'un classeur xlsx (ticket 6f6066e9).
    sheet: str = ""


class ColumnBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    column: str = Field(min_length=1, max_length=200)


class MappingBody(BaseModel):
    # La propriété voulue pour chaque colonne (ticket 47716786) ; "" : colonne
    # ignorée. Envoyées ensemble, depuis le bouton « Enregistrer les correspondances ».
    model_config = ConfigDict(extra="forbid")
    mapping: dict[str, str] = Field(max_length=500)


Deletion = Literal["keep", "created", "all"]


class TemplateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    template: str = Field(default="", max_length=MAX_TEMPLATE_LENGTH)


class TrialBody(TemplateBody):
    # La stratégie de suppression à essayer ; absente : celle enregistrée avec l'import.
    deletion: Deletion | None = None


class DeletionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    deletion: Deletion


Field_ = tuple[str, str | None]
Skeleton = tuple[list[Field_], tuple[str, list[Field_]] | None]


class Handle(Protocol):
    """Une branche ouverte pour une personne : lire, écrire dans son brouillon
    (les refus de la cible y sont déjà traduits en ApiError)."""

    async def read(self, fn: Callable[[Path], Any]) -> Any: ...

    async def write(self, intent: str, fn: Callable[[Path], Any]) -> tuple[Any, str | None]: ...


@dataclass(frozen=True)
class ImportAdapter:
    """Ce qu'une cible apporte aux routes communes. `owner` : ce qui porte les
    imports dans la cible (le nom d'un référentiel, le slug d'un type)."""

    journal_type: str
    read_dependency: Callable[..., Awaitable[Any]]
    write_dependency: Callable[..., Awaitable[Any]]
    open: Callable[..., Awaitable[Handle]]
    folder: Callable[[str], str]
    # Vérifie que le propriétaire existe (refus nommé sinon).
    require: Callable[[Path, str], None]
    # Ce que l'import compare et fusionne : (cible, éléments actuels).
    current: Callable[[Path, str], tuple[ImportTarget, list[Any]]]
    # Écrit les éléments après import (fusion, ou remplacement si permis).
    write_items: Callable[[Path, str, ImportTarget, list[Any], list[Any]], None]
    # Ce que disent les messages du brouillon : « de delais », « du type application »…
    of: Callable[[str], str]
    # … et pour un import : « dans delais », « dans le type application ».
    into: Callable[[str], str]
    # Les propriétés du modèle auxquelles comparer les colonnes (ticket 221d832b).
    properties: Callable[[Path, str], list[str]]
    # Ajoute une propriété au modèle (nom normalisé, type JSON Schema).
    add_property: Callable[[Path, str, str, str], None]
    # La forme d'un élément pour « Résoudre le template » (ticket 0598c41b) : les
    # champs de premier niveau (nom, type JSON Schema) et un groupe imbriqué éventuel.
    skeleton: Callable[[Path, str], Skeleton]


def _kept_payload(absent: Absent) -> dict[str, Any]:
    """Un élément gardé car utilisé, tel que sa page l'explique : sa clé, sa fiche
    (si la cible en a une) et ses utilisations au moment de l'import."""
    return {"key": absent.label, "ref": absent.ref, "usages": list(absent.usages)}


def _summary(imp: Import, last: LastImport | None) -> dict[str, Any]:
    d = imp.declaration
    return {
        "slug": d.slug,
        "label": d.label,
        "format": d.format,
        "separator": d.separator,
        "encoding": d.encoding,
        "header": d.header,
        "sheet": d.sheet,
        # La stratégie de suppression des éléments absents du fichier (ticket 6d36073b).
        "deletion": d.deletion,
        # Le dernier import abouti, lu dans le journal (None : jamais importé).
        "last_import": last.at if last else None,
        "last_import_by": last.by if last else None,
    }


def _detected(decl: ImportDeclaration, raw: bytes) -> ImportDeclaration:
    """Les réglages de lecture devinés sur le fichier déposé (ticket 123631e5) ;
    la personne les corrige ensuite dans « Fichier d'exemple » si besoin. Un
    classeur d'une seule feuille la prend d'office ; sinon la feuille déclarée
    reste si le nouveau fichier l'a, à choisir sinon (ticket 6f6066e9)."""
    if decl.format == "xlsx" and raw:
        try:
            sheets = sheet_names(raw)
        except ExampleError:
            return decl
        if len(sheets) == 1:
            return replace(decl, sheet=sheets[0])
        return decl if decl.sheet in sheets else replace(decl, sheet="")
    if decl.format != "csv" or not raw:
        return decl
    encoding, separator = detect_reading(raw)
    return replace(decl, encoding=encoding, separator=separator or decl.separator)


def _example_of(imp: Import, reading: ReadingBody | None, raw: bytes | None = None) -> Example:
    """Les lignes du fichier d'exemple (ou de `raw`, un autre fichier déposé),
    lues avec les réglages de l'import, ou ceux donnés."""
    decl = imp.declaration
    if reading is not None and decl.format == "csv":
        decl = replace(
            decl, separator=reading.separator, encoding=reading.encoding, header=reading.header
        )
    elif reading is not None and decl.format == "xlsx":
        # Feuille non donnée : celle de la déclaration (jamais « à choisir » par oubli).
        decl = replace(decl, header=reading.header, sheet=reading.sheet or decl.sheet)
    try:
        return read_example(decl, imp.example if raw is None else raw)
    except ExampleError as exc:
        raise ApiError(exc.code, status_code=400, detail=exc.detail) from None


def import_router(prefix: str, adapter: ImportAdapter) -> APIRouter:
    """Les routes d'import d'une cible, sous `prefix` (qui porte `{owner}` : le
    nom du paramètre de chemin est `name` pour les référentiels, `slug` pour la
    cartographie ; on le lit dans la requête)."""
    router = APIRouter(prefix=prefix)
    Reader = Annotated[Any, Depends(adapter.read_dependency)]
    Writer = Annotated[Any, Depends(adapter.write_dependency)]

    def owner_of(request: Request) -> str:
        params = request.path_params
        return str(params.get("name") or params.get("type_slug") or "")

    def folder(owner: str) -> str:
        return adapter.folder(owner)

    def detail(owner: str, imp: Import, last: LastImport | None = None) -> dict[str, Any]:
        return {
            **_summary(imp, last),
            "template": imp.template,
            "example_name": example_name(imp.slug, imp.declaration.format),
            "example_size": len(imp.example),
            "paths": files.import_paths(folder(owner), imp.declaration),
        }

    def read_one(owner: str, slug: str) -> Callable[[Path], Import]:
        def read(root: Path) -> Import:
            adapter.require(root, owner)
            return files.read_import(root, folder(owner), slug)

        return read

    async def last_of(conn: Any, branch_id: uuid.UUID, owner: str) -> dict[str, LastImport]:
        return await last_imports(conn, adapter.journal_type, branch_id, owner)

    @router.get("")
    async def list_imports(
        request: Request,
        conn: ConnDep,
        crypto: CryptoDep,
        user: Reader,
        workdir: DraftWorkdirDep,
        space_id: uuid.UUID,
        branch_id: uuid.UUID,
    ) -> dict[str, object]:
        owner = owner_of(request)
        handle = await adapter.open(conn, crypto, workdir, space_id, branch_id, user)

        def read(root: Path) -> list[Import]:
            adapter.require(root, owner)
            return files.read_imports(root, folder(owner))

        found: list[Import] = await handle.read(read)
        last = await last_of(conn, branch_id, owner)
        return {"imports": [_summary(i, last.get(i.slug)) for i in found]}

    @router.post("", status_code=201)
    async def create_import(
        request: Request,
        conn: ConnDep,
        crypto: CryptoDep,
        user: Writer,
        workdir: DraftWorkdirDep,
        space_id: uuid.UUID,
        branch_id: uuid.UUID,
        body: CreateBody,
    ) -> dict[str, object]:
        owner = owner_of(request)
        decl = ImportDeclaration(body.slug.strip(), body.label.strip(), body.format)
        handle = await adapter.open(conn, crypto, workdir, space_id, branch_id, user)

        def write(root: Path) -> Import:
            adapter.require(root, owner)
            return files.write_import(root, folder(owner), decl, STARTER_TEMPLATE, b"", create=True)

        imp, _rev = await handle.write(f"import {decl.slug} {adapter.of(owner)}", write)
        return detail(owner, imp)

    @router.get("/{slug}")
    async def get_import(
        request: Request,
        conn: ConnDep,
        crypto: CryptoDep,
        user: Reader,
        workdir: DraftWorkdirDep,
        space_id: uuid.UUID,
        branch_id: uuid.UUID,
        slug: str,
    ) -> dict[str, object]:
        owner = owner_of(request)
        handle = await adapter.open(conn, crypto, workdir, space_id, branch_id, user)
        imp: Import = await handle.read(read_one(owner, slug))
        last = await last_of(conn, branch_id, owner)
        return detail(owner, imp, last.get(slug))

    async def update(
        request: Request,
        conn: Any,
        crypto: Any,
        workdir: Any,
        space_id: uuid.UUID,
        branch_id: uuid.UUID,
        user: Any,
        slug: str,
        intent: str,
        change: Callable[[Import], tuple[ImportDeclaration, str, bytes]],
    ) -> dict[str, object]:
        """Relit l'import dans le brouillon, applique `change` (déclaration,
        template, exemple), et l'écrit."""
        owner = owner_of(request)
        handle = await adapter.open(conn, crypto, workdir, space_id, branch_id, user)

        def write(root: Path) -> Import:
            decl, template, example = change(read_one(owner, slug)(root))
            return files.write_import(root, folder(owner), decl, template, example, create=False)

        imp, _rev = await handle.write(f"{intent} {slug} {adapter.of(owner)}", write)
        return detail(owner, imp)

    @router.patch("/{slug}")
    async def rename_import(
        request: Request,
        conn: ConnDep,
        crypto: CryptoDep,
        user: Writer,
        workdir: DraftWorkdirDep,
        space_id: uuid.UUID,
        branch_id: uuid.UUID,
        slug: str,
        body: RenameBody,
    ) -> dict[str, object]:
        """Renommer : le libellé seulement, le slug ne change pas."""
        label = body.label.strip()
        return await update(
            request, conn, crypto, workdir, space_id, branch_id, user, slug,
            "renomme l'import",
            lambda imp: (replace(imp.declaration, label=label), imp.template, imp.example),
        )  # fmt: skip

    @router.delete("/{slug}", status_code=204)
    async def remove_import(
        request: Request,
        conn: ConnDep,
        crypto: CryptoDep,
        user: Writer,
        workdir: DraftWorkdirDep,
        space_id: uuid.UUID,
        branch_id: uuid.UUID,
        slug: str,
    ) -> None:
        owner = owner_of(request)
        handle = await adapter.open(conn, crypto, workdir, space_id, branch_id, user)

        def write(root: Path) -> None:
            adapter.require(root, owner)
            files.delete_import(root, folder(owner), slug)

        await handle.write(f"supprime l'import {slug} {adapter.of(owner)}", write)

    @router.put("/{slug}/exemple")
    async def upload_example(
        request: Request,
        conn: ConnDep,
        crypto: CryptoDep,
        user: Writer,
        workdir: DraftWorkdirDep,
        space_id: uuid.UUID,
        branch_id: uuid.UUID,
        slug: str,
        file: UploadFile,
    ) -> dict[str, object]:
        """Déposer ou remplacer le fichier d'exemple (5 Mo au plus)."""
        raw = await file.read(MAX_EXAMPLE_BYTES + 1)
        if len(raw) > MAX_EXAMPLE_BYTES:
            raise ApiError("import_example_too_large", status_code=413, limit=MAX_EXAMPLE_BYTES)
        return await update(
            request, conn, crypto, workdir, space_id, branch_id, user, slug,
            "exemple de l'import",
            lambda imp: (_detected(imp.declaration, raw), imp.template, raw),
        )  # fmt: skip

    @router.delete("/{slug}/exemple")
    async def clear_example(
        request: Request,
        conn: ConnDep,
        crypto: CryptoDep,
        user: Writer,
        workdir: DraftWorkdirDep,
        space_id: uuid.UUID,
        branch_id: uuid.UUID,
        slug: str,
    ) -> dict[str, object]:
        """Retirer le fichier d'exemple (ticket 34fa9248, bouton « Nettoyer » de
        l'aperçu) : il redevient vide, comme à la création de l'import ; la
        déclaration, le template et les correspondances restent."""
        return await update(
            request, conn, crypto, workdir, space_id, branch_id, user, slug,
            "retire l'exemple de l'import",
            lambda imp: (imp.declaration, imp.template, b""),
        )  # fmt: skip

    @router.put("/{slug}/lecture")
    async def set_reading(
        request: Request,
        conn: ConnDep,
        crypto: CryptoDep,
        user: Writer,
        workdir: DraftWorkdirDep,
        space_id: uuid.UUID,
        branch_id: uuid.UUID,
        slug: str,
        body: ReadingBody,
    ) -> dict[str, object]:
        """Réglages de lecture du CSV : séparateur, encodage, ligne d'en-tête."""
        return await update(
            request, conn, crypto, workdir, space_id, branch_id, user, slug,
            "lecture de l'import",
            lambda imp: (
                replace(imp.declaration, separator=body.separator, encoding=body.encoding,
                        header=body.header, sheet=body.sheet),
                imp.template,
                imp.example,
            ),
        )  # fmt: skip

    @router.put("/{slug}/template")
    async def save_template(
        request: Request,
        conn: ConnDep,
        crypto: CryptoDep,
        user: Writer,
        workdir: DraftWorkdirDep,
        space_id: uuid.UUID,
        branch_id: uuid.UUID,
        slug: str,
        body: TemplateBody,
    ) -> dict[str, object]:
        """Enregistrer le template (même en cours d'écriture) ; la réponse dit s'il
        s'analyse."""
        found = await update(
            request, conn, crypto, workdir, space_id, branch_id, user, slug,
            "template de l'import",
            lambda imp: (imp.declaration, body.template, imp.example),
        )  # fmt: skip
        return {**found, "template_problems": [p.payload() for p in check_template(body.template)]}

    @router.post("/{slug}/apercu")
    async def preview_example(
        request: Request,
        conn: ConnDep,
        crypto: CryptoDep,
        user: Reader,
        workdir: DraftWorkdirDep,
        space_id: uuid.UUID,
        branch_id: uuid.UUID,
        slug: str,
        body: ReadingBody | None = None,
    ) -> dict[str, object]:
        """Aperçu des premières lignes et chemins détectés, avec les réglages donnés
        (ceux de l'import s'il n'y en a pas)."""
        owner = owner_of(request)
        handle = await adapter.open(conn, crypto, workdir, space_id, branch_id, user)

        def read(root: Path) -> tuple[Import, list[str]]:
            return read_one(owner, slug)(root), adapter.properties(root, owner)

        imp, properties = await handle.read(read)
        decl = imp.declaration
        out: dict[str, object] = {}
        if decl.format == "xlsx" and imp.example:
            # Classeur : ses feuilles, pour choisir (ticket 6f6066e9).
            sheets = sheet_names(imp.example)
            out["sheets"] = sheets
            chosen = (body.sheet if body is not None else "") or decl.sheet
            if chosen not in sheets and len(sheets) != 1:
                return {**out, "sheet_required": True, "columns": [], "rows": [], "count": 0,
                        "paths": [], "comparison": []}  # fmt: skip
        result = preview(_example_of(imp, body))
        header = body.header if body is not None else decl.header
        # Colonnes face aux propriétés du modèle, si le fichier a un en-tête (221d832b).
        comparison = (
            compare(list(result["columns"]), properties, decl.mapping)
            if header and decl.format in TABULAR
            else []
        )
        return {**result, **out, "comparison": comparison, "properties": properties}

    @router.post("/{slug}/resoudre")
    async def resolve(
        request: Request,
        conn: ConnDep,
        crypto: CryptoDep,
        user: Reader,
        workdir: DraftWorkdirDep,
        space_id: uuid.UUID,
        branch_id: uuid.UUID,
        slug: str,
        body: ReadingBody | None = None,
    ) -> dict[str, object]:
        """« Résoudre le template » (ticket 0598c41b) : un template écrit d'après le
        modèle (toutes ses propriétés) et les colonnes du fichier d'exemple lues avec
        les réglages donnés. Rien n'est écrit : l'écran le met dans l'éditeur."""
        owner = owner_of(request)
        handle = await adapter.open(conn, crypto, workdir, space_id, branch_id, user)

        def read(root: Path) -> tuple[Import, Skeleton]:
            return read_one(owner, slug)(root), adapter.skeleton(root, owner)

        imp, (fields, group) = await handle.read(read)
        try:
            example = _example_of(imp, body)
        except ApiError:
            example = Example([])
        columns: dict[str, list[Any]] = {c: [] for c in example.columns}
        for record in example.records[:200]:
            if isinstance(record.data, dict):
                for key, value in record.data.items():
                    columns.setdefault(str(key), []).append(value)
        chosen = imp.declaration.mapping if imp.declaration.header else {}
        return {"template": resolve_template(fields, group, columns, chosen)}

    @router.post("/{slug}/proprietes")
    async def add_model_property(
        request: Request,
        conn: ConnDep,
        crypto: CryptoDep,
        user: Writer,
        workdir: DraftWorkdirDep,
        space_id: uuid.UUID,
        branch_id: uuid.UUID,
        slug: str,
        body: ColumnBody,
    ) -> dict[str, object]:
        """« Ajouter au modèle » (ticket 221d832b) : une propriété au nom normalisé de
        la colonne, typée d'après les valeurs du fichier d'exemple ; dans le brouillon.
        Les règles de la cible s'appliquent (nom réservé, modèle valide)."""
        owner = owner_of(request)
        handle = await adapter.open(conn, crypto, workdir, space_id, branch_id, user)
        name = property_name(body.column)

        def write(root: Path) -> dict[str, object]:
            imp = read_one(owner, slug)(root)
            try:
                example = read_example(imp.declaration, imp.example)
            except ExampleError:
                example = Example([])
            values = [r.data.get(body.column) for r in example.records if isinstance(r.data, dict)]
            kind = infer_type(values)
            adapter.add_property(root, owner, name, kind)
            if body.column in imp.declaration.mapping:
                # La colonne ajoutée va à sa nouvelle propriété, pas à son ancien choix
                # (« Aucune » ou une autre propriété) : ticket 47716786.
                mapping = {c: p for c, p in imp.declaration.mapping.items() if c != body.column}
                decl = replace(imp.declaration, mapping=mapping)
                files.write_import(
                    root, folder(owner), decl, imp.template, imp.example, create=False
                )
            return {"property": name, "type": kind}

        added, revision = await handle.write(
            f"ajoute la propriété {name} {adapter.of(owner)}", write
        )
        return {**added, "revision": revision}

    @router.put("/{slug}/correspondances")
    async def set_mapping(
        request: Request,
        conn: ConnDep,
        crypto: CryptoDep,
        user: Writer,
        workdir: DraftWorkdirDep,
        space_id: uuid.UUID,
        branch_id: uuid.UUID,
        slug: str,
        body: MappingBody,
    ) -> dict[str, object]:
        """Les propriétés des colonnes, choisies à la main (ticket 47716786) ; reprises
        par « Résoudre le template ». Un choix égal à la correspondance par nom n'est
        pas gardé (la déclaration ne porte que les écarts)."""
        owner = owner_of(request)
        handle = await adapter.open(conn, crypto, workdir, space_id, branch_id, user)

        def write(root: Path) -> Import:
            imp = read_one(owner, slug)(root)
            properties = adapter.properties(root, owner)
            wanted = {c: p for c, p in body.mapping.items()}
            unknown = next((p for p in wanted.values() if p and p not in properties), None)
            if unknown is not None:
                raise ApiError("import_mapping_unknown", status_code=400, property=unknown)
            try:
                columns = read_example(imp.declaration, imp.example).columns
            except ExampleError:
                columns = []
            columns = [*columns, *(c for c in wanted if c not in columns)]
            # Les choix gardés sur les autres colonnes, puis ceux envoyés.
            chosen = {c: p for c, p in imp.declaration.mapping.items() if c not in wanted}
            chosen.update(wanted)
            target = effective_mapping(columns, properties, chosen)
            taken = [p for p in target.values() if p]
            duplicate = next((p for p in taken if taken.count(p) > 1), None)
            if duplicate is not None:
                raise ApiError("import_mapping_duplicate", status_code=409, property=duplicate)
            # La déclaration ne garde que les écarts : un choix retiré qui ne change
            # rien au résultat (c'est la correspondance par nom) n'est pas écrit.
            decl = replace(imp.declaration, mapping=minimal_mapping(columns, properties, chosen))
            return files.write_import(
                root, folder(owner), decl, imp.template, imp.example, create=False
            )

        imp, _rev = await handle.write(
            f"correspondances de l'import {slug} {adapter.of(owner)}", write
        )
        return detail(owner, imp)

    @router.put("/{slug}/suppression")
    async def set_deletion(
        request: Request,
        conn: ConnDep,
        crypto: CryptoDep,
        user: Writer,
        workdir: DraftWorkdirDep,
        space_id: uuid.UUID,
        branch_id: uuid.UUID,
        slug: str,
        body: DeletionBody,
    ) -> dict[str, object]:
        """La stratégie de suppression de l'import (ticket 6d36073b) : garder les
        éléments absents du fichier, supprimer ceux que cet import a créés, ou les
        supprimer tous. Enregistrée dans la déclaration de l'import."""

        def change(imp: Import) -> tuple[ImportDeclaration, str, bytes]:
            return replace(imp.declaration, deletion=body.deletion), imp.template, imp.example

        return await update(
            request, conn, crypto, workdir, space_id, branch_id, user, slug,
            "stratégie de suppression de l'import", change,
        )  # fmt: skip

    @router.post("/{slug}/essai")
    async def try_template(
        request: Request,
        conn: ConnDep,
        crypto: CryptoDep,
        user: Reader,
        workdir: DraftWorkdirDep,
        space_id: uuid.UUID,
        branch_id: uuid.UUID,
        slug: str,
        body: TrialBody,
    ) -> dict[str, object]:
        """Essaie le template (le texte donné, enregistré ou non) sur le fichier
        d'exemple : éléments nouveaux, modifiés, inchangés, en erreur, et — selon la
        stratégie de suppression — supprimés et absents gardés (avec la raison).
        Rien n'est écrit."""
        owner = owner_of(request)
        handle = await adapter.open(conn, crypto, workdir, space_id, branch_id, user)

        def read(root: Path) -> tuple[ImportTarget, list[Any], Import, set[Any]]:
            target, current = adapter.current(root, owner)
            created = read_created(root, folder(owner), slug)
            return target, current, read_one(owner, slug)(root), created

        target, current, imp, created = await handle.read(read)
        deletion = body.deletion or imp.declaration.deletion
        example = _example_of(imp, None)
        return run_template(target, current, body.template, example, deletion, created)

    @router.post("/{slug}/import")
    async def run_import(
        request: Request,
        conn: ConnDep,
        crypto: CryptoDep,
        user: Writer,
        workdir: DraftWorkdirDep,
        space_id: uuid.UUID,
        branch_id: uuid.UUID,
        slug: str,
        file: UploadFile | None = None,
        reviewed: Annotated[bool, Form()] = False,
        refuse: Annotated[list[str] | None, Form()] = None,
    ) -> dict[str, object]:
        """Importer : applique l'import (template enregistré, réglages de lecture,
        stratégie de suppression) à un fichier déposé — ou, sans fichier, au fichier
        d'exemple — et écrit les éléments dans le brouillon de la personne.

        - **lignes taguées** (ticket 43478978) : si la cible en tague (valeur absente
          du référentiel lié) et que la personne ne les a pas encore vues
          (`reviewed` faux), rien n'est écrit ni journalisé : 409
          `import_review_required`, compte rendu joint. Elle renvoie ensuite le même
          fichier avec `reviewed` et les lignes qu'elle refuse (`refuse`, « ligne 3 ») :
          elles ne sont pas écrites, et la stratégie de suppression ne les touche pas ;

        - **fusion** : corrige les éléments qui diffèrent, ajoute ceux qui manquent ;
          les absents suivent la stratégie de suppression ; un élément à supprimer
          encore utilisé est gardé, et le reste de l'import s'applique ;
        - **tout ou rien** : la moindre erreur refuse l'import (422
          `import_refused`, compte rendu joint) et rien n'est écrit ;
        - un fichier sans changement n'écrit rien (`revision` nulle) ;
        - chaque import, refusé compris, est journalisé (auteur, fichier, stratégie,
          compte rendu, éléments gardés car utilisés : `run_id`)."""
        owner = owner_of(request)
        raw: bytes | None = None
        file_name = ""
        if file is not None:
            raw = await file.read(MAX_IMPORT_BYTES + 1)
            if len(raw) > MAX_IMPORT_BYTES:
                raise ApiError("import_file_too_large", status_code=413, limit=MAX_IMPORT_BYTES)
            file_name = file.filename or ""
        handle = await adapter.open(conn, crypto, workdir, space_id, branch_id, user)

        def read(root: Path) -> tuple[ImportTarget, list[Any], Import]:
            target, current = adapter.current(root, owner)
            return target, current, read_one(owner, slug)(root)

        target, current, imp = await handle.read(read)
        deletion = imp.declaration.deletion
        if raw is None:
            raw, file_name = imp.example, example_name(slug, imp.declaration.format)
        example = _example_of(imp, None, raw)
        if not example.records:
            raise ApiError("import_file_empty", status_code=400)
        # Le calcul (une évaluation du template par ligne) ne bloque pas le serveur.
        done = await asyncio.to_thread(transform, target, imp.template, example)
        journal: dict[str, Any] = {"file": file_name, "deletion": deletion}
        if not done.ok:
            refused = report(target, current, done)
            await record_import(
                conn, adapter.journal_type, user, branch_id, owner, slug,
                outcome=REFUSED, result=refused, **journal,
            )  # fmt: skip
            raise ApiError(
                "import_refused", status_code=422, errors=refused.get("errors", 0), report=refused
            )
        if done.tags and not reviewed:
            raise ApiError(
                "import_review_required",
                status_code=409,
                tagged=len(done.tags),
                report=report(target, current, done),
            )
        rejected = done.refused_indices(set(refuse or []))

        def write(root: Path) -> tuple[dict[str, Any], list[Absent]]:
            # Relu dans l'écriture : la fusion et le plan de suppression se font sur
            # les éléments du moment, et sur ce que l'import a créé jusqu'ici.
            now_target, now = adapter.current(root, owner)
            created = read_created(root, folder(owner), slug)
            plan = deletion_plan(now_target, now, done, deletion, created)
            items = imported_items(now_target, now, done, plan, rejected)
            assert items is not None  # done.ok
            adapter.write_items(root, owner, now_target, now, items)
            after = created_after(now_target, now, items, created)
            if after != created:
                write_created(root, folder(owner), slug, after)
            return report(now_target, now, done, plan, rejected), plan.in_use

        result: dict[str, Any]
        kept: list[Absent]
        (result, kept), revision = await handle.write(f"import {slug} {adapter.into(owner)}", write)
        run_id = await record_import(
            conn, adapter.journal_type, user, branch_id, owner, slug,
            outcome=IMPORTED if revision else UNCHANGED, result=result, revision=revision,
            kept_in_use=[_kept_payload(a) for a in kept], **journal,
        )  # fmt: skip
        # Fin d'une modification en masse : une passe de validation est demandée pour le
        # brouillon de la personne (enabler 6b171b74).
        await request_pass(conn, ViewKey(space_id, branch_id, workdir.user_id), IMPORT)
        last = (await last_of(conn, branch_id, owner)).get(slug)
        return {
            **result,
            "deletion": deletion,
            "file": file_name,
            "revision": revision,
            "run_id": run_id,
            "last_import": last.at if last else None,
        }

    @router.get("/{slug}/executions/{run_id}")
    async def get_run(
        request: Request,
        conn: ConnDep,
        crypto: CryptoDep,
        user: Reader,
        workdir: DraftWorkdirDep,
        space_id: uuid.UUID,
        branch_id: uuid.UUID,
        slug: str,
        run_id: int,
    ) -> dict[str, object]:
        """Une exécution de l'import (ticket 6d36073b) : quand, par qui, le fichier
        intégré, la stratégie appliquée, et les éléments gardés car encore utilisés,
        chacun avec ses utilisations au moment de l'import."""
        owner = owner_of(request)
        run = await read_run(conn, adapter.journal_type, branch_id, owner, slug, run_id)
        if run is None:
            raise ApiError("import_run_not_found", status_code=404, run=run_id)
        handle = await adapter.open(conn, crypto, workdir, space_id, branch_id, user)
        try:
            imp: Import | None = await handle.read(read_one(owner, slug))
        except ApiError:
            imp = None  # l'import a été supprimé depuis : sa page reste lisible
        deletion = str(run.detail.get("deletion") or "")
        return {
            "id": run.id,
            "owner": owner,
            "slug": slug,
            "label": imp.declaration.label if imp is not None else slug,
            "at": run.at,
            "by": run.by,
            "file": run.detail.get("file", ""),
            "deletion": deletion,
            "deletion_label": DELETION_LABELS.get(deletion, deletion),
            "outcome": run.detail.get("outcome", ""),
            "counts": {
                k: run.detail.get(k, 0)
                for k in ("new", "modified", "deleted", "kept", "tagged", "refused")
            },
            "kept_in_use": run.kept,
        }

    return router
