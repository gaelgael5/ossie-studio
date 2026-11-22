from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import yaml

from ossie_studio.sources.paths import is_valid_source_slug
from ossie_studio.sources.tri import validate_tri_rules

# Version du format de manifeste. Une évolution incompatible l'incrémente ;
# chaque version sait se relire. Le champ `version` du fichier fait foi.
# Version 2 : clés en anglais (la version 1 avait des clés françaises). On écrit
# toujours la version courante ; une version 1 est convertie à la lecture pour
# qu'un dépôt existant reste lisible sans migration.
MANIFEST_VERSION = 2
LEGACY_MANIFEST_VERSION = 1

GIT_TYPE = "git"
# Les types « base » partagent la même forme de paramètres ; le type vaut le
# moteur de la connexion choisie.
DB_TYPES = ("postgres", "sqlserver", "snowflake")
# Source alimentée par **dépôt de fichiers** (contrats OpenAPI) : pas de connexion.
OPENAPI_TYPE = "openapi"
SOURCE_TYPES = (GIT_TYPE, *DB_TYPES, OPENAPI_TYPE)

# Clés autorisées, par type. Toute autre clé est refusée (manifeste strict).
_GIT_KEYS = {"repository", "branch", "directories", "parsers", "exclusions"}
_DB_KEYS = {"database", "schemas", "exclusions"}
_TOP_KEYS = {"version", "slug", "label", "type", "connection", "parameters", "triage"}

# Correspondance version 1 (française) → version 2 (anglaise). Seules les clés
# renommées y figurent ; une clé absente de la table est reprise telle quelle,
# et la validation stricte de la version 2 refuse ensuite toute clé inconnue.
_LEGACY_TOP_KEYS = {"connexion": "connection", "parametres": "parameters", "tri": "triage"}
_LEGACY_PARAM_KEYS = {
    "depot": "repository",
    "branche": "branch",
    "repertoires": "directories",
    "parseurs": "parsers",
    "base": "database",
}


class ManifestError(Exception):
    """Manifeste invalide. Porte la liste des champs fautifs, pour le contrat
    d'erreur de l'API."""

    def __init__(self, fields: list[str]) -> None:
        super().__init__(", ".join(fields))
        self.fields = fields


@dataclass(frozen=True)
class SourceManifest:
    slug: str
    label: str
    type: str
    connection: str
    parameters: dict[str, Any]
    # Règles de tri (écarter le bruit) ; section dédiée versionnée avec la source.
    triage: list[dict[str, Any]] = field(default_factory=list)

    def to_mapping(self) -> dict[str, Any]:
        mapping: dict[str, Any] = {
            "version": MANIFEST_VERSION,
            "slug": self.slug,
            "label": self.label,
            "type": self.type,
            "connection": self.connection,
            "parameters": self.parameters,
        }
        if self.triage:  # omise quand vide : un manifeste sans règle reste inchangé
            mapping["triage"] = self.triage
        return mapping


def _str_list(value: Any) -> list[str] | None:
    if not isinstance(value, list) or any(not isinstance(v, str) for v in value):
        return None
    return list(value)


def _validate(data: dict[str, Any]) -> list[str]:
    """Rend la liste des champs fautifs d'un manifeste (vide = valide)."""
    errors: list[str] = []
    unknown = set(data) - _TOP_KEYS
    errors += [f"unknown_key:{k}" for k in sorted(unknown)]

    if data.get("version") != MANIFEST_VERSION:
        errors.append("version_unsupported")

    slug = data.get("slug")
    if not isinstance(slug, str) or not is_valid_source_slug(slug):
        errors.append("slug_invalid")

    if not isinstance(data.get("label"), str) or not str(data.get("label")).strip():
        errors.append("label_required")

    source_type = data.get("type")
    if source_type not in SOURCE_TYPES:
        errors.append("type_unknown")

    connection = data.get("connection")
    if source_type == OPENAPI_TYPE:
        # Source par dépôt : pas de connexion.
        if isinstance(connection, str) and connection.strip():
            errors.append("connection_forbidden")
    elif not isinstance(connection, str) or not connection.strip():
        errors.append("connection_required")

    params = data.get("parameters")
    if not isinstance(params, dict):
        errors.append("parameters_invalid")
    elif source_type == GIT_TYPE:
        errors += _validate_git_params(params)
    elif source_type in DB_TYPES:
        errors += _validate_db_params(params)
    elif source_type == OPENAPI_TYPE and params:
        # Aucun paramètre : les contrats vivent dans contrats/, pas dans le manifeste.
        errors.append("parameters_forbidden")

    errors += validate_tri_rules(data.get("triage"))
    return errors


