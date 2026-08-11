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
            "label": self.label,
            "type": self.type,
            "required": self.required,
            "default": self.default,
            "placeholder": self.placeholder,
            "help": self.help,
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
            "label": self.label,
            "description": self.description,
            "danger": self.danger.name,
            "fields": [item.to_dict() for item in self.fields],
        }

    # ---------------------------------------------------------------- #
    @staticmethod
    def _text(params: dict[str, Any], name: str, *, label: str, max_length: int = 512) -> str:
        value = str(params.get(name, "") or "").strip()
        if not value:
            raise ValidationError(
                f"{label} manquant.",
                cause=f"Le champ « {label} » est vide.",
                remediation=f"Saisir {label.lower()}.",
            )
        if len(value) > max_length:
            raise ValidationError(
                f"{label} trop long.",
                cause=f"{len(value)} caractères pour un maximum de {max_length}.",
                remediation="Raccourcir le texte.",
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
    label = "Message global"
    description = "Affiche un message dans le chat de tous les joueurs connectés."
    fields = (Field("message", "Message", "text", placeholder="Bonjour à tous !"),)

    def validate(self, params: dict[str, Any]) -> dict[str, Any]:
        return {"message": self._text(params, "message", label="Message")}

    def describe(self, params: dict[str, Any]) -> str:
        return f"Message global : « {params['message']} »"

    async def execute(self, ctx: ExecutionContext, params: dict[str, Any]) -> ActionResult:
        sent = await ctx.send(commands.build_say(params["message"]))
        return ActionResult(summary=self.describe(params), commands=(sent,))


class TitleAction(Action):
    """Grand texte affiché au centre de l'écran."""

    key = "title"
    label = "Titre à l'écran"
    description = "Affiche un grand texte, avec un sous-titre facultatif."
    fields = (
        Field("title", "Titre", "text", placeholder="ÉVÉNEMENT"),
        Field(
            "subtitle",
            "Sous-titre",
            "text",
            required=False,
            placeholder="L'événement commence !",
        ),
        Field("target", "Cible", "target", required=False, default="@a"),
        Field(
            "fade_in",
            "Apparition (ticks)",
            "number",
            required=False,
            default=10,
            minimum=0,
            maximum=200,
        ),
        Field(
            "stay", "Durée (ticks)", "number", required=False, default=70, minimum=0, maximum=600
        ),
        Field(
            "fade_out",
            "Disparition (ticks)",
            "number",
            required=False,
            default=20,
            minimum=0,
            maximum=200,
        ),
    )

    def validate(self, params: dict[str, Any]) -> dict[str, Any]:
        clean: dict[str, Any] = {
            "title": self._text(params, "title", label="Titre", max_length=256),
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
        detail = f" / « {subtitle} »" if subtitle else ""
        return f"Titre à l'écran : « {params['title']} »{detail}"

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
    label = "Barre d'action"
    description = "Affiche un message court au-dessus de la barre d'objets."
    fields = (
        Field("message", "Message", "text", placeholder="Plus que 5 minutes !"),
        Field("target", "Cible", "target", required=False, default="@a"),
    )

    def validate(self, params: dict[str, Any]) -> dict[str, Any]:
        return {
            "message": self._text(params, "message", label="Message", max_length=256),
            "target": commands.validate_target(str(params.get("target") or "@a")),
        }

    def describe(self, params: dict[str, Any]) -> str:
        return f"Barre d'action : « {params['message']} »"

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
    label = "Donner un objet"
    description = "Donne un objet à un joueur ou à tous les joueurs connectés."
    fields = (
        Field("item", "Objet", "text", placeholder="diamond", help="Identifiant Minecraft."),
        Field("count", "Quantité", "number", default=1, minimum=1, maximum=6400),
        Field("target", "Cible", "target", required=False, default="@a"),
    )

    def validate(self, params: dict[str, Any]) -> dict[str, Any]:
        return {
            "item": commands.validate_resource(str(params.get("item", "")), kind="objet"),
            "count": commands.validate_count(_positive_int(params.get("count", 1), "count")),
            "target": commands.validate_target(str(params.get("target") or "@a")),
        }

    def describe(self, params: dict[str, Any]) -> str:
        # Le signe multiplié est volontaire : il se lit mieux que la lettre x
        # dans une liste d'étapes.
        return f"Donner {params['count']} × {params['item']} à {params['target']}"  # noqa: RUF001

    async def execute(self, ctx: ExecutionContext, params: dict[str, Any]) -> ActionResult:
        sent = await ctx.send(
            commands.build_give(params["target"], params["item"], params["count"])
        )
        return ActionResult(summary=self.describe(params), commands=(sent,))


class TeleportAction(Action):
    """Téléportation vers un joueur ou des coordonnées."""

    key = "teleport"
    label = "Téléporter"
    description = "Téléporte des joueurs vers un autre joueur ou vers des coordonnées."
    fields = (
        Field("target", "Qui déplacer", "target", default="@a"),
        Field(
            "destination",
            "Destination",
            "text",
            placeholder="Flavien ou 100 64 -200",
            help="Pseudo, sélecteur, ou trois coordonnées séparées par des espaces.",
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
                    "Destination invalide.",
                    cause=f"« {raw} » ne ressemble ni à un pseudo ni à des coordonnées.",
                    remediation="Saisir un pseudo, ou trois nombres séparés par des espaces.",
                ) from None
            return {"target": target, "coordinates": coordinates}

        return {"target": target, "destination": commands.validate_target(raw)}

    def describe(self, params: dict[str, Any]) -> str:
        if "coordinates" in params:
            x, y, z = params["coordinates"]
            return f"Téléporter {params['target']} en {x:g} {y:g} {z:g}"
        return f"Téléporter {params['target']} vers {params['destination']}"

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
    label = "Tuer"
    description = "Tue les cibles désignées. Irréversible."
    danger = DangerLevel.DESTRUCTIVE
    fields = (Field("target", "Cible", "target", default="@a"),)

    def validate(self, params: dict[str, Any]) -> dict[str, Any]:
        return {"target": commands.validate_target(str(params.get("target") or "@a"))}

    def describe(self, params: dict[str, Any]) -> str:
        return f"Tuer {params['target']}"

    async def execute(self, ctx: ExecutionContext, params: dict[str, Any]) -> ActionResult:
        sent = await ctx.send(commands.build_kill(params["target"]))
        return ActionResult(summary=self.describe(params), commands=(sent,))


# --------------------------------------------------------------------------- #
#  Actions de contrôle
# --------------------------------------------------------------------------- #
class DelayAction(Action):
    """Pause entre deux étapes."""

    key = "delay"
    label = "Attendre"
    description = "Marque une pause avant l'étape suivante."
    fields = (
        Field("seconds", "Durée (secondes)", "number", default=10, minimum=1, maximum=MAX_DELAY_S),
    )

    def validate(self, params: dict[str, Any]) -> dict[str, Any]:
        seconds = _positive_int(params.get("seconds", 10), "seconds", maximum=MAX_DELAY_S)
        return {"seconds": seconds}

    def describe(self, params: dict[str, Any]) -> str:
        seconds = params["seconds"]
        if seconds >= 60:
            return f"Attendre {seconds // 60} min {seconds % 60:02d} s"
        return f"Attendre {seconds} s"

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
    label = "Commande personnalisée"
    description = "Exécute une commande console arbitraire."
    fields = (
        Field(
            "command",
            "Commande",
            "text",
            placeholder="weather clear",
            help="Sans le / initial. Le niveau de risque est déduit de la commande.",
        ),
    )

    def validate(self, params: dict[str, Any]) -> dict[str, Any]:
        return {"command": commands.sanitize_command(str(params.get("command", "")))}

    def describe(self, params: dict[str, Any]) -> str:
        return f"Commande : {params['command']}"

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
            "Valeur numérique attendue.",
            cause=f"« {value} » n'est pas un nombre entier ({name}).",
            remediation="Saisir un nombre entier.",
        ) from None

    if number < 0 or number > maximum:
        raise ValidationError(
            "Valeur hors limites.",
            cause=f"{number} n'est pas compris entre 0 et {maximum} ({name}).",
            remediation=f"Saisir une valeur entre 0 et {maximum}.",
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
