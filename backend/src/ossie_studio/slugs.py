from __future__ import annotations

import re
import unicodedata


def slugify(label: str, *, fallback: str = "item") -> str:
    """Transforme un libellé en slug : minuscules, accents retirés, tout ce qui
    n'est pas alphanumérique devient un tiret, sans tiret en bord. Un libellé
    sans caractère exploitable retombe sur `fallback`."""
    normalized = unicodedata.normalize("NFKD", label).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", normalized.lower()).strip("-")
    return slug or fallback
