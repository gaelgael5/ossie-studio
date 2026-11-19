"""Correspondance des types SQL Server vers JSON Schema, **partagée** par
l'extracteur de catalogue (base en ligne) et le parseur de projet SQL Server
(fichiers `.sql`). Même correspondance, même forme de sortie."""

from __future__ import annotations

from typing import Any

STRING_TYPES = ("nvarchar", "varchar", "char", "nchar", "text", "ntext")


def db_type(data_type: str, max_len: int | None, precision: int | None, scale: int | None) -> str:
    """Type d'origine reconstruit (utilisé quand on n'a pas le texte brut)."""
    dt = data_type.lower()
    if dt in STRING_TYPES:
        if max_len == -1:
            return f"{dt}(max)"
        return f"{dt}({max_len})" if max_len else dt
    if dt in ("decimal", "numeric"):
        return f"{dt}({precision or 18},{scale or 0})"
    return dt


def type_fragment(
    data_type: str,
    max_len: int | None,
    precision: int | None,
    scale: int | None,
    *,
    raw: str | None = None,
) -> dict[str, Any]:
    """Fragment JSON Schema d'une colonne SQL Server. `raw` : type **tel qu'écrit**
    (projet `.sql`) conservé en x-db-type ; sinon il est reconstruit."""
    dt = data_type.lower()
    frag: dict[str, Any]
    if dt in STRING_TYPES:
        frag = {"type": "string"}
        if max_len is not None and max_len > 0:
            frag["maxLength"] = max_len
    elif dt == "int":
        frag = {"type": "integer", "format": "int32"}
    elif dt == "bigint":
        frag = {"type": "integer", "format": "int64"}
    elif dt in ("smallint", "tinyint"):
        frag = {"type": "integer"}
    elif dt in ("decimal", "numeric"):
        frag = {"type": "integer"} if (scale or 0) == 0 else {"type": "number"}
    elif dt in ("money", "smallmoney", "float", "real"):
        frag = {"type": "number"}
    elif dt == "bit":
        frag = {"type": "boolean"}
    elif dt == "uniqueidentifier":
        frag = {"type": "string", "format": "uuid"}
    elif dt == "date":
        frag = {"type": "string", "format": "date"}
    elif dt in ("datetime", "datetime2", "smalldatetime", "datetimeoffset"):
        frag = {"type": "string", "format": "date-time"}
    elif dt == "time":
        frag = {"type": "string", "format": "time"}
    elif dt in ("binary", "varbinary", "image"):
        frag = {"type": "string", "contentEncoding": "base64"}
    elif dt == "xml":
        frag = {"type": "string"}
    else:
        frag = {}
    frag["x-db-type"] = raw if raw is not None else db_type(data_type, max_len, precision, scale)
    return frag
