"""Avancement d'une collecte, lu par l'écran pendant qu'elle tourne.

Écrit par la collecte (y compris depuis un thread : de simples affectations
d'attributs), lu par GET /collect. Les étapes : download (descente du code ou
extraction de la base), scan (référencement des fichiers dans la table de
travail : compteur), process (parseurs : barre, pondérée par le nombre de
fichiers de chaque parseur — un parseur traite ses fichiers en un seul lot),
resolve (références croisées), write (écriture dans le brouillon).
"""

from __future__ import annotations

from dataclasses import dataclass, field

STEPS = ("download", "scan", "process", "resolve", "write")


@dataclass
class CollectProgress:
    step: str = ""
    done: list[str] = field(default_factory=list)
    scanned: int = 0
    total: int = 0
    processed: int = 0
    current: str = ""

    def enter(self, step: str) -> None:
        if self.step and self.step not in self.done:
            self.done.append(self.step)
        self.step = step
        self.current = ""

    def finish(self) -> None:
        self.enter("")

    def payload(self) -> dict[str, object]:
        return {
            "step": self.step,
            "done": list(self.done),
            "scanned": self.scanned,
            "total": self.total,
            "processed": self.processed,
            "current": self.current,
        }
