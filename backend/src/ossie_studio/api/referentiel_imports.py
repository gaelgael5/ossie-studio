"""Onglet « Imports » d'un référentiel (ticket cda9c43a) : déclarer un import,
déposer un fichier d'exemple, régler sa lecture, écrire et essayer le template
JSLT ; puis **importer** (ticket a909dc66) : appliquer l'import à un fichier et
écrire les valeurs du référentiel.

Routes de l'écran, pour une personne connectée (session) : les API des
référentiels sont **en lecture seule** (Gael, 24 novembre : « Les api sur les
référentiels sont en lecture seules »), une clé d'API n'importe rien.

Les fichiers sont ceux de la brique « format » (`imports/<slug>.yaml`, `.jslt`,
`.exemple.<csv|json>`, `valeurs.json`), écrits dans le brouillon de la personne
(jamais dans la version publiée : la publication reste un geste humain).
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import replace
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Form, UploadFile
from pydantic import BaseModel, ConfigDict, Field

from ossie_studio.api.common import ReferentielsRead, ReferentielsWrite
from ossie_studio.api.governance_common import CryptoDep, DraftWorkdirDep
from ossie_studio.api.referentiels import read_in, referentiel_target, write_in
from ossie_studio.deps import ConnDep
from ossie_studio.errors import ApiError
from ossie_studio.referentiels.import_journal import (
    IMPORTED,
    REFUSED,
    UNCHANGED,
    LastImport,
    last_imports,
    record_import,
)
from ossie_studio.referentiels.importing import (
    MERGE,
    Example,
    ExampleError,
    check_template,
    detect_reading,
    imported_values,
    preview,
    read_example,
    report,
    run_template,
    transform,
)
from ossie_studio.referentiels.imports import ImportDeclaration, ReferentielImport, example_name
from ossie_studio.referentiels.repo import (
    Referentiel,
    delete_import,
    import_paths,
    read_import,
    read_referentiel,
    write_import,
    write_referentiel,
)

router = APIRouter(prefix="/api/spaces/{space_id}/branches/{branch_id}/referentiels/{name}/imports")

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


def _summary(imp: ReferentielImport, last: LastImport | None) -> dict[str, Any]:
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


def _detail(name: str, imp: ReferentielImport, last: LastImport | None = None) -> dict[str, Any]:
    return {
        **_summary(imp, last),
        "template": imp.template,
        "example_name": example_name(imp.slug, imp.declaration.format),
        "example_size": len(imp.example),
        "paths": import_paths(name, imp.declaration),
    }


async def _target(
    conn: ConnDep,
    crypto: CryptoDep,
    workdir: DraftWorkdirDep,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
    user: Any,
) -> Any:
    return await referentiel_target(conn, crypto, workdir, space_id, branch_id, user)


@router.get("")
async def list_imports(
    conn: ConnDep,
    crypto: CryptoDep,
    user: ReferentielsRead,
    workdir: DraftWorkdirDep,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
    name: str,
) -> dict[str, object]:
    target = await _target(conn, crypto, workdir, space_id, branch_id, user)
    ref: Referentiel = await read_in(target, lambda root: read_referentiel(root, name))
    last = await last_imports(conn, branch_id, name)
    return {"imports": [_summary(i, last.get(i.slug)) for i in ref.imports]}


@router.post("", status_code=201)
async def create_import(
    conn: ConnDep,
    crypto: CryptoDep,
    user: ReferentielsWrite,
    workdir: DraftWorkdirDep,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
    name: str,
    body: CreateBody,
) -> dict[str, object]:
    decl = ImportDeclaration(body.slug.strip(), body.label.strip(), body.format)
    target = await _target(conn, crypto, workdir, space_id, branch_id, user)
    imp: ReferentielImport
    imp, _rev = await write_in(
        target,
        f"import {decl.slug} de {name}",
        lambda root: write_import(root, name, decl, STARTER_TEMPLATE, b"", create=True),
    )
    return _detail(name, imp)


@router.get("/{slug}")
async def get_import(
    conn: ConnDep,
    crypto: CryptoDep,
    user: ReferentielsRead,
    workdir: DraftWorkdirDep,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
    name: str,
    slug: str,
) -> dict[str, object]:
    target = await _target(conn, crypto, workdir, space_id, branch_id, user)
    imp: ReferentielImport = await read_in(target, lambda root: read_import(root, name, slug))
    last = await last_imports(conn, branch_id, name)
    return _detail(name, imp, last.get(slug))


async def _update(
    conn: ConnDep,
    crypto: CryptoDep,
    workdir: DraftWorkdirDep,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
    user: Any,
    name: str,
    slug: str,
    intent: str,
    change: Any,
) -> dict[str, object]:
    """Relit l'import dans le brouillon, applique `change` (déclaration,
    template, exemple), et l'écrit."""
    target = await _target(conn, crypto, workdir, space_id, branch_id, user)

    def write(root: Path) -> ReferentielImport:
        decl, template, example = change(read_import(root, name, slug))
        return write_import(root, name, decl, template, example, create=False)

    imp: ReferentielImport
    imp, _rev = await write_in(target, intent, write)
    return _detail(name, imp)


