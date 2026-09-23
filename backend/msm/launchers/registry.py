"""Registre des méthodes de démarrage.

Ajouter un launcher se résume à l'enregistrer ici (ou depuis un module externe via
:func:`register`) : ni le gestionnaire de processus, ni l'API, ni le frontend n'ont
à connaître son existence à l'avance.
"""

from __future__ import annotations

from msm.exceptions import LaunchError
from msm.i18n import tr
from msm.launchers.base import LaunchContext, Launcher, ProcessSpec
from msm.launchers.batch import BatchLauncher
from msm.launchers.custom import CustomLauncher
from msm.launchers.jar import JarLauncher
from msm.launchers.shell import ShellLauncher

_REGISTRY: dict[str, Launcher] = {}


def register(launcher: Launcher, *, replace: bool = False) -> None:
    """Enregistre un launcher sous sa clé."""
    if launcher.key in _REGISTRY and not replace:
        raise ValueError(f"A launcher is already registered under the key {launcher.key!r}.")
    _REGISTRY[launcher.key] = launcher


def get(key: str) -> Launcher:
    """Récupère un launcher par sa clé."""
    try:
        return _REGISTRY[key]
    except KeyError:
        known = ", ".join(sorted(_REGISTRY)) or tr("none")
        raise LaunchError(
            tr("Unknown start method."),
            cause=tr("“{key}” matches no registered start method.", key=key),
            remediation=tr("Choose one of the available methods: {methods}.", methods=known),
        ) from None


def all_launchers() -> tuple[Launcher, ...]:
    """Tous les launchers enregistrés, triés par clé."""
    return tuple(_REGISTRY[key] for key in sorted(_REGISTRY))


def describe_all() -> list[dict[str, str | None]]:
    """Description des launchers pour l'interface (avec raison d'indisponibilité)."""
    return [
        {
            "key": launcher.key,
            "label": tr(launcher.label),
            "description": tr(launcher.description),
            "unavailable_reason": launcher.is_available(),
        }
        for launcher in all_launchers()
    ]


def build_spec(key: str, ctx: LaunchContext) -> ProcessSpec:
    """Raccourci : résout le launcher et construit la commande."""
    return get(key).build_spec(ctx)


for _launcher in (JarLauncher(), ShellLauncher(), BatchLauncher(), CustomLauncher()):
    register(_launcher)

__all__ = [
    "LaunchContext",
    "Launcher",
    "ProcessSpec",
    "all_launchers",
    "build_spec",
    "describe_all",
    "get",
    "register",
]
