"""En-tête des procédures stockées et des fonctions T-SQL (epic « Cartographie
applicative dans raw », ticket 30c55418).

Une procédure ou une fonction d'un projet SQL Server devient une **fonction** du
module du projet (`.extend.yaml`) : nom qualifié (`schéma.nom`), paramètres et
leur type tel qu'écrit, type de retour pour une fonction. Seul l'**en-tête** est
lu, par un petit lecteur dédié : le corps n'est jamais analysé (ni exécuté), et
sqlglot échoue sur des formes courantes de l'en-tête (`@x AS INT`,
`RETURNS @t TABLE (…)`).

Formes lues (T-SQL impose que `CREATE PROCEDURE` / `CREATE FUNCTION` ouvre son
lot) :

    CREATE [OR ALTER] {PROC | PROCEDURE} nom [;n] [(] [@p [AS] type [VARYING]
        [NULL] [= défaut] [OUT | OUTPUT | READONLY], …] [)] [WITH …] [FOR …] AS …
    CREATE [OR ALTER] FUNCTION nom ( [@p [AS] type [= défaut] [READONLY], …] )
        RETURNS {type | TABLE | @t TABLE (…)} [WITH …] [AS] …
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass

PROCEDURE = "procedure"
FUNCTION = "function"
TABLE = "TABLE"
DEFAULT_SCHEMA = "dbo"

_MODIFIERS = {"OUT": "OUTPUT", "OUTPUT": "OUTPUT", "READONLY": "READONLY"}
_HEADER_END = {"WITH", "FOR", "AS"}
_RETURNS_END = {"WITH", "AS", "BEGIN", "RETURN"}
_START = re.compile(
    r"\s*(?:(?:--[^\n]*\n|/\*.*?\*/)\s*)*CREATE\s+(?:OR\s+ALTER\s+)?(?:PROC|PROCEDURE|FUNCTION)\b",
    re.IGNORECASE | re.DOTALL,
)
_WORD = re.compile(r"[A-Za-z_#][\w$#]*")
_NUMBER = re.compile(r"\d+(?:\.\d+)?")
_VAR = re.compile(r"@@?[\w$#]+")


class RoutineError(ValueError):
    """En-tête illisible : la procédure ou la fonction est laissée de côté."""


@dataclass(frozen=True)
class Parameter:
    name: str  # tel qu'écrit, avec son `@`
    type: str  # tel qu'écrit, suivi de OUTPUT / READONLY le cas échéant
    base: str  # le type seul (sans modificateur), pour sa forme JSON Schema
    optional: bool  # valeur par défaut


@dataclass(frozen=True)
class Routine:
    kind: str  # PROCEDURE | FUNCTION
    schema: str
    name: str
    parameters: tuple[Parameter, ...]
    returns: str | None  # fonction : le type rendu (TABLE pour une fonction table)
    line: int  # ligne de `CREATE` dans le lot (1 = première ligne du lot)

    @property
    def qualified(self) -> str:
        return f"{self.schema}.{self.name}"


@dataclass(frozen=True)
class _Token:
    kind: str  # word | ident | var | string | number | punct
    value: str
    start: int
    end: int

    def is_word(self, *words: str) -> bool:
        return self.kind == "word" and self.value.upper() in words


def is_routine(batch: str) -> bool:
    """Le lot crée-t-il une procédure ou une fonction ?"""
    return _START.match(batch) is not None


def _tokens(text: str) -> Iterator[_Token]:
    """Jetons de l'en-tête, à la demande (le corps n'est jamais parcouru en entier) ;
    commentaires sautés."""
    i, size = 0, len(text)
    while i < size:
        char = text[i]
        if char.isspace():
            i += 1
        elif text.startswith("--", i):
            end = text.find("\n", i)
            i = size if end < 0 else end + 1
        elif text.startswith("/*", i):
            depth, i = 1, i + 2
            while i < size and depth:
                if text.startswith("/*", i):
                    depth, i = depth + 1, i + 2
                elif text.startswith("*/", i):
                    depth, i = depth - 1, i + 2
                else:
                    i += 1
        elif char in '["':
            close = "]" if char == "[" else '"'
            j = i + 1
            while j < size:
                if text[j] == close and text[j + 1 : j + 2] == close:
                    j += 2
                elif text[j] == close:
                    break
                else:
                    j += 1
            if j >= size:
                raise RoutineError("unterminated identifier")
            yield _Token("ident", text[i + 1 : j].replace(close * 2, close), i, j + 1)
            i = j + 1
        elif char == "'" or (char in "Nn" and text[i + 1 : i + 2] == "'"):
            j = i + (2 if char != "'" else 1)
            while j < size:
                if text[j] == "'" and text[j + 1 : j + 2] == "'":
                    j += 2
                elif text[j] == "'":
                    break
                else:
                    j += 1
            if j >= size:
                raise RoutineError("unterminated string")
            yield _Token("string", text[i : j + 1], i, j + 1)
            i = j + 1
        elif char == "@" and (match := _VAR.match(text, i)):
            yield _Token("var", match.group(0), i, match.end())
            i = match.end()
        elif match := _WORD.match(text, i):
            yield _Token("word", match.group(0), i, match.end())
            i = match.end()
        elif match := _NUMBER.match(text, i):
            yield _Token("number", match.group(0), i, match.end())
            i = match.end()
        else:
            yield _Token("punct", char, i, i + 1)
            i += 1


class _Reader:
    def __init__(self, text: str) -> None:
        self.text = text
        self._stream = _tokens(text)
        self._peeked: _Token | None = None

    def peek(self) -> _Token | None:
        if self._peeked is None:
            self._peeked = next(self._stream, None)
        return self._peeked

    def take(self) -> _Token:
        token = self.peek()
        if token is None:
            raise RoutineError("header ends too early")
        self._peeked = None
        return token

    def punct(self, char: str) -> bool:
        token = self.peek()
        if token is not None and token.kind == "punct" and token.value == char:
            self.take()
            return True
        return False

    def expect_word(self, *words: str) -> _Token:
        token = self.take()
        if not token.is_word(*words):
            raise RoutineError(f"expected {'/'.join(words)}, found {token.value!r}")
        return token

    def word_ahead(self, *words: str) -> bool:
        token = self.peek()
        return token is not None and token.is_word(*words)

    def name(self) -> list[str]:
        parts = []
        while True:
            token = self.take()
            if token.kind not in ("word", "ident"):
                raise RoutineError(f"expected a name, found {token.value!r}")
            parts.append(token.value)
            if not self.punct("."):
                return parts

    def skip_group(self) -> int:
        """Après `(` : jusqu'à la `)` correspondante ; rend la fin de celle-ci."""
        depth = 1
        while depth:
            token = self.take()
            if token.kind == "punct" and token.value == "(":
                depth += 1
            elif token.kind == "punct" and token.value == ")":
                depth -= 1
        return token.end


