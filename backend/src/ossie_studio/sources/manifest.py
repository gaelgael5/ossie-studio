from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import yaml

from ossie_studio.sources.paths import is_valid_source_slug

# Version du format de manifeste. Une évolution incompatible l'incrémente ;
# chaque version sait se relire. Le champ `version` du fichier fait foi.
MANIFEST_VERSION = 1

GIT_TYPE = "git"
# Les types « base » partagent la même forme de paramètres ; le type vaut le
# moteur de la connexion choisie (décision : « juste SQL ou Git » pour l'instant,
# pas de contrats OpenAPI ni de sélection de parseurs).
DB_TYPES = ("postgres", "sqlserver", "snowflake")
SOURCE_TYPES = (GIT_TYPE, *DB_TYPES)

# Clés autorisées, par type. Toute autre clé est refusée (manifeste strict).
_GIT_KEYS = {"depot", "branche", "repertoires"}
_DB_KEYS = {"base", "schemas", "exclusions"}
_TOP_KEYS = {"version", "slug", "label", "type", "connexion", "parametres"}


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
    connexion: str
    parametres: dict[str, Any]

    def to_mapping(self) -> dict[str, Any]:
        return {
            "version": MANIFEST_VERSION,
            "slug": self.slug,
            "label": self.label,
            "type": self.type,
            "connexion": self.connexion,
            "parametres": self.parametres,
        }


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

    if not isinstance(data.get("connexion"), str) or not str(data.get("connexion")).strip():
        errors.append("connexion_required")

    params = data.get("parametres")
    if not isinstance(params, dict):
        errors.append("parametres_invalid")
    elif source_type == GIT_TYPE:
        errors += _validate_git_params(params)
    elif source_type in DB_TYPES:
        errors += _validate_db_params(params)
    return errors


def _validate_git_params(params: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    errors += [f"unknown_key:parametres.{k}" for k in sorted(set(params) - _GIT_KEYS)]
    if not isinstance(params.get("depot"), str) or not str(params.get("depot")).strip():
        errors.append("depot_required")
    if not isinstance(params.get("branche"), str) or not str(params.get("branche")).strip():
        errors.append("branche_required")
    if "repertoires" in params and _str_list(params["repertoires"]) is None:
        errors.append("repertoires_invalid")
    return errors


def _validate_db_params(params: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    errors += [f"unknown_key:parametres.{k}" for k in sorted(set(params) - _DB_KEYS)]
    if not isinstance(params.get("base"), str) or not str(params.get("base")).strip():
        errors.append("base_required")
    for key in ("schemas", "exclusions"):
        if key in params and _str_list(params[key]) is None:
            errors.append(f"{key}_invalid")
    return errors


def manifest_from_mapping(data: dict[str, Any]) -> SourceManifest:
    """Valide puis construit un manifeste depuis un mapping (lu d'un fichier ou
    reçu de l'API). Lève ManifestError avec les champs fautifs."""
    errors = _validate(data)
    if errors:
        raise ManifestError(errors)
    return SourceManifest(
        slug=str(data["slug"]),
        label=str(data["label"]).strip(),
        type=str(data["type"]),
        connexion=str(data["connexion"]),
        parametres=dict(data["parametres"]),
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
