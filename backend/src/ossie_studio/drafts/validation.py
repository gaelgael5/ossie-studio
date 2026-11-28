"""Tests de validation du travail d'un espace, et leur journal.

Décision (feature « Tests de validation et journal ») : des règles tournent sur
la vue fusionnée (branche publiée + brouillon) et produisent des entrées de
niveau **erreur** ou **avertissement**. Une erreur liée au brouillon bloque la
publication ; une erreur **préexistante** (déjà là côté publié) ne bloque pas,
sinon une erreur ancienne bloquerait tout le monde. Un avertissement peut être
accepté (la décision part dans le message du commit). Le journal se recalcule
depuis le contenu : il n'est pas stocké dans Git. Liste de règles extensible :
la gouvernance dans Git y ajoute ses contraintes d'unicité."""

from __future__ import annotations

import hashlib
import json
import posixpath
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Protocol

import yaml

from ossie_studio.drafts.impacts import refs_in
from ossie_studio.parsers.conventions import SCHEMA_SUFFIX, load_schema
from ossie_studio.sources.manifest import ManifestError, parse_manifest

# Analyseur YAML en C (libyaml) quand l'image l'a : relire ~5 700 fichiers prend 3 s
# au lieu de 25 s en Python pur, et la passe de validation tient le verrou de la vue
# d'autant (bug c6a4ab74). Mêmes fichiers acceptés ou refusés.
_YAML_LOADER = getattr(yaml, "CSafeLoader", yaml.SafeLoader)

Level = Literal["error", "warning"]


@dataclass(frozen=True)
class Finding:
    level: Level
    path: str
    rule: str
    message_code: str
    args: dict[str, str] = field(default_factory=dict)
    fingerprint: str = ""

    @property
    def key(self) -> str:
        return f"{self.rule}:{self.path}:{self.message_code}"


class ValidationRule(Protocol):
    name: str

    def check(self, view: Path) -> list[Finding]: ...


def _digest(file: Path) -> str:
    try:
        return hashlib.sha256(file.read_bytes()).hexdigest()
    except OSError:
        return ""


# Dossiers du travail versionné. Domaines, cartographie et référentiels n'y étaient
# pas : un `valeurs.json` ou un `.extend.yaml` de domaine illisible passait la
# publication sans un mot (ticket 661efd1c).
_WORK_FOLDERS = ("sources", "governance", "domaines", "cartographie", "referentiels")


def _work_files(view: Path) -> Iterator[Path]:
    for folder in _WORK_FOLDERS:
        root = view / folder
        if root.is_dir():
            yield from (p for p in root.rglob("*") if p.is_file())


class ParseableRule:
    """Tout fichier YAML ou JSON du travail se lit."""

    name = "parseable"

    def check(self, view: Path) -> list[Finding]:
        out: list[Finding] = []
        for file in _work_files(view):
            if file.suffix not in (".json", ".yaml", ".yml"):
                continue
            if ".exemple." in file.name:
                # Fichier d'exemple d'un import (`<slug>.exemple.json`…) : donnée
                # brute déposée par l'utilisateur, pas un document de l'application ;
                # il peut ne pas être du JSON (test des référentiels).
                continue
            try:
                text = file.read_text(encoding="utf-8")
                if file.suffix == ".json":
                    json.loads(text)
                else:
                    yaml.load(text, Loader=_YAML_LOADER)  # noqa: S506 — chargeur sûr
            except (OSError, UnicodeDecodeError, ValueError, yaml.YAMLError):
                rel = file.relative_to(view).as_posix()
                out.append(Finding("error", rel, self.name, "file_unreadable", {}, _digest(file)))
        return out


class SourceManifestRule:
    """Un manifeste de source respecte sa validation."""

    name = "source_manifest"

    def check(self, view: Path) -> list[Finding]:
        out: list[Finding] = []
        for file in (view / "sources").glob("*/manifest.yaml"):
            try:
                parse_manifest(file.read_text(encoding="utf-8"))
            except ManifestError as exc:
                rel = file.relative_to(view).as_posix()
                if exc.fields == ["yaml_invalid"]:
                    continue  # déjà signalé par ParseableRule
                out.append(
                    Finding(
                        "error",
                        rel,
                        self.name,
                        "manifest_invalid",
                        {"fields": ", ".join(exc.fields)},
                        _digest(file),
                    )
                )
        return out


class BrokenRefRule:
    """Un `$ref` désigne un schéma qui existe."""

    name = "broken_ref"

    def check(self, view: Path) -> list[Finding]:
        out: list[Finding] = []
        for file in [
            *(view / "sources").glob(f"*/raw/*{SCHEMA_SUFFIX}"),
            *(view / "sources").glob(f"*/assets/*{SCHEMA_SUFFIX}"),
        ]:
            rel = file.relative_to(view).as_posix()
            try:
                data = load_schema(file.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            for ref in sorted(set(refs_in(data))):
                if ref.startswith("#") or "://" in ref:
                    continue
                target = posixpath.normpath(
                    posixpath.join(posixpath.dirname(rel), ref.split("#")[0])
                )
                if not (view / target).is_file():
                    out.append(
                        Finding(
                            "error", rel, self.name, "ref_broken", {"target": target}, _digest(file)
                        )
                    )
        return out


class OrphanAssetRule:
    """Un asset curé sans son raw (le brut a disparu) : à regarder."""

    name = "orphan_asset"

    def check(self, view: Path) -> list[Finding]:
        out: list[Finding] = []
        for file in (view / "sources").glob(f"*/assets/*{SCHEMA_SUFFIX}"):
            raw = file.parent.parent / "raw" / file.name
            if not raw.is_file():
                rel = file.relative_to(view).as_posix()
                out.append(Finding("warning", rel, self.name, "asset_orphan", {}, _digest(file)))
        return out


# Liste extensible : un domaine ajoute ses règles en s'enregistrant (la
# gouvernance : governance/rules.py).
VALIDATION_RULES: list[ValidationRule] = [
    ParseableRule(),
    SourceManifestRule(),
    BrokenRefRule(),
    OrphanAssetRule(),
]


def run_rules(view: Path, rules: list[ValidationRule] | None = None) -> list[Finding]:
    findings: list[Finding] = []
    for rule in rules if rules is not None else VALIDATION_RULES:
        findings.extend(rule.check(view))
    return sorted(findings, key=lambda f: (f.level != "error", f.path, f.rule))


@dataclass(frozen=True)
class LoggedFinding:
    finding: Finding
    preexisting: bool
    accepted: bool

    @property
    def blocking(self) -> bool:
        return self.finding.level == "error" and not self.preexisting

    def payload(self) -> dict[str, object]:
        f = self.finding
        return {
            "level": f.level,
            "path": f.path,
            "rule": f.rule,
            "message_code": f.message_code,
            "args": f.args,
            "preexisting": self.preexisting,
            "accepted": self.accepted,
            "blocking": self.blocking,
        }


def validation_log(
    merged: list[Finding], published: list[Finding], accepted: dict[str, str]
) -> list[LoggedFinding]:
    """Journal du brouillon : chaque entrée de la vue fusionnée, marquée
    préexistante si la même (règle, objet, message) existe côté publié, et
    acceptée si l'avertissement a été accepté sur ce même contenu."""
    before = {f.key for f in published}
    return [
        LoggedFinding(
            finding=f,
            preexisting=f.key in before,
            accepted=f.level == "warning" and accepted.get(f.key) == f.fingerprint,
        )
        for f in merged
    ]


def blocking(log: list[LoggedFinding]) -> list[LoggedFinding]:
    return [entry for entry in log if entry.blocking]
