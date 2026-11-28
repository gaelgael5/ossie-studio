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

**Fusion seulement, et stratégie de suppression** (ticket 6d36073b) : un import
corrige les éléments existants qui diffèrent et ajoute ceux qui manquent ; les
éléments absents du fichier sont supprimés ou gardés selon la stratégie de
l'import (`declaration.KEEP`, `CREATED`, `ALL`, voir `deletion_plan`). Un élément à
supprimer mais **encore utilisé** dans l'état final (la cible le dit, `in_use`)
est gardé, et le reste de l'import s'applique.
"""

from __future__ import annotations

import csv
import io
import json
from collections.abc import Hashable, Set
from dataclasses import dataclass, field
from datetime import date, datetime, time
from typing import Any

import openpyxl

from ossie_studio.imports.declaration import ALL, CREATED, KEEP, ImportDeclaration
from ossie_studio.imports.jslt_engine import (
    EvaluationError,
    TemplateSyntaxError,
    UnknownFunctionError,
    UnsupportedConstructError,
    compile_template,
    evaluate,
)
from ossie_studio.imports.target import ImportTarget, ItemProblem

PREVIEW_ROWS = 20
MAX_PATHS = 200
MAX_DEPTH = 6
PATH_SAMPLE = 50
DIFF_MAX = 200
# Nœuds évalués au plus par ligne : le langage n'a pas de boucle, mais un
# document énorme ferait tourner un template sans fin visible (comme Miles).
BUDGET = 100_000
ROOT = "$"

# Pourquoi un élément absent du fichier est gardé (ticket 6d36073b).
KEPT_STRATEGY = "strategy_keep"
KEPT_NOT_CREATED = "not_created"
KEPT_IN_USE = "in_use"


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


def sheet_names(raw: bytes) -> list[str]:
    """Les feuilles d'un classeur xlsx ; ExampleError si le fichier ne se lit pas."""
    try:
        book = openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
    except Exception as exc:  # zip, XML ou format invalides : tous refusés pareil
        raise ExampleError("example_xlsx", str(exc)) from None
    try:
        return list(book.sheetnames)
    finally:
        book.close()


def _cell(value: Any) -> Any:
    """Une cellule Excel en valeur JSON : nombres et booléens tels quels, dates en
    texte ISO, vide = chaîne vide (comme une cellule CSV vide)."""
    if value is None:
        return ""
    if isinstance(value, datetime | date | time):
        return value.isoformat()
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def _rows_to_example(rows: list[list[Any]], header: bool) -> Example:
    """Lignes (cellules) → lignes nommées, communes au CSV et au xlsx."""
    if not rows:
        return Example([])
    if header:
        columns = [str(c).strip() or f"col{i + 1}" for i, c in enumerate(rows[0])]
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
        if any(str(cell).strip() for cell in row)
    ]
    return Example(records, columns)


def _read_xlsx(decl: ImportDeclaration, raw: bytes) -> Example:
    """Une feuille d'un classeur xlsx (ticket 6f6066e9) : la feuille déclarée, ou la
    seule ; plusieurs feuilles sans choix → refus nommé qui les liste."""
    sheets = sheet_names(raw)
    if decl.sheet and decl.sheet in sheets:
        name = decl.sheet
    elif len(sheets) == 1:
        name = sheets[0]
    else:
        raise ExampleError("example_sheet_required", ", ".join(sheets))
    book = openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
    try:
        rows = [[_cell(v) for v in row] for row in book[name].iter_rows(values_only=True)]
    finally:
        book.close()
    # Les cellules vides en fin de ligne n'ajoutent pas de colonne.
    while rows and not any(str(c).strip() for c in rows[-1]):
        rows.pop()
    return _rows_to_example(rows, decl.header)


def read_example(decl: ImportDeclaration, raw: bytes) -> Example:
    """Les lignes du fichier d'exemple, selon le format et les réglages de lecture."""
    if not raw:
        return Example([])
    if decl.format == "xlsx":
        return _read_xlsx(decl, raw)
    if decl.format == "csv":
        try:
            # « utf-8 » lu en « utf-8-sig » : un fichier enregistré par Excel commence
            # par un BOM, qui se collerait sinon au nom de la première colonne.
            utf8 = decl.encoding.lower().replace("_", "-") in ("utf-8", "utf8")
            encoding = "utf-8-sig" if utf8 else decl.encoding
            text = raw.decode(encoding)
        except (UnicodeDecodeError, LookupError) as exc:
            raise ExampleError("example_encoding", str(exc)) from None
        rows: list[list[Any]] = list(csv.reader(io.StringIO(text), delimiter=decl.separator))
        return _rows_to_example(rows, decl.header)
    try:
        data = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise ExampleError("example_json", str(exc)) from None
    if isinstance(data, list):
        return Example([Record(item, f"élément [{i}]") for i, item in enumerate(data)])
    return Example([Record(data, "élément")])


SNIFF_LINES = 20
SEPARATOR_CANDIDATES = (";", ",", "\t", "|")


