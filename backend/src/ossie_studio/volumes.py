from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class VolumeStatus:
    name: str
    path: str
    writable: bool
    detail: str | None = None


def check_volume_writable(name: str, path: Path) -> VolumeStatus:
    """Vérifie qu'un volume est monté et inscriptible, par un fichier sonde.

    Le contrôle de santé s'en sert : un volume absent ou en lecture seule doit
    être signalé nommément, jamais masqué.
    """
    if not path.exists():
        return VolumeStatus(name, str(path), writable=False, detail="répertoire absent")
    if not path.is_dir():
        return VolumeStatus(name, str(path), writable=False, detail="n'est pas un répertoire")
    probe = path / f".health-probe-{uuid.uuid4().hex}"
    try:
        probe.write_bytes(b"ok")
        probe.read_bytes()
    except OSError as exc:
        return VolumeStatus(name, str(path), writable=False, detail=f"écriture refusée : {exc}")
    finally:
        try:
            os.unlink(probe)
        except OSError:
            pass
    return VolumeStatus(name, str(path), writable=True)
