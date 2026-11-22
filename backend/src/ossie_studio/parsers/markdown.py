"""Plugin `markdown` : trouve les documents Markdown d'une source et les range
dans `raw/docs/`, avec un index.

Un document n'est **pas** un objet structuré : il n'est pas converti en JSON
Schema. Il est **recopié tel quel** sous `raw/docs/`, en conservant son chemin
relatif dans le dépôt, et décrit dans `raw/docs-index.yaml` : titre, sections,
**mentions** et ressources non copiées.

Les **mentions** sont les identifiants que le document cite dans son texte et ses
blocs de code (noms repérés tels quels). Le plugin ne sait pas si une mention
correspond à un vrai objet : il ne lit jamais la sortie d'un autre plugin. Le
rapprochement mention ↔ asset se fera plus tard, côté Curation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import yaml

from ossie_studio.parsers.base import (
    CIBLE_RAW,
    STATUT_ERREUR,
    STATUT_INCHANGE,
    STATUT_TRAITE,
    OutputAction,
    PluginContext,
    PluginError,
    PluginParam,
    PluginResult,
    matches_globs,
)

MARKDOWN_PLUGIN_ID = "markdown"
MARKDOWN_PLUGIN_VERSION = "1.0"
DOCS_DIR = "docs"
DOCS_INDEX = "docs-index.yaml"
# Version 2 : clés en anglais (`title`, `resources`). La version 1 (`titre`,
# `ressources`) se relit en repli ; on réécrit toujours en version 2.
DOCS_INDEX_VERSION = 2
_GLOBS = ["**/*.md", "**/*.markdown"]
_DEFAULT_MAX_KB = 512
PARAM_MAX_KB = "taille_max_ko"

_H1 = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)
_HEADING = re.compile(r"^#{2,6}\s+(.+?)\s*$", re.MULTILINE)
_FENCE = re.compile(r"```.*?\n(.*?)```", re.DOTALL)
_INLINE_CODE = re.compile(r"`([^`\n]+)`")
_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_.]{2,}")
_PASCAL = re.compile(r"\b([A-Z][a-zA-Z0-9]*[a-z][a-zA-Z0-9]*)\b")
_IMAGE = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")
_LINK = re.compile(r"(?<!\!)\[[^\]]*\]\(([^)]+)\)")
_FOLLOWED_SCHEMES = ("http://", "https://", "mailto:", "#")


@dataclass
class DocEntry:
    path: str
    crc: str
    title: str
    sections: list[str] = field(default_factory=list)
    mentions: list[str] = field(default_factory=list)
    resources: list[str] = field(default_factory=list)

    def to_mapping(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "crc": self.crc,
            "title": self.title,
            "sections": list(self.sections),
            "mentions": list(self.mentions),
            "resources": list(self.resources),
        }


def parse_docs_index(text: str | None) -> dict[str, DocEntry]:
    data = yaml.safe_load(text) if text else None
    out: dict[str, DocEntry] = {}
    for row in (data or {}).get("documents", []) if isinstance(data, dict) else []:
        if not isinstance(row, dict):
            continue
        path = str(row.get("path", ""))
        out[path] = DocEntry(
            path=path,
            crc=str(row.get("crc", "")),
            title=str(row.get("title", row.get("titre", ""))),
            sections=[str(s) for s in row.get("sections") or []],
            mentions=[str(s) for s in row.get("mentions") or []],
            resources=[str(s) for s in row.get("resources", row.get("ressources")) or []],
        )
    return out


def dump_docs_index(entries: dict[str, DocEntry]) -> str:
    mapping = {
        "version": DOCS_INDEX_VERSION,
        "documents": [entries[p].to_mapping() for p in sorted(entries)],
    }
    return yaml.safe_dump(mapping, sort_keys=False, allow_unicode=True, default_flow_style=False)


def _title(path: str, text: str) -> str:
    match = _H1.search(text)
    if match:
        return match.group(1).strip()
    return path.rsplit("/", 1)[-1]


def _mentions(text: str) -> list[str]:
    """Identifiants cités : code inline qui ressemble à un identifiant, et noms
    PascalCase dans les blocs de code. Déterministe (trié, dédoublonné)."""
    found: set[str] = set()
    for code in _INLINE_CODE.findall(text):
        token = code.strip()
        if _IDENT.fullmatch(token):
            found.add(token)
    for block in _FENCE.findall(text):
        for name in _PASCAL.findall(block):
            found.add(name)
    return sorted(found)


def _resources(text: str) -> list[str]:
    """Images et liens relatifs — non copiés, non suivis."""
    found: set[str] = set()
    for target in _IMAGE.findall(text) + _LINK.findall(text):
        ref = target.strip().split()[0] if target.strip() else ""
        if ref and not ref.lower().startswith(_FOLLOWED_SCHEMES):
            found.add(ref)
    return sorted(found)


def analyze_markdown(path: str, text: str, crc: str) -> DocEntry:
    return DocEntry(
        path=path,
        crc=crc,
        title=_title(path, text),
        sections=[m.strip() for m in _HEADING.findall(text)],
        mentions=_mentions(text),
        resources=_resources(text),
    )


class MarkdownPlugin:
    """Plugin de documentation : copie les `.md` dans `raw/docs/` et les indexe.
    Incrémental via `docs-index.yaml` (crc) ; ne retransforme que ce qui change."""

    id = MARKDOWN_PLUGIN_ID
    version = MARKDOWN_PLUGIN_VERSION
    label = "Documents Markdown"
    cible = CIBLE_RAW

    def globs(self) -> list[str]:
        return list(_GLOBS)

    def parameters(self) -> list[PluginParam]:
        return [
            PluginParam(
                slug=PARAM_MAX_KB,
                label="Taille maximale d'un document (Ko)",
                doc="Au-delà, le document est signalé et non copié.",
                type="int",
                default=str(_DEFAULT_MAX_KB),
            )
        ]

    def run(self, context: PluginContext) -> PluginResult:
        try:
            max_kb = int(context.params.get(PARAM_MAX_KB) or _DEFAULT_MAX_KB)
        except ValueError:
            max_kb = _DEFAULT_MAX_KB
        max_bytes = max(1, max_kb) * 1024

        previous = parse_docs_index(context.raw.read_index(DOCS_INDEX))
        result = PluginResult()
        index: dict[str, DocEntry] = {}
        current = {f.path for f in context.files}

        for file in context.files:
            prev = previous.get(file.path)
            if prev is not None and prev.crc == file.crc and not context.force:
                index[file.path] = prev
                result.unchanged += 1
                result.statuses[file.path] = STATUT_INCHANGE
                continue
            raw = context.contents[file.path]
            if len(raw) > max_bytes:
                result.errors.append(PluginError(source=file.path, cause="too_large"))
                result.statuses[file.path] = STATUT_ERREUR
                continue
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                result.errors.append(PluginError(source=file.path, cause="unreadable"))
                result.statuses[file.path] = STATUT_ERREUR
                continue
            if not text.strip():
                result.errors.append(PluginError(source=file.path, cause="empty"))
                result.statuses[file.path] = STATUT_ERREUR
                continue
            index[file.path] = analyze_markdown(file.path, text, file.crc)
            result.writes.append(OutputAction(path=f"{DOCS_DIR}/{file.path}", content=text))
            result.statuses[file.path] = STATUT_TRAITE
            if prev is None:
                result.added += 1
            else:
                result.modified += 1
            result.read += 1

        for path in previous:
            if path not in current:
                result.deletes.append(f"{DOCS_DIR}/{path}")
                result.deleted += 1

        result.retained = len(index)
        result.writes.append(OutputAction(path=DOCS_INDEX, content=dump_docs_index(index)))
        return result

    def handles(self, path: str) -> bool:
        return matches_globs(_GLOBS, path)
