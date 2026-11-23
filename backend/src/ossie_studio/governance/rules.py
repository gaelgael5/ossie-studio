"""Règles propres à la gouvernance dans Git.

- **Validation** : les contraintes que garantissait PostgreSQL (unicités, un seul
  schéma principal, champs valides) deviennent des erreurs du journal ; elles
  bloquent la publication quand le brouillon les introduit.
- **Impact** : un asset dépend des tags gouvernés qu'il porte (`x-tags`, à tout
  niveau du schéma) ; une certification se rattache à un tag. Modifier ou
  supprimer l'objet de gouvernance impacte ces assets."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

from ossie_studio.drafts.impacts import DEPENDENCY_RULES
from ossie_studio.drafts.validation import VALIDATION_RULES, Finding, _digest
from ossie_studio.governance.certifications import validate_certification
from ossie_studio.governance.classifications import validate_level, validate_scheme
from ossie_studio.governance.repo import (
    CERTIFICATIONS_DIR,
    CLASSIFICATIONS_DIR,
    TAGS_DIR,
    cert_path,
    read_certs,
    read_schemes,
    read_tags,
    scheme_path,
    tag_path,
)
from ossie_studio.governance.tag_rules import TAG_REMOVALS_FILE, read_tag_rules, removal_errors
from ossie_studio.governance.tags import validate_tag_fields
from ossie_studio.parsers.conventions import SCHEMA_SUFFIX, load_schema


class GovernanceRule:
    name = "governance"

    def check(self, view: Path) -> list[Finding]:
        out: list[Finding] = []

        def add(rel: str, code: str, **args: str) -> None:
            out.append(Finding("error", rel, self.name, code, args, _digest(view / rel)))

        for tag in read_tags(view):
            rel = tag_path(tag.id)
            if not (view / rel).is_file():
                add(_tag_file_of(view, tag.id), "tag_id_mismatch", tag=tag.id)
                continue
            for code in validate_tag_fields(tag.id, tag.links, tag.properties):
                add(rel, code)
        seen_tags: dict[str, str] = {}
        seen_slugs: set[str] = set()
        for cert in read_certs(view):
            rel = cert_path(cert.id)
            if cert.slug and cert.slug in seen_slugs:
                add(rel, "certification_slug_duplicate", slug=cert.slug)
            seen_slugs.add(cert.slug)
            for code in validate_certification(cert.name, cert.rank, cert.tag):
                add(rel, code)
            if cert.tag in seen_tags:
                add(rel, "certification_tag_duplicate", tag=cert.tag)
            seen_tags.setdefault(cert.tag, rel)
        # Règles de tags : une règle invalide ne pose rien et est signalée ici,
        # jamais en faisant échouer l'indexation de l'explorateur.
        for rule_file in read_tag_rules(view):
            for code in rule_file.errors:
                add(rule_file.rel, code)
        for code in removal_errors(view):
            add(TAG_REMOVALS_FILE, code)
        primaries = [s for s in read_schemes(view) if s.is_primary]
        if len(primaries) > 1:
            for scheme in primaries:
                add(scheme_path(scheme.id), "classification_primary_duplicate")
        for scheme in read_schemes(view):
            rel = scheme_path(scheme.id)
            for code in validate_scheme(scheme.name, scheme.multi_value, scheme.is_primary):
                add(rel, code)
            names: set[str] = set()
            shorts: set[str] = set()
            for level in scheme.levels:
                for code in validate_level(level.name, level.short_name):
                    add(rel, code, level=level.name)
                if level.name in names:
                    add(rel, "level_name_duplicate", level=level.name)
                if level.short_name and level.short_name in shorts:
                    add(rel, "level_short_name_duplicate", level=level.short_name)
                names.add(level.name)
                shorts.add(level.short_name)
        return out


def _tag_file_of(view: Path, tag_id: str) -> str:
    for file in (view / TAGS_DIR).rglob("*.yaml"):
        if tag_id in file.read_text(encoding="utf-8"):
            return file.relative_to(view).as_posix()
    return tag_path(tag_id)


def _tags_in(node: Any) -> Iterator[str]:
    if isinstance(node, dict):
        for tag in node.get("x-tags") or []:
            if isinstance(tag, str):
                yield tag
        for value in node.values():
            yield from _tags_in(value)
    elif isinstance(node, list):
        for item in node:
            yield from _tags_in(item)


class GovernanceAssetsRule:
    """Asset → tags gouvernés qu'il porte (et certifications de ces tags)."""

    name = "governance_assets"

    def dependents(self, view: Path, changed: set[str]) -> dict[str, set[str]]:
        changed_tags: dict[str, str] = {}
        for path in changed:
            if path.startswith(f"{TAGS_DIR}/") and path.endswith(".yaml"):
                changed_tags[path[len(TAGS_DIR) + 1 : -5]] = path
        if any(p.startswith(f"{CERTIFICATIONS_DIR}/") for p in changed):
            for cert in read_certs(view):
                if cert_path(cert.id) in changed:
                    changed_tags.setdefault(cert.tag, cert_path(cert.id))
        if not changed_tags:
            return {}
        out: dict[str, set[str]] = {}
        for file in (view / "sources").glob(f"*/assets/*{SCHEMA_SUFFIX}"):
            try:
                data = load_schema(file.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            used = set(_tags_in(data)) & set(changed_tags)
            if used:
                rel = file.relative_to(view).as_posix()
                out[rel] = {changed_tags[t] for t in used}
        return out


def register() -> None:
    """Ajoute les règles de gouvernance aux listes du brouillon (idempotent)."""
    if not any(isinstance(r, GovernanceRule) for r in VALIDATION_RULES):
        VALIDATION_RULES.append(GovernanceRule())
    if not any(isinstance(r, GovernanceAssetsRule) for r in DEPENDENCY_RULES):
        DEPENDENCY_RULES.append(GovernanceAssetsRule())


register()

__all__ = ["CLASSIFICATIONS_DIR", "GovernanceAssetsRule", "GovernanceRule", "register"]