def _read_type(reader: _Reader) -> str:
    """Le type d'un paramètre ou d'un retour, tel qu'écrit."""
    first = reader.take()
    if first.kind not in ("word", "ident"):
        raise RoutineError(f"expected a type, found {first.value!r}")
    start, end = first.start, first.end
    while reader.punct("."):
        part = reader.take()
        if part.kind not in ("word", "ident"):
            raise RoutineError(f"expected a type, found {part.value!r}")
        end = part.end
    if reader.punct("("):
        end = reader.skip_group()
    while reader.word_ahead("VARYING"):
        end = reader.take().end
    return " ".join(reader.text[start:end].split())


def _skip_default(reader: _Reader) -> None:
    """Après `=` : la valeur par défaut (constante, `NULL`, `-1`, `N'…'`, appel)."""
    value = reader.take()
    if value.kind == "punct" and value.value in "+-":
        value = reader.take()
    if value.kind == "punct" and value.value == "(":
        reader.skip_group()
    elif value.kind == "word" and reader.punct("("):
        reader.skip_group()


def _parameter(reader: _Reader, stop: set[str]) -> Parameter:
    """`@nom [AS] type [VARYING] [NULL] [= défaut] [OUT | OUTPUT | READONLY]`."""
    name = reader.take()
    if reader.word_ahead("AS"):
        reader.take()
    base = _read_type(reader)
    optional = False
    modifiers: list[str] = []
    while (token := reader.peek()) is not None:
        if (token.kind == "punct" and token.value in ",)") or token.is_word(*stop):
            break
        if token.is_word(*_MODIFIERS):
            modifiers.append(_MODIFIERS[reader.take().value.upper()])
        elif token.is_word("NULL", "NOT"):
            reader.take()
        elif token.kind == "punct" and token.value == "=":
            reader.take()
            _skip_default(reader)
            optional = True
        else:
            raise RoutineError(f"unexpected {token.value!r} in parameter {name.value}")
    return Parameter(
        name=name.value, type=" ".join([base, *modifiers]), base=base, optional=optional
    )


def _parameters(reader: _Reader, stop: set[str]) -> list[Parameter]:
    params: list[Parameter] = []
    while (token := reader.peek()) is not None and token.kind == "var":
        params.append(_parameter(reader, stop))
        if not reader.punct(","):
            break
    return params


def read_routine(batch: str) -> Routine:
    """L'en-tête d'un lot `CREATE PROCEDURE` / `CREATE FUNCTION`. Lève
    RoutineError s'il est illisible."""
    reader = _Reader(batch)
    create = reader.expect_word("CREATE")
    if reader.word_ahead("OR"):
        reader.take()
        reader.expect_word("ALTER")
    kind_token = reader.expect_word("PROC", "PROCEDURE", "FUNCTION")
    kind = FUNCTION if kind_token.value.upper() == "FUNCTION" else PROCEDURE
    parts = reader.name()
    schema = parts[-2] if len(parts) > 1 else DEFAULT_SCHEMA
    line = batch.count("\n", 0, create.start) + 1
    returns: str | None = None
    if kind == PROCEDURE:
        if reader.punct(";"):  # procédure numérotée (`nom;2`)
            reader.take()
        wrapped = reader.punct("(")
        params = _parameters(reader, _HEADER_END)
        if wrapped and not reader.punct(")"):
            raise RoutineError("unbalanced parameter list")
        end = reader.take()
        if not end.is_word(*_HEADER_END):
            raise RoutineError(f"unexpected {end.value!r} after the parameters")
    else:
        if not reader.punct("("):
            raise RoutineError("expected ( after the function name")
        params = _parameters(reader, set())
        if not reader.punct(")"):
            raise RoutineError("unbalanced parameter list")
        reader.expect_word("RETURNS")
        token = reader.peek()
        if token is not None and token.kind == "var":  # RETURNS @t TABLE (…)
            reader.take()
            reader.expect_word("TABLE")
            returns = TABLE
        elif token is not None and token.is_word("TABLE"):
            reader.take()
            returns = TABLE
        else:
            returns = _read_type(reader)
        after = reader.peek()
        if after is not None and not (after.is_word(*_RETURNS_END) or after.value == "("):
            raise RoutineError(f"unexpected {after.value!r} after RETURNS")
    return Routine(
        kind=kind,
        schema=schema,
        name=parts[-1],
        parameters=tuple(params),
        returns=returns,
        line=line,
    )
