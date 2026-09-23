"""Actions exécutables par le moteur d'événements.

Une action traduit des paramètres saisis dans l'interface en **commandes
Minecraft**, sans jamais construire de chaîne à la main : tout passe par les
constructeurs déjà éprouvés de :mod:`msm.core.commands`, qui refusent les sauts
de ligne et valident pseudos, objets et quantités.

Chaque action déclare son formulaire (``fields``) : le frontend construit
l'interface à partir de cette description, sans rien savoir des actions à
l'avance. Ajouter une action revient donc à écrire une classe et à l'enregistrer.

Les actions ne connaissent ni le runtime, ni la base de données : elles reçoivent
un :class:`ExecutionContext` qui sait envoyer une commande et attendre. C'est ce
qui permet de les tester sans serveur.
"""

from __future__ import annotations

import asyncio
import json
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, ClassVar

from msm.core import commands
from msm.core.danger import DangerLevel, classify
from msm.exceptions import ValidationError
from msm.i18n import tr

#: Durée maximale d'une attente, pour qu'un événement mal saisi ne bloque pas une
#: exécution pendant des jours.
MAX_DELAY_S = 6 * 3600


@dataclass(slots=True)
class ExecutionContext:
    """Ce dont une action a besoin pour s'exécuter."""

    server_name: str
    actor: str
    #: Envoie une commande à la console et renvoie la commande réellement émise.
    send: Callable[[str], Awaitable[str]]
    #: Attente interruptible ; injectable pour que les tests n'attendent pas.
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep


@dataclass(frozen=True, slots=True)
class ActionResult:
    """Trace de ce qu'une action a réellement fait."""

    summary: str
    commands: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {"summary": self.summary, "commands": list(self.commands)}


@dataclass(frozen=True, slots=True)
class Field:
    """Description d'un champ de formulaire, à destination du frontend."""

    name: str
    label: str
    type: str = "text"
    required: bool = True
    default: Any = None
    placeholder: str = ""
    help: str = ""
    minimum: int | None = None
    maximum: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "label": tr(self.label),
            "type": self.type,
            "required": self.required,
            "default": self.default,
            "placeholder": tr(self.placeholder) if self.placeholder else "",
            "help": tr(self.help) if self.help else "",
            "minimum": self.minimum,
            "maximum": self.maximum,
        }


class Action(ABC):
    """Une étape d'événement."""

    key: ClassVar[str]
    label: ClassVar[str]
    description: ClassVar[str] = ""
    fields: ClassVar[tuple[Field, ...]] = ()
    #: Niveau de risque : conditionne la permission et la confirmation exigées.
    danger: ClassVar[DangerLevel] = DangerLevel.SAFE

    @abstractmethod
    def validate(self, params: dict[str, Any]) -> dict[str, Any]:
        """Valide et normalise les paramètres. Lève :class:`ValidationError` sinon."""

    @abstractmethod
    def describe(self, params: dict[str, Any]) -> str:
        """Résumé lisible, affiché avant exécution et dans l'audit."""

    @abstractmethod
    async def execute(self, ctx: ExecutionContext, params: dict[str, Any]) -> ActionResult:
        """Exécute l'action. Les paramètres ont déjà été validés."""

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": tr(self.label),
            "description": tr(self.description) if self.description else "",
            "danger": self.danger.name,
            "fields": [item.to_dict() for item in self.fields],
        }

    # ---------------------------------------------------------------- #
    @staticmethod
    def _text(params: dict[str, Any], name: str, *, label: str, max_length: int = 512) -> str:
        value = str(params.get(name, "") or "").strip()
        if not value:
            raise ValidationError(
                tr("Missing value: {label}.", label=tr(label)),
                cause=tr("The field “{label}” is empty.", label=tr(label)),
                remediation=tr("Fill in the field “{label}”.", label=tr(label)),
            )
        if len(value) > max_length:
            raise ValidationError(
                tr("Too long: {label}.", label=tr(label)),
                cause=tr(
                    "{length} characters for a maximum of {maximum}.",
                    length=len(value),
                    maximum=max_length,
                ),
                remediation=tr("Shorten the text."),
            )
        return value


