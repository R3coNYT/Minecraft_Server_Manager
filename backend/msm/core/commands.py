"""Construction et assainissement des commandes Minecraft.

Point critique de sécurité : une commande est écrite sur **l'entrée standard** du
serveur, où le séparateur d'instructions est le saut de ligne. Laisser passer un
``\\n`` permettrait donc de faire exécuter dix commandes là où l'utilisateur n'est
autorisé qu'à en soumettre une — et une seule serait auditée. Tout caractère de
contrôle est donc rejeté, jamais « nettoyé silencieusement ».
"""

from __future__ import annotations

import re

from msm.exceptions import UnsafeCommandError, ValidationError
from msm.i18n import tr

#: Limite large, alignée sur celle du client Minecraft.
MAX_COMMAND_LENGTH = 32_500

#: Pseudo Minecraft classique.
_USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{1,16}$")
#: Sélecteur de cible (`@a`, `@p[distance=..5]`, …).
_SELECTOR_RE = re.compile(r"^@[aprse](\[[^\]\n\r]*\])?$")
#: Identifiant d'objet / d'entité (`diamond`, `minecraft:diamond_sword`).
_RESOURCE_RE = re.compile(r"^[a-z0-9_.-]+(:[a-z0-9_./-]+)?$")
#: Caractères interdits : tout ce qui pourrait scinder ou tronquer la commande.
_FORBIDDEN_RE = re.compile(r"[\x00-\x08\x0a-\x1f\x7f]")


def sanitize_command(raw: str) -> str:
    """Valide une commande console et renvoie sa forme normalisée.

    * retire le ``/`` initial (la console serveur ne l'attend pas) ;
    * rejette sauts de ligne, octet nul et caractères de contrôle ;
    * rejette les commandes vides ou démesurées.
    """
    if not isinstance(raw, str):
        raise UnsafeCommandError(
            tr("Invalid command."),
            cause=tr("The command received is not a string."),
            remediation=tr("Send the command as text."),
        )

    # `\r` seul est toléré (fin de ligne Windows) mais retiré ; `\n` ne l'est pas.
    candidate = raw.replace("\r\n", "").replace("\r", "").strip()

    if not candidate:
        raise UnsafeCommandError(
            tr("Empty command."),
            cause=tr("Nothing to send to the server."),
            remediation=tr("Enter a command before submitting."),
        )

    if (bad := _FORBIDDEN_RE.search(candidate)) is not None:
        raise UnsafeCommandError(
            tr("Command refused: forbidden character."),
            cause=tr(
                "The command contains the control character 0x{code}, which would allow "
                "several commands to run in a single request.",
                code=f"{ord(bad.group()):02x}",
            ),
            remediation=tr("Send a single command, without line breaks."),
        )

    if len(candidate) > MAX_COMMAND_LENGTH:
        raise UnsafeCommandError(
            tr("Command too long."),
            cause=tr(
                "{length} characters for a maximum of {maximum}.",
                length=len(candidate),
                maximum=MAX_COMMAND_LENGTH,
            ),
            remediation=tr("Shorten the command."),
        )

    return candidate.removeprefix("/").strip() or _empty()


def _empty() -> str:
    raise UnsafeCommandError(
        tr("Empty command."),
        cause=tr("The command only contained a `/`."),
        remediation=tr("Enter a full command, for example `say Hello`."),
    )


def command_verb(command: str) -> str:
    """Renvoie le verbe normalisé d'une commande (``give Flavien …`` → ``give``).

    Le préfixe d'espace de noms est retiré (``minecraft:give`` → ``give``) : sans
    cela, la classification des commandes sensibles serait contournable.
    """
    stripped = command.lstrip("/ ").strip()
    if not stripped:
        return ""
    verb = stripped.split(maxsplit=1)[0].casefold()
    return verb.rsplit(":", 1)[-1]


# --------------------------------------------------------------------------- #
#  Validation des arguments (utilisée par les actions joueur et les événements)
# --------------------------------------------------------------------------- #
def validate_target(target: str, *, allow_selector: bool = True) -> str:
    """Valide un pseudo de joueur ou un sélecteur de cible."""
    value = target.strip()
    if _USERNAME_RE.match(value):
        return value
    if allow_selector and _SELECTOR_RE.match(value):
        return value
    raise ValidationError(
        tr("Invalid target."),
        cause=tr("“{target}” is neither a Minecraft username nor a valid selector.", target=target),
        remediation=(
            tr(
                "Use a username (1 to 16 characters: letters, digits and `_`) "
                "or a selector such as `@a`."
            )
            if allow_selector
            else tr("Use a username (1 to 16 characters: letters, digits and `_`).")
        ),
    )