@router.patch("/{slug}")
async def rename_import(
    conn: ConnDep,
    crypto: CryptoDep,
    user: ReferentielsWrite,
    workdir: DraftWorkdirDep,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
    name: str,
    slug: str,
    body: RenameBody,
) -> dict[str, object]:
    """Renommer : le libellé seulement, le slug ne change pas."""
    label = body.label.strip()
    return await _update(
        conn, crypto, workdir, space_id, branch_id, user, name, slug,
        f"renomme l'import {slug} de {name}",
        lambda imp: (replace(imp.declaration, label=label), imp.template, imp.example),
    )  # fmt: skip


@router.delete("/{slug}", status_code=204)
async def remove_import(
    conn: ConnDep,
    crypto: CryptoDep,
    user: ReferentielsWrite,
    workdir: DraftWorkdirDep,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
    name: str,
    slug: str,
) -> None:
    target = await _target(conn, crypto, workdir, space_id, branch_id, user)
    await write_in(
        target, f"supprime l'import {slug} de {name}", lambda root: delete_import(root, name, slug)
    )


@router.put("/{slug}/exemple")
async def upload_example(
    conn: ConnDep,
    crypto: CryptoDep,
    user: ReferentielsWrite,
    workdir: DraftWorkdirDep,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
    name: str,
    slug: str,
    file: UploadFile,
) -> dict[str, object]:
    """Déposer ou remplacer le fichier d'exemple (5 Mo au plus)."""
    raw = await file.read(MAX_EXAMPLE_BYTES + 1)
    if len(raw) > MAX_EXAMPLE_BYTES:
        raise ApiError("import_example_too_large", status_code=413, limit=MAX_EXAMPLE_BYTES)
    return await _update(
        conn, crypto, workdir, space_id, branch_id, user, name, slug,
        f"exemple de l'import {slug} de {name}",
        lambda imp: (_detected(imp.declaration, raw), imp.template, raw),
    )  # fmt: skip


def _detected(decl: ImportDeclaration, raw: bytes) -> ImportDeclaration:
    """Les réglages de lecture devinés sur le fichier déposé (ticket 123631e5) ;
    la personne les corrige ensuite dans « Fichier d'exemple » si besoin."""
    if decl.format != "csv" or not raw:
        return decl
    encoding, separator = detect_reading(raw)
    return replace(decl, encoding=encoding, separator=separator or decl.separator)


@router.put("/{slug}/lecture")
async def set_reading(
    conn: ConnDep,
    crypto: CryptoDep,
    user: ReferentielsWrite,
    workdir: DraftWorkdirDep,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
    name: str,
    slug: str,
    body: ReadingBody,
) -> dict[str, object]:
    """Réglages de lecture du CSV : séparateur, encodage, ligne d'en-tête."""
    return await _update(
        conn, crypto, workdir, space_id, branch_id, user, name, slug,
        f"lecture de l'import {slug} de {name}",
        lambda imp: (
            replace(imp.declaration, separator=body.separator, encoding=body.encoding,
                    header=body.header),
            imp.template,
            imp.example,
        ),
    )  # fmt: skip


@router.put("/{slug}/template")
async def save_template(
    conn: ConnDep,
    crypto: CryptoDep,
    user: ReferentielsWrite,
    workdir: DraftWorkdirDep,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
    name: str,
    slug: str,
    body: TemplateBody,
) -> dict[str, object]:
    """Enregistrer le template (même en cours d'écriture) ; la réponse dit s'il
    s'analyse."""
    detail = await _update(
        conn, crypto, workdir, space_id, branch_id, user, name, slug,
        f"template de l'import {slug} de {name}",
        lambda imp: (imp.declaration, body.template, imp.example),
    )  # fmt: skip
    return {**detail, "template_problems": [p.payload() for p in check_template(body.template)]}


def _example_of(
    imp: ReferentielImport, reading: ReadingBody | None, raw: bytes | None = None
) -> Example:
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