def _json_text(message: str) -> str:
    """Encode un texte en composant JSON Minecraft.

    L'encodage passe par ``json.dumps`` plutôt que par une concaténation : un
    guillemet ou une contre-oblique dans le message casserait sinon la commande,
    et un texte bien choisi pourrait en modifier le sens.
    """
    return json.dumps({"text": message}, ensure_ascii=False)


# --------------------------------------------------------------------------- #
#  Actions de communication
# --------------------------------------------------------------------------- #
class SayAction(Action):
    """Message diffusé dans le chat de tous les joueurs."""

    key = "say"
    label = "Broadcast message"
    description = "Shows a message in the chat of every connected player."
    fields = (Field("message", "Message", "text", placeholder="Hello everyone!"),)

    def validate(self, params: dict[str, Any]) -> dict[str, Any]:
        return {"message": self._text(params, "message", label="Message")}

    def describe(self, params: dict[str, Any]) -> str:
        return tr("Broadcast message: “{message}”", message=params["message"])

    async def execute(self, ctx: ExecutionContext, params: dict[str, Any]) -> ActionResult:
        sent = await ctx.send(commands.build_say(params["message"]))
        return ActionResult(summary=self.describe(params), commands=(sent,))


class TitleAction(Action):
    """Grand texte affiché au centre de l'écran."""

    key = "title"
    label = "On-screen title"
    description = "Shows large text, with an optional subtitle."
    fields = (
        Field("title", "Title", "text", placeholder="EVENT"),
        Field(
            "subtitle",
            "Subtitle",
            "text",
            required=False,
            placeholder="The event is starting!",
        ),
        Field("target", "Target", "target", required=False, default="@a"),
        Field(
            "fade_in",
            "Fade in (ticks)",
            "number",
            required=False,
            default=10,
            minimum=0,
            maximum=200,
        ),
        Field("stay", "Stay (ticks)", "number", required=False, default=70, minimum=0, maximum=600),
        Field(
            "fade_out",
            "Fade out (ticks)",
            "number",
            required=False,
            default=20,
            minimum=0,
            maximum=200,
        ),
    )

    def validate(self, params: dict[str, Any]) -> dict[str, Any]:
        clean: dict[str, Any] = {
            "title": self._text(params, "title", label="Title", max_length=256),
            "target": commands.validate_target(str(params.get("target") or "@a")),
        }
        subtitle = str(params.get("subtitle", "") or "").strip()
        if subtitle:
            clean["subtitle"] = subtitle

        for name, default in (("fade_in", 10), ("stay", 70), ("fade_out", 20)):
            clean[name] = _positive_int(params.get(name, default), name, maximum=600)
        return clean

    def describe(self, params: dict[str, Any]) -> str:
        subtitle = params.get("subtitle")
        if subtitle:
            return tr(
                "On-screen title: “{title}” / “{subtitle}”",
                title=params["title"],
                subtitle=subtitle,
            )
        return tr("On-screen title: “{title}”", title=params["title"])

    async def execute(self, ctx: ExecutionContext, params: dict[str, Any]) -> ActionResult:
        target = params["target"]
        sent: list[str] = []

        # Les durées d'affichage sont réglées avant le titre : appliquées après,
        # elles ne prendraient effet qu'au titre suivant.
        sent.append(
            await ctx.send(
                commands.sanitize_command(
                    f"title {target} times {params['fade_in']} {params['stay']} "
                    f"{params['fade_out']}"
                )
            )
        )
        if subtitle := params.get("subtitle"):
            sent.append(
                await ctx.send(
                    commands.sanitize_command(f"title {target} subtitle {_json_text(subtitle)}")
                )
            )
        sent.append(
            await ctx.send(
                commands.sanitize_command(f"title {target} title {_json_text(params['title'])}")
            )
        )
        return ActionResult(summary=self.describe(params), commands=tuple(sent))


