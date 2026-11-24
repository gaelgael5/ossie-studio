"""Lire un fichier d'import (CSV ou JSON), en détecter les chemins, et essayer
un template JSLT dessus (epic « Référentiels », ticket « Onglet Imports »).

**Une ligne = une valeur** (Gael, 24 novembre) : chaque ligne d'un CSV, ou chaque
élément d'un fichier JSON qui est un tableau, passe dans le template, qui rend
une valeur du référentiel. Un fichier JSON qui est un objet compte pour une
ligne. Une erreur se rattache ainsi à la ligne du fichier (et, si le moteur la
donne, à la ligne du template).

L'essai ne modifie rien : il compte les valeurs nouvelles, modifiées,
inchangées et en erreur par rapport aux valeurs actuelles (comparées par la
clé), et liste les différences.
"""

from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass, field
from typing import Any, Final

from ossie_studio.referentiels.errors import Problem
from ossie_studio.referentiels.imports import ImportDeclaration
from ossie_studio.referentiels.jslt_engine import (
    EvaluationError,
    OutsideProfileError,
    TemplateSyntaxError,
    UnsupportedConstructError,
    compile_template,
    evaluate,
)
from ossie_studio.referentiels.model import SEPARATOR, model_key
from ossie_studio.referentiels.values import check_values, key_label, key_of

PREVIEW_ROWS = 20
MAX_PATHS = 200
MAX_DEPTH = 6
PATH_SAMPLE = 50
DIFF_MAX = 200
# Nœuds évalués au plus par ligne : le langage n'a pas de boucle, mais un
# document énorme ferait tourner un template sans fin visible (comme Miles).
BUDGET = 100_000
ROOT = "$"

# Modes d'import (fusion par défaut).
MERGE: Final = "merge"
REPLACE: Final = "replace"
MODES = (MERGE, REPLACE)


class ExampleError(Exception):
    """Le fichier d'exemple ne se lit pas avec ces réglages."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class Record:
    """Une ligne du fichier : sa donnée, et où elle est (« ligne 3 », « élément [2] »)."""

    data: Any
    where: str


@dataclass(frozen=True)
class Example:
    records: list[Record]
    columns: list[str] = field(default_factory=list)


def read_example(decl: ImportDeclaration, raw: bytes) -> Example:
    """Les lignes du fichier d'exemple, selon le format et les réglages de lecture."""
    if not raw:
        return Example([])
    if decl.format == "csv":
        try:
            text = raw.decode(decl.encoding)
        except (UnicodeDecodeError, LookupError) as exc:
            raise ExampleError("example_encoding", str(exc)) from None
        rows = list(csv.reader(io.StringIO(text), delimiter=decl.separator))
        if not rows:
            return Example([])
        if decl.header:
            columns = [c.strip() or f"col{i + 1}" for i, c in enumerate(rows[0])]
            body, first_line = rows[1:], 2
        else:
            width = max(len(r) for r in rows)
            columns = [f"col{i + 1}" for i in range(width)]
            body, first_line = rows, 1
        records = [
            Record(
                {c: (row[i] if i < len(row) else "") for i, c in enumerate(columns)},
                f"ligne {first_line + n}",
            )
            for n, row in enumerate(body)
            if any(cell.strip() for cell in row)
        ]
        return Example(records, columns)
    try:
        data = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise ExampleError("example_json", str(exc)) from None
    if isinstance(data, list):
        return Example([Record(item, f"élément [{i}]") for i, item in enumerate(data)])
    return Example([Record(data, "élément")])


def _kind(value: Any) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int | float):
        return "number"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, list):
        return "array"
    if value is None:
        return "null"
    return "string"


def _plain(name: str) -> bool:
    return bool(name) and all(c.isalnum() or c == "_" for c in name) and not name[0].isdigit()


def _walk(value: Any, path: str, found: dict[str, str], depth: int) -> None:
    if depth > MAX_DEPTH or len(found) >= MAX_PATHS:
        return
    if isinstance(value, dict):
        for key, sub in value.items():
            step = f"{path}.{key}" if _plain(str(key)) else f'{path}["{key}"]'
            found.setdefault(step, _kind(sub))
            _walk(sub, step, found, depth + 1)
    elif isinstance(value, list) and value:
        step = f"{path}[0]"
        found.setdefault(step, _kind(value[0]))
        _walk(value[0], step, found, depth + 1)


def detect_paths(example: Example) -> list[dict[str, str]]:
    """Les chemins vus dans les premières lignes, pour l'auto-complétion du template
    (même forme que Miles : chemin, genre, description)."""
    found: dict[str, str] = {}
    for record in example.records[:PATH_SAMPLE]:
        _walk(record.data, ROOT, found, 0)
    return [{"path": p, "kind": k, "description": ""} for p, k in list(found.items())[:MAX_PATHS]]


def preview(example: Example) -> dict[str, Any]:
    rows = [r.data for r in example.records[:PREVIEW_ROWS]]
    columns = example.columns or sorted({k for r in rows if isinstance(r, dict) for k in r})
    return {
        "columns": columns,
        "rows": rows,
        "count": len(example.records),
        "paths": detect_paths(example),
    }


# --- Essai du template -----------------------------------------------------------------------


@dataclass(frozen=True)
class TemplateProblem:
    """Le template ne s'analyse pas : message du moteur, et position s'il la donne."""

    code: str
    message: str
    line: int | None = None
    column: int | None = None

    def payload(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "line": self.line,
            "column": self.column,
        }


