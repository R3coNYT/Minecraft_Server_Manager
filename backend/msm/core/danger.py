"""Classification des commandes console selon leur dangerosité.

La console permet, par nature, d'exécuter n'importe quoi. Plutôt que de tenter une
liste blanche impossible à maintenir, MSM classe les commandes en trois niveaux et
adapte l'exigence : permission requise, confirmation explicite, ou double
confirmation.

La classification porte sur le **verbe normalisé**, jamais sur une recherche de
sous-chaîne : ``say attention je vais stop le serveur`` est un message anodin et
doit le rester.
"""

from __future__ import annotations

import re
from enum import IntEnum

from msm.core.commands import command_verb
from msm.i18n import tr

#: Sélecteurs qui visent l'ensemble des joueurs.
_BROAD_SELECTOR_RE = re.compile(r"@[ae](\[|\b)")


class DangerLevel(IntEnum):
    """Niveau de risque d'une commande."""

    SAFE = 0
    #: Modifie durablement la configuration, les droits ou l'état du serveur.
    SENSITIVE = 1
    #: Irréversible ou impactant tous les joueurs simultanément.
    DESTRUCTIVE = 2


#: Verbes modifiant les droits, la persistance ou la configuration du serveur.
SENSITIVE_VERBS: frozenset[str] = frozenset(
    {
        "op",
        "deop",
        "ban",
        "ban-ip",
        "banip",
        "pardon",
        "pardon-ip",
        "whitelist",
        "save-all",
        "save-off",
        "save-on",
        "gamerule",
        "difficulty",
        "defaultgamemode",
        "setworldspawn",
        "setidletimeout",
        "reload",
        "datapack",
        "worldborder",
        "debug",
        "perf",
        "jfr",
        "publish",
        "forceload",
    }
)

#: Verbes dont l'exécution est irréversible ou coupe le service.
DESTRUCTIVE_VERBS: frozenset[str] = frozenset(
    {
        "stop",
        "restart",
        "end",
        "shutdown",
        "kill",
        "deleteworld",
    }
)

#: Explications affichées dans la boîte de confirmation, par verbe.
_EXPLANATIONS: dict[str, str] = {
    "stop": "The server will stop and every connected player will be disconnected.",
    "restart": "The server will restart and every player will be disconnected.",
    "kill": "The targeted entities will be killed, with no way to undo it.",
    "op": "The player will get full administrator powers on the server.",
    "deop": "The player will lose their administrator rights.",
    "ban": "The player will no longer be able to join the server.",
    "ban-ip": "Every connection from this IP address will be blocked.",
    "whitelist": "Access to the server is about to be restricted or opened.",
    "reload": "A hot reload can destabilise plugins and corrupt data.",
    "gamerule": "A game rule of the world is about to change permanently.",
    "save-off": "Automatic saving will be disabled: risk of data loss.",
    "difficulty": "The world difficulty will change for every player.",
    "worldborder": "The world border will change for every player.",
    "forceload": "Chunks will be kept loaded permanently (performance impact).",
}


def classify(command: str) -> DangerLevel:
    """Renvoie le niveau de danger d'une commande console."""
    verb = command_verb(command)
    if not verb:
        return DangerLevel.SAFE

    if verb in DESTRUCTIVE_VERBS:
        return DangerLevel.DESTRUCTIVE

    if verb in SENSITIVE_VERBS:
        # Une commande sensible visant *tous* les joueurs devient destructrice :
        # `ban @a` n'a pas la même portée que `ban Flavien`.
        arguments = command[len(verb) :] if command.casefold().startswith(verb) else command
        if _BROAD_SELECTOR_RE.search(arguments):
            return DangerLevel.DESTRUCTIVE
        return DangerLevel.SENSITIVE

    return DangerLevel.SAFE


def explain(command: str) -> str | None:
    """Message d'avertissement à afficher avant confirmation, s'il y a lieu."""
    level = classify(command)
    if level is DangerLevel.SAFE:
        return None

    verb = command_verb(command)
    template = _EXPLANATIONS.get(verb)
    if template is None:
        template = (
            "This command permanently changes the server state."
            if level is DangerLevel.SENSITIVE
            else "This command cannot be undone and affects every player."
        )
    explanation = tr(template)
    if level is DangerLevel.DESTRUCTIVE and _BROAD_SELECTOR_RE.search(command):
        explanation += " " + tr("It targets every connected player.")
    return explanation


def requires_confirmation(command: str) -> bool:
    """Une confirmation explicite du client est-elle nécessaire ?"""
    return classify(command) is not DangerLevel.SAFE


def requires_strong_confirmation(command: str) -> bool:
    """L'utilisateur doit-il ressaisir le nom du serveur pour valider ?"""
    return classify(command) is DangerLevel.DESTRUCTIVE