class ActionBarAction(Action):
    """Message discret affiché au-dessus de la barre d'objets."""

    key = "actionbar"
    label = "Action bar"
    description = "Shows a short message above the hotbar."
    fields = (
        Field("message", "Message", "text", placeholder="Only 5 minutes left!"),
        Field("target", "Target", "target", required=False, default="@a"),
    )

    def validate(self, params: dict[str, Any]) -> dict[str, Any]:
        return {
            "message": self._text(params, "message", label="Message", max_length=256),
            "target": commands.validate_target(str(params.get("target") or "@a")),
        }

    def describe(self, params: dict[str, Any]) -> str:
        return tr("Action bar: “{message}”", message=params["message"])

    async def execute(self, ctx: ExecutionContext, params: dict[str, Any]) -> ActionResult:
        sent = await ctx.send(
            commands.sanitize_command(
                f"title {params['target']} actionbar {_json_text(params['message'])}"
            )
        )
        return ActionResult(summary=self.describe(params), commands=(sent,))


# --------------------------------------------------------------------------- #
#  Actions sur les joueurs
# --------------------------------------------------------------------------- #
class GiveAction(Action):
    """Distribution d'un objet."""

    key = "give"
    label = "Give an item"
    description = "Gives an item to one player or to every connected player."
    fields = (
        Field("item", "Item", "text", placeholder="diamond", help="Minecraft identifier."),
        Field("count", "Quantity", "number", default=1, minimum=1, maximum=6400),
        Field("target", "Target", "target", required=False, default="@a"),
    )

    def validate(self, params: dict[str, Any]) -> dict[str, Any]:
        return {
            "item": commands.validate_resource(str(params.get("item", "")), kind="item"),
            "count": commands.validate_count(_positive_int(params.get("count", 1), "count")),
            "target": commands.validate_target(str(params.get("target") or "@a")),
        }

    def describe(self, params: dict[str, Any]) -> str:
        # Le signe multiplié est volontaire : il se lit mieux que la lettre x
        # dans une liste d'étapes.
        return tr(
            "Give {count} × {item} to {target}",  # noqa: RUF001
            count=params["count"],
            item=params["item"],
            target=params["target"],
        )

    async def execute(self, ctx: ExecutionContext, params: dict[str, Any]) -> ActionResult:
        sent = await ctx.send(
            commands.build_give(params["target"], params["item"], params["count"])
        )
        return ActionResult(summary=self.describe(params), commands=(sent,))


class TeleportAction(Action):
    """Téléportation vers un joueur ou des coordonnées."""

    key = "teleport"
    label = "Teleport"
    description = "Teleports players to another player or to coordinates."
    fields = (
        Field("target", "Who to move", "target", default="@a"),
        Field(
            "destination",
            "Destination",
            "text",
            placeholder="Steve or 100 64 -200",
            help="Username, selector, or three coordinates separated by spaces.",
        ),
    )

    def validate(self, params: dict[str, Any]) -> dict[str, Any]:
        target = commands.validate_target(str(params.get("target") or "@a"))
        raw = str(params.get("destination", "") or "").strip()

        parts = raw.split()
        if len(parts) == 3:
            try:
                coordinates = [float(part) for part in parts]
            except ValueError:
                raise ValidationError(
                    tr("Invalid destination."),
                    cause=tr("“{value}” looks like neither a username nor coordinates.", value=raw),
                    remediation=tr("Enter a username, or three numbers separated by spaces."),
                ) from None
            return {"target": target, "coordinates": coordinates}

        return {"target": target, "destination": commands.validate_target(raw)}

    def describe(self, params: dict[str, Any]) -> str:
        if "coordinates" in params:
            x, y, z = params["coordinates"]
            return tr(
                "Teleport {target} to {x} {y} {z}",
                target=params["target"],
                x=f"{x:g}",
                y=f"{y:g}",
                z=f"{z:g}",
            )
        return tr(
            "Teleport {target} to {destination}",
            target=params["target"],
            destination=params["destination"],
        )

    async def execute(self, ctx: ExecutionContext, params: dict[str, Any]) -> ActionResult:
        if "coordinates" in params:
            x, y, z = params["coordinates"]
            command = commands.build_teleport_coords(params["target"], x, y, z)
        else:
            command = commands.build_teleport(params["target"], params["destination"])
        sent = await ctx.send(command)
        return ActionResult(summary=self.describe(params), commands=(sent,))


