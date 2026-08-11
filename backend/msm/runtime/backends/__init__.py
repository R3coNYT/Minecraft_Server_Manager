"""Sélection du backend de processus adapté au système hôte."""

from __future__ import annotations

import sys
from functools import lru_cache

from msm.runtime.backends.base import ProcessBackend, SpawnedProcess


@lru_cache(maxsize=1)
def get_backend() -> ProcessBackend:
    """Renvoie le backend correspondant au système courant (instance unique)."""
    if sys.platform == "win32":
        from msm.runtime.backends.windows import WindowsProcessBackend

        return WindowsProcessBackend()

    from msm.runtime.backends.posix import PosixProcessBackend

    return PosixProcessBackend()


__all__ = ["ProcessBackend", "SpawnedProcess", "get_backend"]
