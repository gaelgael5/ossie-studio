from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class ParsedObject:
    """Un objet lu par un parseur. `key` est son identité stable (elle nomme le
    fichier schéma de destination) ; `sources` liste les fichiers originaux qui
    le composent (un objet peut venir de plusieurs fichiers, p. ex. une classe
    partielle) ; `schema` est son JSON Schema, sortie **déterministe**."""

    key: str
    sources: list[str]
    schema: str


class ParserError(Exception):
    """Échec de lecture d'un fichier original, porté par un code stable
    (unreadable, syntax). La collecte le signale et continue les autres."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class Parser(Protocol):
    """Contrat commun à tous les parseurs (socle minimal ; l'epic « Parseurs et
    extracteurs » le formalisera). Un parseur annonce son identifiant et sa
    version, dit quels fichiers il prend en charge, et transforme un ensemble de
    fichiers originaux en objets JSON Schema."""

    id: str
    version: str

    def handles(self, path: str) -> bool:
        """Vrai si ce parseur prend en charge ce fichier (chemin relatif à la
        racine de la source)."""
        ...

    def parse(self, files: dict[str, bytes]) -> list[ParsedObject]:
        """Transforme les fichiers (chemin → contenu) en objets. Lève ParserError
        pour un fichier illisible."""
        ...