def detect_encoding(raw: bytes) -> str:
    """L'encodage probable d'un CSV déposé (ticket 123631e5) : UTF-16 s'il porte
    son BOM, UTF-8 s'il se décode ainsi, sinon cp1252.

    cp1252 en dernier recours, et non latin-1 : c'est ce qu'écrit Excel sous
    Windows en Europe de l'Ouest (Gael, fichier `carriers`), et il décode aussi
    les guillemets typographiques et l'euro que latin-1 lirait de travers."""
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return "utf-16"
    try:
        raw.decode("utf-8")
    except UnicodeDecodeError:
        return "cp1252"
    return "utf-8"


def detect_separator(text: str) -> str | None:
    """Le séparateur présent sur toutes les premières lignes, le même nombre de fois
    de préférence ; `None` si aucun ne se dégage (fichier d'une colonne, vide) :
    on garde alors le réglage en place plutôt que d'en imposer un au hasard.

    Compté à la main plutôt qu'avec `csv.Sniffer`, qui choisit parfois une lettre
    ou un espace comme délimiteur sur des fichiers courts."""
    lines = [line for line in text.splitlines() if line.strip()][:SNIFF_LINES]
    best: tuple[bool, int] | None = None
    chosen: str | None = None
    for sep in SEPARATOR_CANDIDATES:
        counts = [len(next(csv.reader([line], delimiter=sep), [])) - 1 for line in lines]
        if not counts or min(counts) < 1:
            continue
        score = (len(set(counts)) == 1, min(counts))
        if best is None or score > best:
            best, chosen = score, sep
    return chosen


