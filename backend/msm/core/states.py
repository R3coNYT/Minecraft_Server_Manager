"""Machine à états du cycle de vie d'un serveur Minecraft.

Les transitions autorisées sont déclarées explicitement : une transition illégale
lève une exception plutôt que de corrompre l'état en silence. C'est ce qui permet
de faire confiance à l'état affiché dans l'interface.

::

                      ┌──────────── start() ───────────┐
                      ▼                                │
    OFFLINE ──▶ STARTING ──(« Done (x.xxxs)! »)──▶ ONLINE
       ▲            │                                  │
       │            │ sortie inattendue                │ stop()
       │            ▼                                  ▼
       │         CRASHED ◀──── sortie non demandée ── STOPPING
       │            │                                  │
       └── reset ───┘◀──── sortie demandée, code 0 ────┘

``UNKNOWN`` est l'état d'un serveur réadopté après un redémarrage de MSM : le
processus est vivant mais les tubes d'entrée/sortie ont été perdus.
"""

from __future__ import annotations

from enum import Enum

from msm.exceptions import InvalidStateTransition


class ServerState(str, Enum):
    """État courant d'un serveur géré."""

    OFFLINE = "OFFLINE"
    STARTING = "STARTING"
    ONLINE = "ONLINE"
    STOPPING = "STOPPING"
    CRASHED = "CRASHED"
    UNKNOWN = "UNKNOWN"

    @property
    def is_running(self) -> bool:
        """Un processus est-il censé exister pour cet état ?"""
        return self in _RUNNING_STATES

    @property
    def is_transitional(self) -> bool:
        """L'état est-il en train de changer (démarrage/arrêt en cours) ?"""
        return self in (ServerState.STARTING, ServerState.STOPPING)

    @property
    def accepts_commands(self) -> bool:
        """Peut-on raisonnablement envoyer une commande console ?"""
        return self in (ServerState.ONLINE, ServerState.STARTING)


_RUNNING_STATES = frozenset(
    {
        ServerState.STARTING,
        ServerState.ONLINE,
        ServerState.STOPPING,
        ServerState.UNKNOWN,
    }
)

#: Transitions autorisées. Toute paire absente est un bug de logique.
ALLOWED_TRANSITIONS: dict[ServerState, frozenset[ServerState]] = {
    ServerState.OFFLINE: frozenset({ServerState.STARTING, ServerState.UNKNOWN}),
    ServerState.STARTING: frozenset(
        {
            ServerState.ONLINE,
            ServerState.STOPPING,
            ServerState.CRASHED,
            ServerState.OFFLINE,
            ServerState.UNKNOWN,
        }
    ),
    ServerState.ONLINE: frozenset(
        {
            ServerState.STOPPING,
            ServerState.CRASHED,
            ServerState.OFFLINE,  # arrêt déclenché hors panel (« /stop » en jeu)
            ServerState.UNKNOWN,
        }
    ),
    ServerState.STOPPING: frozenset(
        {ServerState.OFFLINE, ServerState.CRASHED, ServerState.UNKNOWN}
    ),
    ServerState.CRASHED: frozenset({ServerState.STARTING, ServerState.OFFLINE}),
    # Un serveur réadopté peut rejoindre n'importe quel état une fois diagnostiqué.
    ServerState.UNKNOWN: frozenset(
        {
            ServerState.OFFLINE,
            ServerState.STARTING,
            ServerState.ONLINE,
            ServerState.STOPPING,
            ServerState.CRASHED,
        }
    ),
}


def can_transition(current: ServerState, target: ServerState) -> bool:
    """Indique si le passage de ``current`` à ``target`` est légal."""
    if current is target:
        return True
    return target in ALLOWED_TRANSITIONS[current]


def assert_transition(current: ServerState, target: ServerState, *, server: str = "") -> None:
    """Valide une transition, ou lève :class:`InvalidStateTransition`."""
    if can_transition(current, target):
        return
    label = f" du serveur « {server} »" if server else ""
    raise InvalidStateTransition(
        f"Transition d'état impossible{label} : {current.value} → {target.value}.",
        cause=(
            f"Depuis l'état {current.value}, seuls "
            f"{', '.join(sorted(s.value for s in ALLOWED_TRANSITIONS[current]))} sont atteignables."
        ),
        remediation="Attendre la fin de l'opération en cours avant de relancer l'action.",
        context={"current": current.value, "target": target.value},
    )
