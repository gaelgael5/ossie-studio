"""Le refus du service des sources (ticket 1920df67) : un code et ses détails, comme
l'écran les rend. Chaque appelant le traduit : l'écran en `ApiError` (avec le statut
HTTP), l'agent en refus nommé (même code, mêmes détails)."""

from __future__ import annotations


class SourceError(Exception):
    def __init__(self, code: str, status: int, **args: object) -> None:
        super().__init__(code)
        self.code = code
        self.status = status
        self.args_dict = args