def detect_reading(raw: bytes) -> tuple[str, str | None]:
    """Encodage et séparateur d'un CSV déposé, posés dans l'import au dépôt : sans
    cela, un fichier Excel (cp1252, « ; ») ne se lit pas avec les réglages par
    défaut, et le template n'a ni aperçu ni complétion des colonnes."""
    encoding = detect_encoding(raw)
    text = raw.decode("utf-8-sig" if encoding == "utf-8" else encoding, errors="replace")
    return encoding, detect_separator(text)


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
    except UnknownFunctionError as exc:
        return [
            TemplateProblem(
                "template_unknown_function",
                v.detail or "",
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


def transform(target: ImportTarget, source: str, example: Example) -> Transformed:
    """Applique le template à chaque ligne du fichier et fait valider ce qu'il
    rend par la cible (module commun, enabler 4f32c8c6)."""
    problems = check_template(source)
    if problems:
        return Transformed(problems)
    template = compile_template(source)
    produced: list[Any] = []
    origins: list[str] = []
    errors: list[dict[str, Any]] = []
    # Forme canonique d'un élément, si la cible en a une (ex. un objet de
    # cartographie sans documentation ni propriétés vides) : comparer ce qui est
    # écrit à ce qui existe sans voir de faux changements.
    normalize = getattr(target, "normalize", None)
    for record in example.records:
        try:
            value = evaluate(template, record.data, BUDGET)
            if normalize is not None:
                value = normalize(value)
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
    value_problems: list[ItemProblem] = target.check(produced)
    for p in value_problems:
        errors.append(
            {
                "where": origins[p.index] if 0 <= p.index < len(origins) else "",
                "code": p.code,
                "message": p.detail,
                "path": None,
                "expected": None,
                "field": p.field,
                "key": p.key,
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


@dataclass(frozen=True)
class Absent:
    """Un élément actuel absent du fichier : supprimé, ou gardé avec sa raison et,
    s'il est encore utilisé, ses utilisations (forme donnée par la cible)."""

    key: Hashable
    label: str
    item: Any
    reason: str = ""  # vide : supprimé
    usages: tuple[dict[str, Any], ...] = ()
    ref: str | None = None  # l'identifiant de sa fiche, si la cible en a une


@dataclass(frozen=True)
class DeletionPlan:
    deleted: list[Absent] = field(default_factory=list)
    kept: list[Absent] = field(default_factory=list)

    @property
    def deleted_keys(self) -> set[Hashable]:
        return {a.key for a in self.deleted}

    @property
    def in_use(self) -> list[Absent]:
        return [a for a in self.kept if a.reason == KEPT_IN_USE]


def deletion_plan(
    target: ImportTarget,
    current: list[Any],
    done: Transformed,
    deletion: str = KEEP,
    created: Set[Hashable] = frozenset(),
) -> DeletionPlan:
    """Ce que devient chaque élément actuel absent du fichier, selon la stratégie :
    `keep` les garde ; `created` supprime ceux que cet import a créés (`created`) ;
    `all` les supprime tous. Un élément à supprimer encore utilisé dans l'état final
    (par un élément qui reste, `target.in_use`) est gardé ; on recommence jusqu'à ce
    que plus rien ne change (un élément gardé peut en retenir un autre)."""
    produced = [v for _i, v in done.valid()]
    keys = {target.key(v) for v in produced}
    labels: dict[Hashable, tuple[str, Any]] = {}
    for index, before in enumerate(current):
        key = target.key(before)
        if key is not None and key not in keys:
            labels[key] = (target.label(before, index), before)
    reasons: dict[Hashable, str] = {}
    for key in labels:
        if deletion == KEEP:
            reasons[key] = KEPT_STRATEGY
        elif deletion == CREATED and key not in created:
            reasons[key] = KEPT_NOT_CREATED
    deleting = {k for k in labels if k not in reasons}
    usages: dict[Hashable, tuple[dict[str, Any], ...]] = {}
    in_use = getattr(target, "in_use", None)
    while deleting and in_use is not None:
        used: dict[Hashable, list[dict[str, Any]]] = in_use(produced, deleting)
        if not used:
            break
        for key, found in used.items():
            deleting.discard(key)
            reasons[key] = KEPT_IN_USE
            usages[key] = tuple(found)
    ref_of = getattr(target, "ref", None)

    def absent(key: Hashable, reason: str = "") -> Absent:
        label, item = labels[key]
        ref = ref_of(item) if ref_of is not None else None
        return Absent(key, label, item, reason, usages.get(key, ()), ref)

    return DeletionPlan(
        deleted=[absent(k) for k in labels if k in deleting],
        kept=[absent(k, reasons[k]) for k in labels if k not in deleting],
    )


def _absent_payload(a: Absent) -> dict[str, Any]:
    out: dict[str, Any] = {"key": a.label, "where": "", "value": a.item}
    if a.ref is not None:
        out["ref"] = a.ref
    if a.reason:
        out.update({"kind": "kept", "reason": a.reason})
        if a.usages:
            out["usages"] = list(a.usages)
    else:
        out["kind"] = "deleted"
    return out


def report(
    target: ImportTarget,
    current: list[Any],
    done: Transformed,
    plan: DeletionPlan | None = None,
) -> dict[str, Any]:
    """Compte rendu par rapport aux valeurs actuelles (comparées par la clé) :
    nouvelles, modifiées, inchangées, en erreur, et — selon le plan de suppression —
    supprimées et absentes gardées (avec la raison) ; et les différences."""
    if done.template_problems:
        return {"ok": False, "template_problems": [p.payload() for p in done.template_problems]}
    by_key = {target.key(v): v for v in current if target.key(v) is not None}
    new = modified = unchanged = 0
    differences: list[dict[str, Any]] = []
    for index, value in done.valid():
        label = target.label(value, index)
        before = by_key.get(target.key(value))
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
    plan = plan or DeletionPlan()
    # Les gardés car utilisés d'abord : ce sont eux qui demandent une action.
    absents = [*plan.deleted, *plan.in_use, *(a for a in plan.kept if a.reason != KEPT_IN_USE)]
    for absent in absents:
        if len(differences) >= DIFF_MAX:
            break
        differences.append(_absent_payload(absent))
    return {
        "ok": done.ok,
        "template_problems": [],
        "lines": done.lines,
        "new": new,
        "modified": modified,
        "unchanged": unchanged,
        "deleted": len(plan.deleted),
        "kept": len(plan.kept),
        "kept_in_use": len(plan.in_use),
        "errors": len({e["where"] for e in done.errors}),
        "error_details": done.errors[:DIFF_MAX],
        "differences": differences,
    }


def run_template(
    target: ImportTarget,
    current: list[Any],
    source: str,
    example: Example,
    deletion: str = KEEP,
    created: Set[Hashable] = frozenset(),
) -> dict[str, Any]:
    """L'essai : applique le template au fichier et compare aux valeurs
    actuelles, suppressions prévues comprises. Rien n'est écrit."""
    done = transform(target, source, example)
    plan = deletion_plan(target, current, done, deletion, created) if done.ok else None
    return report(target, current, done, plan)


def imported_items(
    target: ImportTarget, current: list[Any], done: Transformed, plan: DeletionPlan
) -> list[Any] | None:
    """Les éléments après l'import, ou None s'il est refusé (**tout ou rien** : la
    moindre erreur bloque) : les éléments du fichier remplacent ceux de même clé,
    les nouveaux s'ajoutent, les absents restent sauf ceux que le plan supprime."""
    if not done.ok:
        return None
    produced = [v for _i, v in done.valid()]
    deleted = plan.deleted_keys
    merged = [v for v in current if target.key(v) not in deleted]
    position = {target.key(v): i for i, v in enumerate(merged) if target.key(v) is not None}
    for value in produced:
        index = position.get(target.key(value))
        if index is None:
            merged.append(value)
        else:
            merged[index] = value
    return merged


def created_after(
    target: ImportTarget,
    current: list[Any],
    items: list[Any],
    created: Set[Hashable],
) -> set[Hashable]:
    """La provenance après un import réussi : les clés qu'il avait créées et qui
    existent toujours, plus celles qu'il vient de créer (absentes avant)."""
    before = {target.key(v) for v in current}
    after = {target.key(v) for v in items} - {None}
    return {k for k in after if k in created or k not in before}


# Libellés des stratégies, pour la page des éléments gardés et le journal.
DELETION_LABELS = {
    KEEP: "On garde les éléments existants qui ne sont pas dans la liste",
    CREATED: "On supprime les éléments absents que cet import a créés",
    ALL: "On supprime tous les éléments absents",
}
