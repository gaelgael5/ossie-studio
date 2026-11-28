"""Les types communs du registre des types de document d'écriture (enabler 7b4b8f3a) : un refus,
un champ d'identité, l'emplacement d'un document, un document lu, ce qu'un
validateur sait faire (lire, écrire, supprimer — par le service de l'écran), et ce
que rend la liste d'un type de document (`list_documents`, ticket 2113e7bc)."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol, runtime_checkable


class DocumentError(Exception):
    """Un refus du registre ou d'un validateur : le code, le champ en cause (un nom
    d'identité, `path`, ou un JSONPath dans le contenu) et un message en français."""

    def __init__(self, code: str, field: str, message: str, **extra: Any) -> None:
        super().__init__(code)
        self.code = code
        self.field = field
        self.message = message
        self.extra = extra


@dataclass(frozen=True)
class IdentityField:
    name: str
    description: str  # en anglais, pour l'agent
    format: str  # la forme attendue, en clair


@dataclass(frozen=True)
class Location:
    """Où vit un document : le fichier de l'espace et, pour une valeur de
    référentiel, la valeur visée dans ce fichier (les valeurs de sa clé, dans l'ordre
    du modèle). `path` None : un document que le service nommera à sa création
    (certification nouvelle)."""

    document_type: str
    identity: dict[str, Any]
    path: str | None
    entry: tuple[Any, ...] | None = None


@dataclass(frozen=True)
class Document:
    """Un document lu : son contenu (objet pour le YAML/JSON, texte pour le XML), sa
    version (à rendre pour le remplacer, le modifier ou le supprimer) et son fichier."""

    content: Any
    version: str
    path: str


class Store(Protocol):
    """Le validateur d'un type de document : **le service de l'écran**, mêmes contrôles et mêmes
    refus nommés. Rien n'est validé ici qui ne le soit déjà par le service ; un
    validateur ne fait que traduire le document en appel du service."""

    def read(self, root: Path, location: Location) -> Document | None: ...

    def write(self, root: Path, location: Location, content: Any, *, create: bool) -> list[str]:
        """Écrit (création ou remplacement complet) ; rend les avertissements."""
        ...

    def delete(self, root: Path, location: Location) -> None: ...


@runtime_checkable
class CheckingStore(Store, Protocol):
    """Le validateur d'un type de document qu'un import alimente (ticket 1612cce4) : il sait aussi
    valider un lot sans rien écrire. Gael : « il faut que le validateur appelle la même
    méthode » — `write` passe par `check` pour son document, et le moteur d'import
    appelle `check` sur ses lignes : l'écran, l'agent et l'import valident par un seul
    chemin. `scope` : ce que le type de document lit de l'espace pour valider (la cartographie
    pour un objet, le modèle pour une valeur de référentiel)."""

    def check(
        self, scope: Any, entries: Sequence[tuple[Location, Any]]
    ) -> list[tuple[int, Exception]]:
        """Les refus du lot, chacun avec le rang de son document ; vide : tout est
        valide. Rien n'est écrit."""
        ...


# --- Lister les documents d'un type (list_documents) ---------------------------------------


@dataclass(frozen=True)
class ListFilter:
    """Un filtre de list_documents propre à un type de document, déclaré dans le registre."""

    name: str
    description: str  # en anglais, pour l'agent
    type: Literal["string", "object"] = "string"
    choices: tuple[str, ...] = ()


@dataclass
class Item:
    """Un document dans une liste : son identité, son nom, son fichier et les champs de
    synthèse que rendaient les lectures par domaine. `version` None : celle du
    fichier ; `related` : d'autres fichiers du document (non publié si l'un l'est) ;
    `changes_under` : un répertoire dont la liste compte les fichiers non publiés
    (`unpublished_changes`, comme le rendait `list_sources`)."""

    identity: dict[str, Any]
    name: str
    path: str
    summary: dict[str, Any] = field(default_factory=dict)
    text: str = ""  # cherché par `search`, en plus du nom et de l'identité
    version: str | None = None
    related: tuple[str, ...] = ()
    changes_under: str | None = None


@dataclass(frozen=True)
class ListQuery:
    """Les filtres d'une liste, déjà contrôlés : l'identité partielle et les filtres
    propres au type de document (le lister applique les seconds, et peut se servir de la
    première pour ne lire que le nécessaire)."""

    identity: Mapping[str, Any]
    filters: Mapping[str, Any]


Lister = Callable[[Path, ListQuery], list[Item]]
