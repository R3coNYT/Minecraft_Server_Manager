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
    "stop": "Le serveur va s'arrêter et tous les joueurs connectés seront déconnectés.",
    "restart": "Le serveur va redémarrer et tous les joueurs seront déconnectés.",
    "kill": "Les entités ciblées seront tuées, sans possibilité d'annulation.",
    "op": "Le joueur obtiendra les pleins pouvoirs administrateur sur le serveur.",
    "deop": "Le joueur perdra ses droits administrateur.",
    "ban": "Le joueur ne pourra plus se connecter au serveur.",
    "ban-ip": "Toutes les connexions depuis cette adresse IP seront bloquées.",
    "whitelist": "L'accès au serveur va être restreint ou ouvert.",
    "reload": "Le rechargement à chaud peut déstabiliser les plugins et corrompre des données.",
    "gamerule": "Une règle de jeu du monde va être modifiée durablement.",
    "save-off": "Les sauvegardes automatiques seront désactivées : risque de perte de données.",
    "difficulty": "La difficulté du monde va changer pour tous les joueurs.",
    "worldborder": "La bordure du monde va être modifiée pour tous les joueurs.",
    "forceload": (
        "Des chunks vont être maintenus chargés en permanence (impact sur les performances)."
    ),
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
    explanation = _EXPLANATIONS.get(verb)
    if explanation is None:
        explanation = (
            "Cette commande modifie durablement l'état du serveur."
            if level is DangerLevel.SENSITIVE
            else "Cette commande est irréversible et affecte tous les joueurs."
        )
    if level is DangerLevel.DESTRUCTIVE and _BROAD_SELECTOR_RE.search(command):
        explanation += " Elle vise l'ensemble des joueurs connectés."
    return explanation


def requires_confirmation(command: str) -> bool:
    """Une confirmation explicite du client est-elle nécessaire ?"""
    return classify(command) is not DangerLevel.SAFE


def requires_strong_confirmation(command: str) -> bool:
    """L'utilisateur doit-il ressaisir le nom du serveur pour valider ?"""
    return classify(command) is DangerLevel.DESTRUCTIVE
