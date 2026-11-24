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
from typing import Any

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


def run_template(
    model: dict[str, Any], current: list[dict[str, Any]], source: str, example: Example
) -> dict[str, Any]:
    """Applique le template à chaque ligne de l'exemple et compare aux valeurs
    actuelles. Rien n'est écrit."""
    problems = check_template(source)
    if problems:
        return {"ok": False, "template_problems": [p.payload() for p in problems]}
    template = compile_template(source)
    key = model_key(model)
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
    # Validées contre le modèle, clé unique comprise parmi les valeurs produites.
    value_problems: list[Problem] = check_values(model, produced)
    invalid = {p.index for p in value_problems}
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
    by_key = {key_of(v, key): v for v in current if key_of(v, key) is not None}
    new = modified = unchanged = 0
    differences: list[dict[str, Any]] = []
    for index, value in enumerate(produced):
        if index in invalid:
            continue
        parts = key_of(value, key)
        label = key_label(value, key, index)
        before = by_key.get(parts)
        if before is None:
            new += 1
            if len(differences) < DIFF_MAX:
                differences.append(
                    {"key": label, "kind": "new", "where": origins[index], "value": value}
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
                        "where": origins[index],
                        "changes": _changes(before, value),
                    }
                )
    return {
        "ok": not errors,
        "template_problems": [],
        "lines": len(example.records),
        "new": new,
        "modified": modified,
        "unchanged": unchanged,
        "errors": len({e["where"] for e in errors}),
        "error_details": errors[:DIFF_MAX],
        "differences": differences,
    }
