"""Contrat commun à tous les plugins de parsing.

Un **plugin** transforme des fichiers d'une source en artefacts rangés dans la
copie de travail (`raw/` pour l'instant). Le socle garantit l'orchestration — il
construit la liste des fichiers, lance les plugins **en série**, applique leurs
actions avec un **écrivain unique**, et confie un seul commit local. Le plugin,
lui, **décide** (en consultant son propre index dans la destination et le crc du
fichier) mais **n'écrit jamais** : il rend une liste d'actions `{path, content}`.

Un plugin ne lit **jamais** la sortie d'un autre plugin. La résolution de
références croisées passera, plus tard, par un service de recherche commun offert
par le socle (réservé ici dans `RawAccess`), jamais par un couplage plugin↔plugin.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from typing import Protocol

# Cible de traitement d'une ligne de la table de travail et d'un plugin. Seul
# `raw` existe aujourd'hui ; le champ réserve d'autres types de traitement.
CIBLE_RAW = "raw"

# États de traitement d'une ligne de la table de travail.
STATUT_EN_ATTENTE = "en_attente"
STATUT_INCHANGE = "inchangé"
STATUT_TRAITE = "traité"
STATUT_ERREUR = "erreur"
STATUT_IGNORE = "ignoré"


@dataclass(frozen=True)
class ParsedObject:
    """Un objet produit par un parseur de schémas. `key` nomme le fichier schéma
    de destination ; `sources` liste les fichiers originaux qui le composent (un
    objet peut venir de plusieurs fichiers, p. ex. une classe partielle) ;
    `schema` est son JSON Schema, sortie **déterministe**."""

    key: str
    sources: list[str]
    schema: str


class ParserError(Exception):
    """Échec de lecture d'un fichier original, porté par un code stable
    (unreadable, syntax). La collecte le signale et continue les autres."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class PluginParam:
    """Un paramètre **global** exposé par un plugin, réglé dans la page de gestion
    des plugins et rangé en base. `type` : text | int | bool."""

    slug: str
    label: str
    doc: str
    type: str = "text"
    default: str = ""


@dataclass(frozen=True)
class FileRow:
    """Une ligne de la table de travail présentée à un plugin : le chemin relatif
    du fichier source et son empreinte (crc)."""

    path: str
    crc: str


@dataclass(frozen=True)
class OutputAction:
    """Une écriture demandée par un plugin : chemin relatif à la destination
    (`raw/`) et contenu texte. Le socle est seul à écrire."""

    path: str
    content: str


@dataclass(frozen=True)
class PluginError:
    """Un fichier source que le plugin n'a pas pu traiter."""

    source: str
    cause: str


@dataclass
class PluginResult:
    """Ce qu'un plugin rend au socle : des écritures, des suppressions (chemins
    relatifs à `raw/`), le statut par fichier source traité, les erreurs, et des
    compteurs pour le compte rendu."""

    writes: list[OutputAction] = field(default_factory=list)
    deletes: list[str] = field(default_factory=list)
    statuses: dict[str, str] = field(default_factory=dict)
    errors: list[PluginError] = field(default_factory=list)
    added: int = 0
    modified: int = 0
    deleted: int = 0
    unchanged: int = 0
    read: int = 0
    retained: int = 0


class RawAccess(Protocol):
    """Accès **lecture seule** d'un plugin à la destination (`raw/`) : son propre
    index et ses sorties déjà écrites. Le service de recherche de schéma commun
    (résolution de références croisées) viendra se greffer ici — un plugin ne lit
    jamais un autre plugin directement."""

    def read_index(self, name: str) -> str | None:
        """Lit un fichier d'index du plugin dans `raw/` (p. ex. `manifest.yaml`,
        `docs-index.yaml`), ou None s'il n'existe pas encore."""
        ...

    def read_output(self, path: str) -> str | None:
        """Lit une sortie déjà écrite dans `raw/` (chemin relatif), ou None."""
        ...

    def list_outputs(self) -> dict[str, str]:
        """Toutes les sorties présentes dans `raw/` (chemin relatif → contenu).
        Le plugin filtre celles qui le concernent (ses extensions, son dossier)."""
        ...


@dataclass
class PluginContext:
    """Tout ce qu'un plugin reçoit pour un passage : les fichiers source qui le
    concernent (déjà filtrés par cible et globs), leur contenu, un accès lecture
    seule à `raw/`, ses paramètres globaux résolus, le mode force et la branche."""

    files: list[FileRow]
    contents: dict[str, bytes]
    raw: RawAccess
    params: dict[str, str]
    force: bool
    branche: str


class Parser(Protocol):
    """Contrat de **transformation** utilisé par le moteur incrémental de schémas
    (`run_collection`) : à partir de fichiers originaux, produit des objets JSON
    Schema. Un plugin de schémas implémente ce contrat en plus de `Plugin`."""

    id: str
    version: str

    def handles(self, path: str) -> bool:
        """Vrai si ce parseur prend en charge ce fichier (chemin relatif)."""
        ...

    def parse(self, files: dict[str, bytes]) -> list[ParsedObject]:
        """Transforme les fichiers (chemin → contenu) en objets. Lève ParserError
        pour un fichier illisible."""
        ...


class Plugin(Protocol):
    """Contrat commun à tous les plugins de parsing. Un plugin annonce son
    identité, sa cible, ses motifs globing et ses paramètres, et transforme les
    fichiers qui le concernent en actions d'écriture."""

    id: str
    version: str
    label: str
    cible: str

    def globs(self) -> list[str]:
        """Motifs globing (relatifs à la racine de la source) que ce plugin
        prend en charge, p. ex. `["**/*.cs"]`."""
        ...

    def parameters(self) -> list[PluginParam]:
        """Paramètres globaux que ce plugin expose (vide s'il n'en a pas)."""
        ...

    def run(self, context: PluginContext) -> PluginResult:
        """Transforme les fichiers du contexte en actions. Décide seul quoi
        (re)traiter à partir de son index et du crc ; n'écrit rien."""
        ...


def matches_globs(globs: list[str], path: str) -> bool:
    """Vrai si l'un des motifs globing matche ce chemin. `**/` est traité comme
    « n'importe quelle profondeur » en complétant par un essai sur le nom de
    fichier seul."""
    name = path.rsplit("/", 1)[-1]
    for pattern in globs:
        if fnmatch.fnmatch(path, pattern):
            return True
        if pattern.startswith("**/") and fnmatch.fnmatch(name, pattern[3:]):
            return True
    return False
