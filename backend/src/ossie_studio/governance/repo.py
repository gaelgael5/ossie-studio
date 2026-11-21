"""Gouvernance d'un espace, dans son dépôt Git (par branche).

Décision (feature « Gouvernance dans le Git de l'espace ») : le travail vit dans
Git ; la gouvernance aussi. **Un fichier par objet**, pour que deux personnes qui
touchent deux tags différents ne se gênent pas à la fusion, et que le lien
« objet de gouvernance → assets qui l'utilisent » soit précis :

    governance/tags/<id du tag>.yaml          (l'id porte la hiérarchie « / »)
    governance/certifications/<id>.yaml
    governance/classifications/<id du schéma>.yaml   (niveaux dedans)

Format : celui de l'export/import YAML existant (yaml_io). Ce module ne fait
que des lectures et écritures de fichiers sous une racine donnée : il s'utilise
dans les `read_fn` / `write_fn` du brouillon. Les contraintes que garantissait
PostgreSQL (unicités) sont devenues des règles de validation."""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml

from ossie_studio.governance.tags import is_valid_tag_id

GOVERNANCE_DIR = "governance"
TAGS_DIR = f"{GOVERNANCE_DIR}/tags"
CERTIFICATIONS_DIR = f"{GOVERNANCE_DIR}/certifications"
CLASSIFICATIONS_DIR = f"{GOVERNANCE_DIR}/classifications"


@dataclass
class Tag:
    id: str
    owner: str = ""
    description: str = ""
    links: dict[str, str] = field(default_factory=dict)
    properties: dict[str, object] = field(default_factory=dict)


@dataclass
class Cert:
    id: str
    name: str
    rank: int
    description: str = ""
    criteria: str = ""
    tag: str = ""
    icon: str = ""
    restricted: bool = False
    slug: str = ""


@dataclass
class Level:
    id: str
    scheme_id: str
    name: str
    label: str = ""
    short_name: str = ""
    rank: int = 0
    description: str = ""
    criteria: str = ""
    color: str = ""
    icon: str = ""
    position: int = 0


@dataclass
class Scheme:
    id: str
    name: str
    description: str = ""
    badge_prefix: str = ""
    multi_value: bool = False
    is_primary: bool = False
    display_order: int = 0
    levels: list[Level] = field(default_factory=list)


def new_id() -> str:
    return str(uuid.uuid4())


def _dump(data: Any) -> str:
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True, default_flow_style=False)