def _validate_git_params(params: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    errors += [f"unknown_key:parameters.{k}" for k in sorted(set(params) - _GIT_KEYS)]
    if not isinstance(params.get("repository"), str) or not str(params.get("repository")).strip():
        errors.append("repository_required")
    if not isinstance(params.get("branch"), str) or not str(params.get("branch")).strip():
        errors.append("branch_required")
    if "directories" in params and _str_list(params["directories"]) is None:
        errors.append("directories_invalid")
    # Une source Git doit choisir au moins un parseur (socle des parseurs).
    parsers = params.get("parsers")
    if "parsers" not in params:
        errors.append("parsers_required")
    elif _str_list(parsers) is None:
        errors.append("parsers_invalid")
    elif not parsers:
        errors.append("parsers_required")
    if "exclusions" in params and _str_list(params["exclusions"]) is None:
        errors.append("exclusions_invalid")
    return errors


def _validate_db_params(params: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    errors += [f"unknown_key:parameters.{k}" for k in sorted(set(params) - _DB_KEYS)]
    if not isinstance(params.get("database"), str) or not str(params.get("database")).strip():
        errors.append("database_required")
    for key in ("schemas", "exclusions"):
        if key in params and _str_list(params[key]) is None:
            errors.append(f"{key}_invalid")
    return errors


def _upgrade_legacy(data: dict[str, Any]) -> dict[str, Any]:
    """Convertit un manifeste version 1 (clés françaises) en version 2. Une clé
    déjà anglaise dans une version 1 n'est pas devinée : elle reste et sera
    refusée par la validation, comme toute clé inconnue."""
    upgraded = {_LEGACY_TOP_KEYS.get(k, k): v for k, v in data.items()}
    upgraded["version"] = MANIFEST_VERSION
    params = upgraded.get("parameters")
    if isinstance(params, dict):
        upgraded["parameters"] = {_LEGACY_PARAM_KEYS.get(k, k): v for k, v in params.items()}
    return upgraded


def manifest_from_mapping(data: dict[str, Any]) -> SourceManifest:
    """Valide puis construit un manifeste depuis un mapping (lu d'un fichier ou
    reçu de l'API). Lève ManifestError avec les champs fautifs."""
    if data.get("version") == LEGACY_MANIFEST_VERSION:
        data = _upgrade_legacy(data)
    errors = _validate(data)
    if errors:
        raise ManifestError(errors)
    return SourceManifest(
        slug=str(data["slug"]),
        label=str(data["label"]).strip(),
        type=str(data["type"]),
        connection=str(data.get("connection") or ""),
        parameters=dict(data["parameters"]),
        triage=list(data.get("triage") or []),
    )


def parse_manifest(text: str) -> SourceManifest:
    """Parse le YAML d'un manifeste et le valide. YAML illisible → ManifestError."""
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError:
        raise ManifestError(["yaml_invalid"]) from None
    if not isinstance(data, dict):
        raise ManifestError(["yaml_invalid"])
    return manifest_from_mapping(data)


def dump_manifest(manifest: SourceManifest) -> str:
    """Sérialise un manifeste en YAML déterministe (clés dans l'ordre du format,
    lisible dans une revue de diff)."""
    return yaml.safe_dump(
        manifest.to_mapping(), sort_keys=False, allow_unicode=True, default_flow_style=False
    )