@router.post("/{slug}/apercu")
async def preview_example(
    conn: ConnDep,
    crypto: CryptoDep,
    user: ReferentielsRead,
    workdir: DraftWorkdirDep,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
    name: str,
    slug: str,
    body: ReadingBody | None = None,
) -> dict[str, object]:
    """Aperçu des premières lignes et chemins détectés, avec les réglages donnés
    (ceux de l'import s'il n'y en a pas) : changer le séparateur met l'aperçu à jour
    avant même de l'enregistrer."""
    target = await _target(conn, crypto, workdir, space_id, branch_id, user)
    imp: ReferentielImport = await read_in(target, lambda root: read_import(root, name, slug))
    return preview(_example_of(imp, body))


@router.post("/{slug}/essai")
async def try_template(
    conn: ConnDep,
    crypto: CryptoDep,
    user: ReferentielsRead,
    workdir: DraftWorkdirDep,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
    name: str,
    slug: str,
    body: TrialBody,
) -> dict[str, object]:
    """Essaie le template (le texte donné, enregistré ou non) sur le fichier
    d'exemple : valeurs nouvelles, modifiées, inchangées, supprimées (mode
    remplacement), en erreur, et différences. Rien n'est écrit."""
    target = await _target(conn, crypto, workdir, space_id, branch_id, user)

    def read(root: Path) -> tuple[Referentiel, ReferentielImport]:
        return read_referentiel(root, name), read_import(root, name, slug)

    ref: Referentiel
    imp: ReferentielImport
    ref, imp = await read_in(target, read)
    return run_template(ref.model, ref.values, body.template, _example_of(imp, None), body.mode)


@router.post("/{slug}/import")
async def run_import(
    conn: ConnDep,
    crypto: CryptoDep,
    user: ReferentielsWrite,
    workdir: DraftWorkdirDep,
    space_id: uuid.UUID,
    branch_id: uuid.UUID,
    name: str,
    slug: str,
    mode: Annotated[Mode, Form()] = MERGE,
    file: UploadFile | None = None,
) -> dict[str, object]:
    """Importer : applique l'import (template enregistré, réglages de lecture) à
    un fichier déposé — ou, sans fichier, au fichier d'exemple — et écrit les
    valeurs du référentiel dans le brouillon de la personne.

    - `mode` : `merge` (fusion par clé, par défaut) ou `replace` (remplacer
      toutes les valeurs) ;
    - **tout ou rien** : la moindre erreur (valeur invalide, clé en double dans
      le fichier) refuse l'import (422 `import_refused`, compte rendu joint) et
      rien n'est écrit ;
    - un fichier sans changement n'écrit rien (`revision` nulle) ;
    - chaque import, refusé compris, est journalisé (auteur, fichier, mode,
      compte rendu)."""
    raw: bytes | None = None
    file_name = ""
    if file is not None:
        raw = await file.read(MAX_IMPORT_BYTES + 1)
        if len(raw) > MAX_IMPORT_BYTES:
            raise ApiError("import_file_too_large", status_code=413, limit=MAX_IMPORT_BYTES)
        file_name = file.filename or ""
    target = await _target(conn, crypto, workdir, space_id, branch_id, user)

    def read(root: Path) -> tuple[Referentiel, ReferentielImport]:
        return read_referentiel(root, name), read_import(root, name, slug)

    ref: Referentiel
    imp: ReferentielImport
    ref, imp = await read_in(target, read)
    if raw is None:
        raw, file_name = imp.example, example_name(slug, imp.declaration.format)
    example = _example_of(imp, None, raw)
    if not example.records:
        raise ApiError("import_file_empty", status_code=400)
    # Le calcul (une évaluation du template par ligne) ne bloque pas le serveur.
    done = await asyncio.to_thread(transform, ref.model, imp.template, example)
    journal = {"file": file_name, "mode": mode}
    if not done.ok:
        refused = report(ref.model, ref.values, done, mode)
        await record_import(
            conn, user, branch_id, name, slug, outcome=REFUSED, result=refused, **journal
        )
        raise ApiError(
            "import_refused", status_code=422, errors=refused.get("errors", 0), report=refused
        )

    def write(root: Path) -> dict[str, Any]:
        # Relu dans l'écriture : la fusion se fait sur les valeurs du moment.
        current = read_referentiel(root, name)
        values = imported_values(current.model, current.values, done, mode)
        assert values is not None  # done.ok
        write_referentiel(root, name, current.model, values, create=False)
        return report(current.model, current.values, done, mode)

    result: dict[str, Any]
    result, revision = await write_in(target, f"import {slug} dans {name} ({mode})", write)
    await record_import(
        conn,
        user,
        branch_id,
        name,
        slug,
        outcome=IMPORTED if revision else UNCHANGED,
        result=result,
        revision=revision,
        **journal,
    )
    last = (await last_imports(conn, branch_id, name)).get(slug)
    return {
        **result,
        "mode": mode,
        "file": file_name,
        "revision": revision,
        "last_import": last.at if last else None,
    }