def _load(file: Path) -> dict[str, Any]:
    data = yaml.safe_load(file.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def _files(root: Path, folder: str) -> list[Path]:
    base = root / folder
    return sorted(base.rglob("*.yaml")) if base.is_dir() else []


def _write(root: Path, rel: str, data: Any) -> None:
    target = root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(_dump(data), encoding="utf-8")


def _remove(root: Path, rel: str) -> bool:
    target = root / rel
    if not target.is_file():
        return False
    target.unlink()
    return True


# --- Tags ----------------------------------------------------------------------


class InvalidGovernanceId(ValueError):
    """Identifiant qui ne peut pas devenir un nom de fichier sûr."""


def tag_path(tag_id: str) -> str:
    # Garde de sécurité : l'id porte la hiérarchie par « / » ; seul un id valide
    # (segments [a-z0-9_-], jamais « .. ») devient un chemin sous governance/tags.
    if not is_valid_tag_id(tag_id):
        raise InvalidGovernanceId(tag_id)
    return f"{TAGS_DIR}/{tag_id}.yaml"


def read_tags(root: Path) -> list[Tag]:
    tags: list[Tag] = []
    for file in _files(root, TAGS_DIR):
        try:
            data = _load(file)
        except (OSError, yaml.YAMLError):
            continue  # signalé par la validation (fichier illisible)
        info = data.get("info") if isinstance(data.get("info"), dict) else {}
        assert isinstance(info, dict)
        tags.append(
            Tag(
                id=str(data.get("id") or file.relative_to(root / TAGS_DIR).as_posix()[:-5]),
                owner=str(info.get("owner", "") or ""),
                description=str(info.get("description", "") or ""),
                links=dict(data.get("links") or {}),
                properties=dict(data.get("properties") or {}),
            )
        )
    return sorted(tags, key=lambda t: t.id)


def write_tag(root: Path, tag: Tag) -> None:
    doc: dict[str, Any] = {
        "id": tag.id,
        "info": {"owner": tag.owner, "description": tag.description},
    }
    if tag.links:
        doc["links"] = dict(tag.links)
    if tag.properties:
        doc["properties"] = dict(tag.properties)
    _write(root, tag_path(tag.id), doc)


def delete_tag(root: Path, tag_id: str) -> bool:
    return _remove(root, tag_path(tag_id))


# --- Certifications --------------------------------------------------------------


def _safe_name(value: str) -> str:
    if not value or "/" in value or "\\" in value or value.startswith("."):
        raise InvalidGovernanceId(value)
    return value


def cert_path(cert_id: str) -> str:
    return f"{CERTIFICATIONS_DIR}/{_safe_name(cert_id)}.yaml"


def read_certs(root: Path) -> list[Cert]:
    certs: list[Cert] = []
    for file in _files(root, CERTIFICATIONS_DIR):
        try:
            data = _load(file)
        except (OSError, yaml.YAMLError):
            continue
        rank = data.get("rank", 1)
        certs.append(
            Cert(
                id=str(data.get("id") or file.stem),
                name=str(data.get("name", "") or ""),
                rank=rank if isinstance(rank, int) and not isinstance(rank, bool) else 1,
                description=str(data.get("description", "") or ""),
                criteria=str(data.get("criteria", "") or ""),
                tag=str(data.get("tag", "") or ""),
                icon=str(data.get("icon", "") or ""),
                restricted=bool(data.get("restricted", False)),
                slug=str(data.get("slug", "") or ""),
            )
        )
    return sorted(certs, key=lambda c: (c.rank, c.name))


def write_cert(root: Path, cert: Cert) -> None:
    _write(root, cert_path(cert.id), asdict(cert))


def delete_cert(root: Path, cert_id: str) -> bool:
    return _remove(root, cert_path(cert_id))


# --- Classifications ---------------------------------------------------------------


def scheme_path(scheme_id: str) -> str:
    return f"{CLASSIFICATIONS_DIR}/{_safe_name(scheme_id)}.yaml"


def _level(scheme_id: str, item: dict[str, Any], position: int) -> Level:
    rank = item.get("rank", 0)
    return Level(
        id=str(item.get("id") or new_id()),
        scheme_id=scheme_id,
        name=str(item.get("name", "") or ""),
        label=str(item.get("label", "") or item.get("name", "") or ""),
        short_name=str(item.get("short_name", "") or ""),
        rank=rank if isinstance(rank, int) and not isinstance(rank, bool) else 0,
        description=str(item.get("description", "") or ""),
        criteria=str(item.get("criteria", "") or ""),
        color=str(item.get("color", "") or ""),
        icon=str(item.get("icon", "") or ""),
        position=position,
    )


def read_schemes(root: Path) -> list[Scheme]:
    schemes: list[Scheme] = []
    for file in _files(root, CLASSIFICATIONS_DIR):
        try:
            data = _load(file)
        except (OSError, yaml.YAMLError):
            continue
        scheme_id = str(data.get("id") or file.stem)
        order = data.get("display_order", 0)
        schemes.append(
            Scheme(
                id=scheme_id,
                name=str(data.get("name", "") or ""),
                description=str(data.get("description", "") or ""),
                badge_prefix=str(data.get("badge_prefix", "") or ""),
                multi_value=bool(data.get("multi_value", False)),
                is_primary=bool(data.get("is_primary", False)),
                display_order=order if isinstance(order, int) else 0,
                levels=[
                    _level(scheme_id, item, i)
                    for i, item in enumerate(data.get("levels") or [])
                    if isinstance(item, dict)
                ],
            )
        )
    return sorted(schemes, key=lambda s: (s.display_order, s.name))


def write_scheme(root: Path, scheme: Scheme) -> None:
    doc = {
        "id": scheme.id,
        "name": scheme.name,
        "description": scheme.description,
        "badge_prefix": scheme.badge_prefix,
        "multi_value": scheme.multi_value,
        "is_primary": scheme.is_primary,
        "display_order": scheme.display_order,
        "levels": [
            {k: v for k, v in asdict(lv).items() if k not in ("scheme_id", "position")}
            for lv in scheme.levels
        ],
    }
    _write(root, scheme_path(scheme.id), doc)


def delete_scheme(root: Path, scheme_id: str) -> bool:
    return _remove(root, scheme_path(scheme_id))