def check_template(source: str) -> list[TemplateProblem]:
    try:
        compile_template(source)
    except TemplateSyntaxError as exc:
        return [TemplateProblem("template_syntax", exc.message, exc.line, exc.column)]
    except OutsideProfileError as exc:
        return [
            TemplateProblem(
                "template_outside_profile",
                f"{v.construct}{f' : {v.detail}' if v.detail else ''}",
                v.line,
                v.column,
            )
            for v in exc.violations
        ]
    except UnsupportedConstructError as exc:
        return [TemplateProblem("template_unsupported", exc.message)]
    return []


def _changes(old: dict[str, Any], new: dict[str, Any]) -> list[dict[str, Any]]:
    names = sorted(set(old) | set(new))
    return [
        {"property": n, "before": old.get(n), "after": new.get(n)}
        for n in names
        if old.get(n) != new.get(n)
    ]


@dataclass(frozen=True)
class Transformed:
    """Le fichier passé dans le template, ligne à ligne, et validé contre le
    modèle (clé unique comprise parmi les valeurs produites). Ne dépend pas des
    valeurs actuelles : le calcul, coûteux, se fait hors de l'écriture."""

    template_problems: list[TemplateProblem]
    lines: int = 0
    produced: list[Any] = field(default_factory=list)
    origins: list[str] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)
    invalid: frozenset[int] = frozenset()

    @property
    def ok(self) -> bool:
        return not self.template_problems and not self.errors

    def valid(self) -> list[tuple[int, Any]]:
        return [(i, v) for i, v in enumerate(self.produced) if i not in self.invalid]


def transform(model: dict[str, Any], source: str, example: Example) -> Transformed:
    """Applique le template à chaque ligne du fichier et valide ce qu'il rend."""
    problems = check_template(source)
    if problems:
        return Transformed(problems)
    template = compile_template(source)
    produced: list[Any] = []
    origins: list[str] = []
    errors: list[dict[str, Any]] = []
    for record in example.records:
        try:
            value = evaluate(template, record.data, BUDGET)
        except EvaluationError as exc:
            errors.append(
                {
                    "where": record.where,
                    "code": f"template_{exc.code}",
                    "message": exc.message or "",
                    "path": exc.path,
                    "expected": exc.expected,
                    "field": "",
                }
            )
            continue
        produced.append(value)
        origins.append(record.where)
    value_problems: list[Problem] = check_values(model, produced)
    for p in value_problems:
        errors.append(
            {
                "where": origins[p.index] if 0 <= p.index < len(origins) else "",
                "code": p.code,
                "message": p.detail,
                "path": None,
                "expected": None,
                "field": p.field,
                "key": p.path.split(SEPARATOR)[0],
            }
        )
    return Transformed(
        [],
        len(example.records),
        produced,
        origins,
        errors,
        frozenset(p.index for p in value_problems),
    )


def report(
    model: dict[str, Any],
    current: list[dict[str, Any]],
    done: Transformed,
    mode: str = MERGE,
) -> dict[str, Any]:
    """Compte rendu par rapport aux valeurs actuelles (comparées par la clé) :
    nouvelles, modifiées, inchangées, supprimées (remplacement seulement), en
    erreur, et les différences."""
    if done.template_problems:
        return {"ok": False, "template_problems": [p.payload() for p in done.template_problems]}
    key = model_key(model)
    by_key = {key_of(v, key): v for v in current if key_of(v, key) is not None}
    new = modified = unchanged = 0
    differences: list[dict[str, Any]] = []
    for index, value in done.valid():
        label = key_label(value, key, index)
        before = by_key.get(key_of(value, key))
        if before is None:
            new += 1
            if len(differences) < DIFF_MAX:
                differences.append(
                    {"key": label, "kind": "new", "where": done.origins[index], "value": value}
                )
        elif before == value:
            unchanged += 1
        else:
            modified += 1
            if len(differences) < DIFF_MAX:
                differences.append(
                    {
                        "key": label,
                        "kind": "modified",
                        "where": done.origins[index],
                        "changes": _changes(before, value),
                    }
                )
    deleted = 0
    if mode == REPLACE:
        kept = {key_of(v, key) for _i, v in done.valid()}
        for index, before in enumerate(current):
            if key_of(before, key) in kept:
                continue
            deleted += 1
            if len(differences) < DIFF_MAX:
                differences.append(
                    {
                        "key": key_label(before, key, index),
                        "kind": "deleted",
                        "where": "",
                        "value": before,
                    }
                )
    return {
        "ok": done.ok,
        "template_problems": [],
        "lines": done.lines,
        "new": new,
        "modified": modified,
        "unchanged": unchanged,
        "deleted": deleted,
        "errors": len({e["where"] for e in done.errors}),
        "error_details": done.errors[:DIFF_MAX],
        "differences": differences,
    }


def run_template(
    model: dict[str, Any],
    current: list[dict[str, Any]],
    source: str,
    example: Example,
    mode: str = MERGE,
) -> dict[str, Any]:
    """L'essai : applique le template au fichier et compare aux valeurs
    actuelles. Rien n'est écrit."""
    return report(model, current, transform(model, source, example), mode)


def imported_values(
    model: dict[str, Any], current: list[dict[str, Any]], done: Transformed, mode: str
) -> list[dict[str, Any]] | None:
    """Les valeurs du référentiel après l'import, ou None s'il est refusé
    (**tout ou rien** : la moindre erreur bloque).

    - fusion (`merge`) : les valeurs du fichier remplacent celles de même clé,
      les nouvelles s'ajoutent, les autres restent ;
    - remplacement (`replace`) : exactement les valeurs du fichier."""
    if not done.ok:
        return None
    produced = [v for _i, v in done.valid()]
    if mode == REPLACE:
        return produced
    key = model_key(model)
    merged = list(current)
    position = {key_of(v, key): i for i, v in enumerate(current) if key_of(v, key) is not None}
    for value in produced:
        index = position.get(key_of(value, key))
        if index is None:
            merged.append(value)
        else:
            merged[index] = value
    return merged
