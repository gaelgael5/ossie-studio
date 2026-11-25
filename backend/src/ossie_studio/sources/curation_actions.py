"""Gestes de curation qui écrivent : retenir, retirer, enrichir, rafraîchir.

Service commun à l'écran (routes `api/curation.py`) et à l'agent (primitives MCP
d'écriture) : une seule règle métier. Chaque geste écrit dans le brouillon de
la personne (`DraftWorkdir.commit_to_branch`), jamais dans un commit, et rend la
révision du brouillon (None si rien n'a changé) et les chemins touchés.

Mode strict (agent) : un champ inconnu est refusé (`UnknownFieldsError`) au
lieu d'être ignoré, comme l'écran le fait. Un agent qui se trompe de nom de
champ doit le savoir.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ossie_studio.drafts.workdir import DraftWorkdir
from ossie_studio.governance.classification_resolve import check_declaration
from ossie_studio.governance.repo import Scheme, read_schemes
from ossie_studio.models import GitSpace
from ossie_studio.parsers.conventions import load_schema
from ossie_studio.sources.asset_files import extend_name
from ossie_studio.sources.collect_core import RAW_DIR
from ossie_studio.sources.curation import (
    ReviewDecisions,
    ReviewResult,
    apply_review_fn,
    classify_asset_fn,
    enrich_asset_fn,
    refresh_assets_fn,
    refresh_targets,
    remove_asset_fn,
    retain_asset_fn,
    set_tri_rules_fn,
)
from ossie_studio.sources.paths import (
    ASSETS_DIR,
    MANIFEST_FILENAME,
    safe_assets_path,
    safe_source_path,
    source_rel_dir,
)
from ossie_studio.sources.tri import validate_tri_rules


class RulesInvalidError(Exception):
    """Règles de tri invalides : chaque erreur nomme la règle (`triage[i]:…`)."""

    def __init__(self, fields: list[str]) -> None:
        super().__init__(", ".join(fields))
        self.fields = fields


class SourceMissingError(Exception):
    def __init__(self, slug: str) -> None:
        super().__init__(slug)
        self.slug = slug


class UnknownFieldsError(Exception):
    """Champs demandés absents de l'objet (mode strict)."""

    def __init__(self, fields: list[str]) -> None:
        super().__init__(", ".join(fields))
        self.fields = fields


@dataclass(frozen=True)
class CurationTarget:
    """Où et pour qui écrire : la branche d'un espace, dans le brouillon de la
    personne (`workdir`), sous son identité (`author`)."""

    workdir: DraftWorkdir
    space: GitSpace
    branch_ref: str
    username: str
    token: str | None
    author: str
    # Précision de l'intention journalisée (« via l'agent … ») ; None à l'écran.
    via: str | None = None
    strict: bool = False

    async def write(
        self, intent: str, fn: Callable[[Path], None], *, expect_head: str | None = None
    ) -> str | None:
        message = f"{intent} ({self.via})" if self.via else intent
        return await self.workdir.commit_to_branch(
            self.space.id,
            self.branch_ref,
            self.space.repo_url,
            self.username,
            self.token,
            self.author,
            self.author,
            message,
            fn,
            expect_head=expect_head,
        )


@dataclass(frozen=True)
class WriteResult:
    # Repère de révision du brouillon (`draft:<n>`), None si rien n'a changé.
    revision: str | None
    # Chemins du dépôt touchés dans le brouillon (vide si rien n'a changé).
    paths: list[str] = field(default_factory=list)


def asset_repo_path(slug: str, path: str) -> str:
    return f"{source_rel_dir(slug)}/{ASSETS_DIR}/{path}"


def _result(revision: str | None, slug: str, paths: list[str]) -> WriteResult:
    return WriteResult(revision, [asset_repo_path(slug, p) for p in paths] if revision else [])


def _touched_files(assets: Path, path: str, inner: Callable[[Path], None], root: Path) -> list[str]:
    """Applique `inner` et rend, parmi l'asset et son `.extend.yaml` (ticket
    7a284e55), les fichiers dont le contenu a changé : un enrichissement ne
    touche d'ordinaire que le `.extend.yaml`, et les chemins annoncés (journal,
    réponse MCP) doivent être exacts."""
    names = [path, extend_name(path)]

    def snapshot() -> list[bytes | None]:
        return [(assets / n).read_bytes() if (assets / n).is_file() else None for n in names]

    before = snapshot()
    inner(root)
    after = snapshot()
    return [n for n, b, a in zip(names, before, after, strict=True) if b != a]


def _properties(file: Path) -> set[str] | None:
    if not file.is_file():
        return None
    data = load_schema(file.read_text(encoding="utf-8"))
    props = data.get("properties") if isinstance(data, dict) else None
    return set(props) if isinstance(props, dict) else set()


def _check_fields(file: Path, fields: list[str] | None) -> None:
    """Mode strict : refuse les champs absents de l'objet lu dans `file`."""
    if not fields:
        return
    known = _properties(file)
    if known is None:
        return  # l'objet manque : le geste lui-même le signale
    unknown = sorted(set(fields) - known)
    if unknown:
        raise UnknownFieldsError(unknown)