def validate_resource(resource: str, *, kind: str = "identifier") -> str:
    """Valide un identifiant de ressource Minecraft (objet, effet, dimension…)."""
    value = resource.strip().casefold()
    if _RESOURCE_RE.match(value):
        return value
    raise ValidationError(
        tr("Invalid {kind}.", kind=tr(kind)),
        cause=tr(
            "“{resource}” does not follow the Minecraft identifier format.", resource=resource
        ),
        remediation=tr("Use an identifier such as `diamond` or `minecraft:diamond_sword`."),
    )


def validate_count(count: int, *, minimum: int = 1, maximum: int = 6400) -> int:
    """Valide une quantité d'objets."""
    if not isinstance(count, int) or isinstance(count, bool):
        raise ValidationError(
            tr("Invalid quantity."),
            cause=tr("The quantity must be a whole number."),
            remediation=tr(
                "Enter a whole number between {minimum} and {maximum}.",
                minimum=minimum,
                maximum=maximum,
            ),
        )
    if not minimum <= count <= maximum:
        raise ValidationError(
            tr("Quantity out of range."),
            cause=tr(
                "{count} is not between {minimum} and {maximum}.",
                count=count,
                minimum=minimum,
                maximum=maximum,
            ),
            remediation=tr(
                "Enter a quantity between {minimum} and {maximum}.",
                minimum=minimum,
                maximum=maximum,
            ),
        )
    return count


# --------------------------------------------------------------------------- #
#  Constructeurs de commandes
# --------------------------------------------------------------------------- #
def build_say(message: str) -> str:
    """``say <message>`` — message global diffusé dans le chat."""
    text = message.strip()
    if not text:
        raise ValidationError(
            tr("Empty message."),
            cause=tr("No text to broadcast."),
            remediation=tr("Enter the message to show to players."),
        )
    return sanitize_command(f"say {text}")


def build_give(target: str, item: str, count: int = 1) -> str:
    """``give <cible> <objet> <quantité>``."""
    return sanitize_command(
        f"give {validate_target(target)} {validate_resource(item, kind='item')} "
        f"{validate_count(count)}"
    )


def build_kick(target: str, reason: str = "") -> str:
    """``kick <joueur> [raison]``."""
    base = f"kick {validate_target(target, allow_selector=False)}"
    return sanitize_command(f"{base} {reason.strip()}" if reason.strip() else base)


def build_ban(target: str, reason: str = "") -> str:
    """``ban <joueur> [raison]``."""
    base = f"ban {validate_target(target, allow_selector=False)}"
    return sanitize_command(f"{base} {reason.strip()}" if reason.strip() else base)


def build_pardon(target: str) -> str:
    """``pardon <joueur>`` — levée de bannissement."""
    return sanitize_command(f"pardon {validate_target(target, allow_selector=False)}")


def build_op(target: str) -> str:
    return sanitize_command(f"op {validate_target(target, allow_selector=False)}")


def build_deop(target: str) -> str:
    return sanitize_command(f"deop {validate_target(target, allow_selector=False)}")


def build_kill(target: str) -> str:
    return sanitize_command(f"kill {validate_target(target)}")


def build_teleport(target: str, destination: str) -> str:
    """``tp <cible> <destination>`` où la destination est un joueur ou un sélecteur."""
    return sanitize_command(f"tp {validate_target(target)} {validate_target(destination)}")


def build_teleport_coords(target: str, x: float, y: float, z: float) -> str:
    """``tp <cible> <x> <y> <z>``."""
    for axis, value in (("x", x), ("y", y), ("z", z)):
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise ValidationError(
                tr("Invalid coordinate."),
                cause=tr("The {axis} coordinate must be numeric.", axis=axis),
                remediation=tr("Enter numeric coordinates."),
            )
    return sanitize_command(f"tp {validate_target(target)} {x:g} {y:g} {z:g}")
