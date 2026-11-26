"""Index en mémoire d'une liste lue dans la vue d'une branche (ticket 0373780f).

Lire la vue d'une personne matérialise son brouillon (fetch, puis tout le
brouillon chargé depuis la base) : 2 à 4 s sur un gros espace. Une liste qui ne
dépend que de la tête publiée et des chemins d'un dossier dans le brouillon est
rendue d'ici tant que leur empreinte (`DraftWorkdir.prefix_signature`) ne bouge
pas. En mémoire et non en base : rien à migrer, l'index se refait au premier
appel après un redémarrage. Borné : une entrée par clé (espace, branche,
personne, vue), les plus anciennes sortent d'abord.
"""

from __future__ import annotations

from collections import OrderedDict


class ListIndex[T]:
    def __init__(self, max_entries: int = 256) -> None:
        self.max_entries = max_entries
        self._entries: OrderedDict[tuple[object, ...], tuple[str, T]] = OrderedDict()

    def get(self, key: tuple[object, ...], signature: str) -> T | None:
        """La liste gardée pour cette clé, si elle a été lue à cette empreinte."""
        cached = self._entries.get(key)
        if cached is None or cached[0] != signature:
            return None
        self._entries.move_to_end(key)
        return cached[1]

    def put(self, key: tuple[object, ...], signature: str, items: T) -> None:
        self._entries[key] = (signature, items)
        self._entries.move_to_end(key)
        while len(self._entries) > self.max_entries:
            self._entries.popitem(last=False)

    def clear(self) -> None:
        self._entries.clear()
