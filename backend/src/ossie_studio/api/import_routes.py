"""Routes d'import communes à toutes les cibles (enabler 4f32c8c6).

Reprises telles quelles de l'onglet « Imports » des référentiels (tickets
cda9c43a et a909dc66) : déclarer un import, déposer un fichier d'exemple, régler
sa lecture, écrire et essayer le template JSLT, puis importer. Ce qui dépend de
la cible passe par un **adaptateur** (`ImportAdapter`) : où vivent ses imports,
ses éléments actuels, sa validation (`ImportTarget`), l'écriture, les modes
qu'elle accepte, le type d'objet de son journal et les droits.

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
from ossie_studio.errors import ApiError
from ossie_studio.imports import files
from ossie_studio.imports.declaration import Import, ImportDeclaration, example_name
from ossie_studio.imports.engine import (
    MERGE,
    Example,
    ExampleError,
    check_template,
    detect_reading,
    imported_items,
    preview,
    read_example,
    report,
    run_template,
    transform,
)
from ossie_studio.imports.journal import (
    IMPORTED,
    REFUSED,
    UNCHANGED,
    LastImport,
    last_imports,
    record_import,
)
from ossie_studio.imports.target import ImportTarget

MAX_EXAMPLE_BYTES = 5 * 1024 * 1024
MAX_IMPORT_BYTES = 10 * 1024 * 1024
MAX_TEMPLATE_LENGTH = 256 * 1024
STARTER_TEMPLATE = "{\n}\n"


class CreateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str = ""
    slug: str = ""
    format: Literal["csv", "json"] = "csv"


class RenameBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str = ""


class ReadingBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    separator: str = ","
    encoding: str = "utf-8"
    header: bool = True


Mode = Literal["merge", "replace"]


class TemplateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    template: str = Field(default="", max_length=MAX_TEMPLATE_LENGTH)


class TrialBody(TemplateBody):
    # Le mode de l'import à venir : en remplacement, l'essai compte les suppressions.
    mode: Mode = MERGE


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
    modes: tuple[str, ...]
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


def _summary(imp: Import, last: LastImport | None) -> dict[str, Any]:
    d = imp.declaration
    return {
        "slug": d.slug,
        "label": d.label,
        "format": d.format,
        "separator": d.separator,
        "encoding": d.encoding,
        "header": d.header,
        # Le dernier import abouti, lu dans le journal (None : jamais importé).
        "last_import": last.at if last else None,
        "last_import_by": last.by if last else None,
    }


def _detected(decl: ImportDeclaration, raw: bytes) -> ImportDeclaration:
    """Les réglages de lecture devinés sur le fichier déposé (ticket 123631e5) ;
    la personne les corrige ensuite dans « Fichier d'exemple » si besoin."""
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
                        header=body.header),
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
        imp: Import = await handle.read(read_one(owner, slug))
        return preview(_example_of(imp, body))

    def check_mode(mode: str) -> None:
        if mode not in adapter.modes:
            raise ApiError("invalid_request", status_code=400, fields=["mode"])

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
        d'exemple : éléments nouveaux, modifiés, inchangés, supprimés (mode
        remplacement), en erreur, et différences. Rien n'est écrit."""
        check_mode(body.mode)
        owner = owner_of(request)
        handle = await adapter.open(conn, crypto, workdir, space_id, branch_id, user)

        def read(root: Path) -> tuple[ImportTarget, list[Any], Import]:
            target, current = adapter.current(root, owner)
            return target, current, read_one(owner, slug)(root)

        target, current, imp = await handle.read(read)
        return run_template(target, current, body.template, _example_of(imp, None), body.mode)

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
        mode: Annotated[Mode, Form()] = MERGE,
        file: UploadFile | None = None,
    ) -> dict[str, object]:
        """Importer : applique l'import (template enregistré, réglages de lecture) à
        un fichier déposé — ou, sans fichier, au fichier d'exemple — et écrit les
        éléments dans le brouillon de la personne.

        - `mode` : `merge` (fusion par clé, par défaut) ou, si la cible le permet,
          `replace` ;
        - **tout ou rien** : la moindre erreur refuse l'import (422
          `import_refused`, compte rendu joint) et rien n'est écrit ;
        - un fichier sans changement n'écrit rien (`revision` nulle) ;
        - chaque import, refusé compris, est journalisé (auteur, fichier, mode,
          compte rendu)."""
        check_mode(mode)
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
        if raw is None:
            raw, file_name = imp.example, example_name(slug, imp.declaration.format)
        example = _example_of(imp, None, raw)
        if not example.records:
            raise ApiError("import_file_empty", status_code=400)
        # Le calcul (une évaluation du template par ligne) ne bloque pas le serveur.
        done = await asyncio.to_thread(transform, target, imp.template, example)
        journal: dict[str, Any] = {"file": file_name, "mode": mode}
        if not done.ok:
            refused = report(target, current, done, mode)
            await record_import(
                conn, adapter.journal_type, user, branch_id, owner, slug,
                outcome=REFUSED, result=refused, **journal,
            )  # fmt: skip
            raise ApiError(
                "import_refused", status_code=422, errors=refused.get("errors", 0), report=refused
            )

        def write(root: Path) -> dict[str, Any]:
            # Relu dans l'écriture : la fusion se fait sur les éléments du moment.
            now_target, now = adapter.current(root, owner)
            items = imported_items(now_target, now, done, mode)
            assert items is not None  # done.ok
            adapter.write_items(root, owner, now_target, now, items)
            return report(now_target, now, done, mode)

        result: dict[str, Any]
        result, revision = await handle.write(
            f"import {slug} {adapter.into(owner)} ({mode})", write
        )
        await record_import(
            conn, adapter.journal_type, user, branch_id, owner, slug,
            outcome=IMPORTED if revision else UNCHANGED, result=result, revision=revision,
            **journal,
        )  # fmt: skip
        last = (await last_of(conn, branch_id, owner)).get(slug)
        return {
            **result,
            "mode": mode,
            "file": file_name,
            "revision": revision,
            "last_import": last.at if last else None,
        }

    return router
