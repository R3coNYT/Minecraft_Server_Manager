"""Registre des types d'actions.

Ajouter une action se résume à écrire une classe et à l'enregistrer ici : ni le
moteur, ni l'API, ni le frontend n'ont besoin de la connaître à l'avance —
l'interface se construit à partir de la description que l'action publie.
"""

from __future__ import annotations

from typing import Any

from msm.core.danger import DangerLevel
from msm.events.actions import BUILTIN_ACTIONS, Action, CommandAction
from msm.exceptions import ValidationError

_REGISTRY: dict[str, Action] = {}


def register(action: Action, *, replace: bool = False) -> None:
    if action.key in _REGISTRY and not replace:
        raise ValueError(f"Une action est déjà enregistrée sous la clé « {action.key} ».")
    _REGISTRY[action.key] = action


def get(key: str) -> Action:
    try:
        return _REGISTRY[key]
    except KeyError:
        known = ", ".join(sorted(_REGISTRY)) or "aucune"
        raise ValidationError(
            "Type d'action inconnu.",
            cause=f"« {key} » ne correspond à aucune action enregistrée.",
            remediation=f"Actions disponibles : {known}.",
        ) from None


def all_actions() -> tuple[Action, ...]:
    return tuple(_REGISTRY[key] for key in sorted(_REGISTRY))


def describe_all() -> list[dict[str, Any]]:
    """Catalogue destiné au frontend, qui en déduit ses formulaires."""
    return [action.to_dict() for action in all_actions()]


def danger_of(key: str, params: dict[str, Any]) -> DangerLevel:
    """Niveau de risque d'une étape, paramètres compris.

    Une commande personnalisée est jugée sur son contenu : classer toute commande
    libre comme dangereuse obligerait à distribuer largement la permission
    correspondante, ce qui la viderait de sa portée.
    """
    action = get(key)
    if isinstance(action, CommandAction):
        return action.danger_for(params)
    return action.danger


for _action in BUILTIN_ACTIONS:
    register(_action)

__all__ = ["all_actions", "danger_of", "describe_all", "get", "register"]