async def retain(
    target: CurationTarget, slug: str, path: str, fields: list[str] | None
) -> WriteResult:
    """Retenir un objet brut (en entier si `fields` est None). Lève
    AssetOriginMissingError si le brut n'existe pas."""
    inner = retain_asset_fn(slug, path, fields)

    def write(root: Path) -> None:
        if target.strict:
            _check_fields(safe_source_path(root, slug) / RAW_DIR / path, fields)
        inner(root)

    revision = await target.write(f"curation {slug} : retenir {path}", write)
    return _result(revision, slug, [path])


async def retain_many(target: CurationTarget, slug: str, paths: list[str]) -> WriteResult:
    """Retenir plusieurs objets bruts en entier, en un seul commit (« créer un
    asset avec ses enfants ») ; un objet déjà retenu est laissé tel quel.
    Lève AssetOriginMissingError si l'un des bruts n'existe pas."""
    unique = list(dict.fromkeys(paths))
    written: list[str] = []

    def write(root: Path) -> None:
        assets = safe_assets_path(root, slug)
        for path in unique:
            if (assets / path).is_file():
                continue
            retain_asset_fn(slug, path, None)(root)
            written.append(path)

    revision = await target.write(f"curation {slug} : retenir {len(unique)} objet(s)", write)
    return _result(revision, slug, written)


async def remove(target: CurationTarget, slug: str, path: str) -> WriteResult:
    """Retirer un asset (sans effet s'il n'existe pas)."""
    inner = remove_asset_fn(slug, path)
    touched: list[str] = []

    def write(root: Path) -> None:
        touched[:] = _touched_files(safe_assets_path(root, slug), path, inner, root)

    revision = await target.write(f"curation {slug} : retirer {path}", write)
    return _result(revision, slug, touched)


async def enrich(
    target: CurationTarget,
    slug: str,
    path: str,
    object_values: dict[str, Any] | None,
    field_values: dict[str, dict[str, Any]],
) -> WriteResult:
    """Poser ou retirer l'enrichissement de l'objet et de ses champs. Seules les
    clés données changent (une valeur vide retire la clé). Lève
    AssetNotRetainedError si l'asset n'existe pas."""
    inner = enrich_asset_fn(slug, path, object_values, field_values)
    touched: list[str] = []

    def write(root: Path) -> None:
        if target.strict:
            _check_fields(safe_assets_path(root, slug) / path, list(field_values))
        touched[:] = _touched_files(safe_assets_path(root, slug), path, inner, root)

    revision = await target.write(f"curation {slug} : enrichir {path}", write)
    return _result(revision, slug, touched)


class ClassificationRefusedError(Exception):
    """Déclaration refusée : schéma inconnu, niveau inconnu, ou plusieurs valeurs
    pour un schéma à valeur unique."""

    def __init__(self, scheme: str, reason: str, field: str = "") -> None:
        super().__init__(reason)
        self.scheme = scheme
        self.reason = reason
        self.field = field


def _stored_values(
    schemes: dict[str, Scheme], values: dict[str, list[str]], field: str
) -> dict[str, Any]:
    """Valide une déclaration et la met sous la forme stockée : un nom pour un
    schéma à valeur unique, une liste pour un multi-valeurs, [] pour un retrait."""
    out: dict[str, Any] = {}
    for scheme_id, raw in values.items():
        names = [n for n in dict.fromkeys(raw) if n.strip()]
        scheme = schemes.get(scheme_id)
        reason = check_declaration(scheme, names)
        if reason is not None:
            raise ClassificationRefusedError(scheme_id, reason, field)
        if not names or scheme is None:
            out[scheme_id] = []
        else:
            out[scheme_id] = names if scheme.multi_value else names[0]
    return out


async def classify(
    target: CurationTarget,
    slug: str,
    path: str,
    object_values: dict[str, list[str]] | None,
    field_values: dict[str, dict[str, list[str]]],
) -> WriteResult:
    """Déclarer ou retirer la classification de l'asset et de ses champs (ticket
    5e8680d7), dans son `.extend.yaml`. Les valeurs sont validées contre les
    schémas de classification **de la vue écrite** (ceux que l'écran montre).
    Lève AssetNotRetainedError, UnknownFieldsError (mode strict) ou
    ClassificationRefusedError."""
    touched: list[str] = []

    def write(root: Path) -> None:
        schemes = {s.id: s for s in read_schemes(root)}
        obj = _stored_values(schemes, object_values, "") if object_values is not None else None
        per_field = {
            name: _stored_values(schemes, values, name) for name, values in field_values.items()
        }
        if target.strict:
            _check_fields(safe_assets_path(root, slug) / path, list(field_values))
        inner = classify_asset_fn(slug, path, obj, per_field)
        touched[:] = _touched_files(safe_assets_path(root, slug), path, inner, root)

    revision = await target.write(f"curation {slug} : classer {path}", write)
    return _result(revision, slug, touched)


