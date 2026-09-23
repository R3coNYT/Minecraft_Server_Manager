"""Traductions françaises, indexées par le texte anglais source.

Un module par domaine, pour que chaque traduction reste à côté de ses voisines
de sens. Les modules sont découverts d'eux-mêmes : en ajouter un suffit. Une clé
présente dans deux modules avec des traductions différentes est une erreur : la
fusion la signale plutôt que de laisser l'un écraser l'autre en silence.
"""

from __future__ import annotations

import pkgutil
from importlib import import_module


def _merge() -> dict[str, str]:
    merged: dict[str, str] = {}
    for name in sorted(module.name for module in pkgutil.iter_modules(__path__)):
        messages: dict[str, str] = import_module(f"msm.i18n.fr.{name}").MESSAGES
        for key, value in messages.items():
            if key in merged and merged[key] != value:
                raise RuntimeError(f"Traduction en double et divergente : {key!r}")
            merged[key] = value
    return merged


MESSAGES: dict[str, str] = _merge()