class KillAction(Action):
    """Élimination de joueurs ou d'entités."""

    key = "kill"
    label = "Kill"
    description = "Kills the given targets. Cannot be undone."
    danger = DangerLevel.DESTRUCTIVE
    fields = (Field("target", "Target", "target", default="@a"),)

    def validate(self, params: dict[str, Any]) -> dict[str, Any]:
        return {"target": commands.validate_target(str(params.get("target") or "@a"))}

    def describe(self, params: dict[str, Any]) -> str:
        return tr("Kill {target}", target=params["target"])

    async def execute(self, ctx: ExecutionContext, params: dict[str, Any]) -> ActionResult:
        sent = await ctx.send(commands.build_kill(params["target"]))
        return ActionResult(summary=self.describe(params), commands=(sent,))


# --------------------------------------------------------------------------- #
#  Actions de contrôle
# --------------------------------------------------------------------------- #
class DelayAction(Action):
    """Pause entre deux étapes."""

    key = "delay"
    label = "Wait"
    description = "Pauses before the next step."
    fields = (
        Field(
            "seconds", "Duration (seconds)", "number", default=10, minimum=1, maximum=MAX_DELAY_S
        ),
    )

    def validate(self, params: dict[str, Any]) -> dict[str, Any]:
        seconds = _positive_int(params.get("seconds", 10), "seconds", maximum=MAX_DELAY_S)
        return {"seconds": seconds}

    def describe(self, params: dict[str, Any]) -> str:
        seconds = params["seconds"]
        if seconds >= 60:
            return tr(
                "Wait {minutes} min {seconds} s",
                minutes=seconds // 60,
                seconds=f"{seconds % 60:02d}",
            )
        return tr("Wait {seconds} s", seconds=seconds)

    async def execute(self, ctx: ExecutionContext, params: dict[str, Any]) -> ActionResult:
        await ctx.sleep(float(params["seconds"]))
        return ActionResult(summary=self.describe(params))


class CommandAction(Action):
    """Commande console libre.

    Son niveau de risque est calculé à partir de la commande elle-même : un
    ``say`` reste anodin, un ``stop`` exige la permission des actions
    destructrices. Le classer arbitrairement en « dangereux » aurait poussé à
    accorder cette permission largement, donc à la vider de son sens.
    """

    key = "command"
    label = "Custom command"
    description = "Runs an arbitrary console command."
    fields = (
        Field(
            "command",
            "Command",
            "text",
            placeholder="weather clear",
            help="Without the leading /. The risk level is inferred from the command.",
        ),
    )

    def validate(self, params: dict[str, Any]) -> dict[str, Any]:
        return {"command": commands.sanitize_command(str(params.get("command", "")))}

    def describe(self, params: dict[str, Any]) -> str:
        return tr("Command: {command}", command=params["command"])

    def danger_for(self, params: dict[str, Any]) -> DangerLevel:
        return classify(params.get("command", ""))

    async def execute(self, ctx: ExecutionContext, params: dict[str, Any]) -> ActionResult:
        sent = await ctx.send(params["command"])
        return ActionResult(summary=self.describe(params), commands=(sent,))


# --------------------------------------------------------------------------- #
def _positive_int(value: Any, name: str, *, maximum: int = 10_000) -> int:
    """Convertit une valeur de formulaire en entier positif."""
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise ValidationError(
            tr("Numeric value expected."),
            cause=tr("“{value}” is not a whole number ({name}).", value=value, name=name),
            remediation=tr("Enter a whole number."),
        ) from None

    if number < 0 or number > maximum:
        raise ValidationError(
            tr("Value out of range."),
            cause=tr(
                "{number} is not between 0 and {maximum} ({name}).",
                number=number,
                maximum=maximum,
                name=name,
            ),
            remediation=tr("Enter a value between 0 and {maximum}.", maximum=maximum),
        )
    return number


BUILTIN_ACTIONS: tuple[Action, ...] = (
    SayAction(),
    TitleAction(),
    ActionBarAction(),
    GiveAction(),
    TeleportAction(),
    KillAction(),
    DelayAction(),
    CommandAction(),
)