async def refresh(target: CurationTarget, slug: str, paths: list[str] | None) -> WriteResult:
    """Rafraîchir depuis le brut les assets retenus (tous si `paths` est None).
    Ne touche que ceux qui changeraient ; sans changement, rien n'est écrit."""
    changed = await target.workdir.read_branch(
        target.space.id,
        target.branch_ref,
        target.space.repo_url,
        target.username,
        target.token,
        lambda root: refresh_targets(root, slug, paths),
    )
    if not changed:
        return WriteResult(None)
    intent = f"curation {slug} : rafraîchir {len(changed)} asset(s) : {', '.join(changed)}"
    revision = await target.write(intent, refresh_assets_fn(slug, changed))
    return _result(revision, slug, changed)


# --- Règles de tri ------------------------------------------------------------------------


def clean_tri_rules(rules: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Forme enregistrée d'une règle : cible, motif, genre, et libellé s'il est
    renseigné."""
    cleaned: list[dict[str, Any]] = []
    for rule in rules:
        item: dict[str, Any] = {
            "target": rule.get("target"),
            "pattern": rule.get("pattern"),
            "kind": rule.get("kind"),
        }
        label = str(rule.get("label") or "").strip()
        if label:
            item["label"] = label
        cleaned.append(item)
    return cleaned


async def set_tri_rules(
    target: CurationTarget, slug: str, rules: list[dict[str, Any]]
) -> tuple[WriteResult, list[dict[str, Any]]]:
    """Remplacer les règles de tri d'une source (liste vide = aucune règle).
    Lève RulesInvalidError, SourceMissingError, ManifestError (manifeste illisible)."""
    cleaned = clean_tri_rules(rules)
    errors = validate_tri_rules(cleaned)
    if errors:
        raise RulesInvalidError(errors)
    try:
        revision = await target.write(
            f"curation {slug} : règles de tri", set_tri_rules_fn(slug, cleaned)
        )
    except FileNotFoundError:
        raise SourceMissingError(slug) from None
    paths = [f"{source_rel_dir(slug)}/{MANIFEST_FILENAME}"] if revision else []
    return WriteResult(revision, paths), cleaned


# --- Revue des changements -------------------------------------------------------------------


def invalid_review_decisions(review: ReviewResult, decisions: ReviewDecisions) -> list[str]:
    """Mode strict : décisions qui ne visent pas un élément de la revue, ou pas
    un élément de la bonne nature. Vide = toutes les décisions sont valides.

    rafraîchir ↔ champ « changed », reprendre ↔ champ « appeared », retirer un
    champ ↔ champ « disappeared », retirer un objet ↔ objet « object-disappeared »,
    retenir un objet ↔ objet « new »."""
    objects = {o.path: o for o in review.objects}

    def field_change(path: str, name: str) -> str | None:
        obj = objects.get(path)
        found = next((f for f in obj.fields if f.name == name), None) if obj else None
        return found.change if found else None

    bad: list[str] = []
    for kind, grouped, expected in (
        ("apply_fields", decisions.apply_fields, "changed"),
        ("take_fields", decisions.take_fields, "appeared"),
        ("remove_fields", decisions.remove_fields, "disappeared"),
    ):
        for path, names in sorted(grouped.items()):
            bad.extend(
                f"{kind}:{path}#{name}" for name in names if field_change(path, name) != expected
            )
    for kind, paths, expected in (
        ("remove_objects", decisions.remove_objects, "object-disappeared"),
        ("retain_objects", decisions.retain_objects, "new"),
    ):
        for path in paths:
            obj = objects.get(path)
            if obj is None or obj.status != expected:
                bad.append(f"{kind}:{path}")
    return bad


def review_paths(slug: str, decisions: ReviewDecisions) -> list[str]:
    """Chemins qu'une revue appliquée peut toucher : les assets visés."""
    names = (
        set(decisions.apply_fields)
        | set(decisions.take_fields)
        | set(decisions.remove_fields)
        | set(decisions.remove_objects)
        | set(decisions.retain_objects)
    )
    return [asset_repo_path(slug, n) for n in sorted(names)]


async def apply_review(
    target: CurationTarget,
    slug: str,
    head_sha: str,
    decisions: ReviewDecisions,
) -> WriteResult:
    """Appliquer des décisions de revue, en une écriture dans le brouillon. Si la
    branche publiée a bougé depuis la lecture de la revue (tête ≠ `head_sha`),
    WorkdirError « review_stale » avant toute écriture : les décisions visaient
    une comparaison qui n'est plus la bonne. Un changement non décidé est laissé
    tel quel. Plus de point de reprise (ticket docflow aad1d3c6)."""
    revision = await target.write(
        f"curation {slug} : décisions de revue appliquées",
        apply_review_fn(slug, decisions),
        expect_head=head_sha,
    )
    paths = review_paths(slug, decisions) if revision else []
    return WriteResult(revision, paths)
