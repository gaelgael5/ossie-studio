"""Points d'entrée Kotlin / Java (epic « Cartographie applicative dans raw », ticket
e34e5c29) : fonctions pures sur les annotations lues par le parseur `kotlin-java`.

- **HTTP** : méthodes d'une classe `@RestController` / `@Controller` portant
  `@GetMapping`, `@PostMapping`, `@PutMapping`, `@PatchMapping`, `@DeleteMapping`,
  ou `@RequestMapping(method = …)` ; route = `@RequestMapping` de la classe +
  chemin de la méthode. Un chemin qui n'est pas un texte littéral (constante,
  `${…}`) donne `route: null`. Un `@RequestMapping` de méthode sans `method` n'est
  pas retenu (le format exige un verbe). Un client Feign (interface sans
  `@RestController`) n'est pas un point d'entrée.
- **Tâche** : `@Scheduled`.
- **Message** : `@KafkaListener` (`topics`, `topicPattern`), `@RabbitListener`
  (`queues`) ; le sujet est gardé tel qu'écrit quand c'est un texte.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

MAPPINGS = {
    "GetMapping": "GET",
    "PostMapping": "POST",
    "PutMapping": "PUT",
    "PatchMapping": "PATCH",
    "DeleteMapping": "DELETE",
}
CONTROLLERS = frozenset({"RestController", "Controller"})
TASKS = frozenset({"Scheduled"})
LISTENERS = {"KafkaListener": ("topics", "topicPattern", "value"), "RabbitListener": ("queues",)}
_LITERAL = re.compile(r'"((?:[^"\\]|\\.)*)"')
_VERB = re.compile(r"\b(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\b")


@dataclass(frozen=True)
class Annotation:
    """Une annotation et ses arguments **tels qu'écrits** (guillemets compris)."""

    name: str
    named: dict[str, str] = field(default_factory=dict)
    positional: tuple[str, ...] = ()

    def argument(self, *keys: str) -> str | None:
        """Le premier argument positionnel, sinon le premier argument nommé trouvé."""
        if self.positional:
            return self.positional[0]
        return next((self.named[k] for k in keys if k in self.named), None)


def literal(raw: str | None) -> str | None:
    """Le texte d'un argument s'il est littéral : `"/x"`, ou le premier élément de
    `{"/x", "/y"}` / `["/x"]`. None pour une constante ou une concaténation."""
    if raw is None:
        return None
    text = raw.strip()
    if text[:1] in "{[":
        text = text[1:-1].strip() if text[-1:] in "}]" else text[1:].strip()
        text = text.split(",", 1)[0].strip()
    match = _LITERAL.fullmatch(text)
    return match.group(1) if match else None


def _path(annotation: Annotation | None) -> str | None:
    """Le chemin d'un mapping : "" s'il n'en donne pas, None s'il n'est pas littéral."""
    if annotation is None:
        return ""
    raw = annotation.argument("value", "path")
    if raw is None:
        return ""
    value = literal(raw)
    return None if value is None or "${" in value else value


def join_routes(base: str | None, path: str | None) -> str | None:
    if base is None or path is None:
        return None
    parts = [p.strip("/") for p in (base, path) if p.strip("/")]
    return "/" + "/".join(parts)


def entry_point(method: list[Annotation], owner: list[Annotation]) -> dict[str, Any] | None:
    """Le point d'entrée d'une méthode (`method` : ses annotations ; `owner` :
    celles de sa classe), None si elle n'en est pas un."""
    for annotation in method:
        if annotation.name in TASKS:
            return {"kind": "task"}
        if annotation.name in LISTENERS:
            topic = literal(annotation.argument(*LISTENERS[annotation.name]))
            return {"kind": "message", **({"topic": topic} if topic else {})}
    if not {a.name for a in owner} & CONTROLLERS:
        return None
    base = _path(next((a for a in owner if a.name == "RequestMapping"), None))
    for annotation in method:
        if annotation.name in MAPPINGS:
            verb: str | None = MAPPINGS[annotation.name]
        elif annotation.name == "RequestMapping":
            found = _VERB.search(annotation.named.get("method", ""))
            verb = found.group(1) if found else None
        else:
            continue
        if verb is None:
            continue
        return {"kind": "http", "verb": verb, "route": join_routes(base, _path(annotation))}
    return None
