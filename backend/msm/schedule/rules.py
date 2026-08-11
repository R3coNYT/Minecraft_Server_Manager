"""Quand une tâche programmée doit-elle se déclencher ?

Trois formes, volontairement, plutôt qu'une expression cron : « toutes les
6 heures », « chaque jour à 4 h », « lundi et jeudi à 3 h 30 ». Un `0 4 * * 1,4`
est illisible pour qui administre un serveur Minecraft sans être administrateur
système, et une erreur de saisie s'y voit rarement.

**Les heures sont locales.** Une sauvegarde « à 4 h du matin » doit rester à 4 h
du matin des deux côtés d'un changement d'heure — c'est le sens de la demande,
pas « toutes les 24 heures ». Le calcul passe donc par le fuseau de la règle, et
traite les deux anomalies annuelles :

* l'heure **qui n'existe pas** (printemps : 2 h 30 est sautée) — la tâche part au
  premier instant valide qui suit ;
* l'heure **qui existe deux fois** (automne : 2 h 30 revient) — la tâche part à la
  première des deux, et pas deux fois.

Ce module ne connaît ni base de données ni serveur : il transforme une règle et
un instant en instant suivant, et se teste comme tel.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from msm.exceptions import ValidationError

#: Un intervalle plus court n'a pas de sens pour les actions concernées
#: (sauvegarde, redémarrage) et saturerait la machine.
MIN_INTERVAL_MINUTES = 5
MAX_INTERVAL_MINUTES = 60 * 24 * 30

#: Jours de la semaine, du lundi au dimanche — la convention de `weekday()`.
DAY_LABELS: tuple[str, ...] = (
    "lundi",
    "mardi",
    "mercredi",
    "jeudi",
    "vendredi",
    "samedi",
    "dimanche",
)


class TriggerKind(str, Enum):
    """Forme de la règle."""

    INTERVAL = "INTERVAL"
    DAILY = "DAILY"
    WEEKLY = "WEEKLY"


@dataclass(frozen=True, slots=True)
class Rule:
    """Règle de déclenchement validée."""

    trigger: TriggerKind
    #: INTERVAL : minutes entre deux déclenchements.
    interval_minutes: int = 60
    #: DAILY et WEEKLY : heure locale.
    hour: int = 4
    minute: int = 0
    #: WEEKLY : jours concernés, 0 = lundi.
    days: tuple[int, ...] = ()
    timezone: str = "UTC"

    @property
    def zone(self) -> ZoneInfo:
        return _zone(self.timezone)

    def to_dict(self) -> dict[str, Any]:
        return {
            "trigger": self.trigger.value,
            "interval_minutes": self.interval_minutes,
            "hour": self.hour,
            "minute": self.minute,
            "days": list(self.days),
            "timezone": self.timezone,
        }


def _zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, KeyError) as exc:
        raise ValidationError(
            "Fuseau horaire inconnu.",
            cause=f"« {name} » n'est pas un identifiant de fuseau reconnu.",
            remediation="Utiliser un identifiant comme « Europe/Paris » ou « UTC ».",
        ) from exc


def parse_rule(raw: Any) -> Rule:
    """Valide une règle venant de l'interface ou de la base."""
    if not isinstance(raw, dict):
        raise ValidationError(
            "Règle de planification invalide.",
            cause="La règle doit être un objet décrivant le déclenchement.",
            remediation="Reconstruire la planification depuis l'interface.",
        )

    try:
        trigger = TriggerKind(str(raw.get("trigger", "")).upper())
    except ValueError as exc:
        raise ValidationError(
            "Type de déclenchement inconnu.",
            cause=f"« {raw.get('trigger')} » n'est pas un déclenchement reconnu.",
            remediation="Choisir « intervalle », « quotidien » ou « hebdomadaire ».",
        ) from exc

    timezone = str(raw.get("timezone") or "UTC")
    _zone(timezone)

    if trigger is TriggerKind.INTERVAL:
        minutes = _int(raw.get("interval_minutes"), label="Intervalle")
        if not MIN_INTERVAL_MINUTES <= minutes <= MAX_INTERVAL_MINUTES:
            raise ValidationError(
                "Intervalle hors limites.",
                cause=(
                    f"{minutes} minutes demandées, "
                    f"pour {MIN_INTERVAL_MINUTES} à {MAX_INTERVAL_MINUTES}."
                ),
                remediation=f"Choisir un intervalle d'au moins {MIN_INTERVAL_MINUTES} minutes.",
            )
        return Rule(trigger=trigger, interval_minutes=minutes, timezone=timezone)

    hour = _int(raw.get("hour"), label="Heure")
    minute = _int(raw.get("minute", 0), label="Minute")
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ValidationError(
            "Heure invalide.",
            cause=f"{hour:02d}:{minute:02d} n'est pas une heure valide.",
            remediation="Saisir une heure entre 00:00 et 23:59.",
        )

    if trigger is TriggerKind.DAILY:
        return Rule(trigger=trigger, hour=hour, minute=minute, timezone=timezone)

    raw_days = raw.get("days") or []
    if not isinstance(raw_days, list) or not raw_days:
        raise ValidationError(
            "Aucun jour sélectionné.",
            cause="Une planification hebdomadaire doit viser au moins un jour.",
            remediation="Cocher au moins un jour de la semaine.",
        )
    days = sorted({_int(day, label="Jour") for day in raw_days})
    if any(day < 0 or day > 6 for day in days):
        raise ValidationError(
            "Jour invalide.",
            cause="Les jours vont de 0 (lundi) à 6 (dimanche).",
            remediation="Reconstruire la planification depuis l'interface.",
        )
    return Rule(
        trigger=trigger,
        hour=hour,
        minute=minute,
        days=tuple(days),
        timezone=timezone,
    )


def _int(value: Any, *, label: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValidationError(
            f"{label} invalide.",
            cause=f"« {value} » n'est pas un nombre entier.",
            remediation=f"Saisir {label.lower()} sous forme de nombre.",
        ) from exc


def _at_local(day: datetime, rule: Rule) -> datetime:
    """Instant UTC correspondant à l'heure locale voulue, ce jour-là.

    Les deux anomalies du changement d'heure sont traitées ici. `fold=0` choisit
    la **première** des deux occurrences d'une heure doublée ; une heure inexistante
    se reconnaît à ce qu'elle ne survit pas à l'aller-retour vers UTC, et l'on
    prend alors le premier instant valide qui suit.
    """
    naive = day.replace(hour=rule.hour, minute=rule.minute, second=0, microsecond=0, fold=0)
    local = naive.replace(tzinfo=rule.zone)
    as_utc = local.astimezone(UTC)

    if as_utc.astimezone(rule.zone).replace(tzinfo=None) != naive:
        # L'heure demandée n'existe pas ce jour-là : on avance jusqu'à ce qu'elle
        # existe, ce qui donne le premier instant valide après le saut.
        for extra in range(1, 4):
            shifted = naive + timedelta(hours=extra)
            candidate = shifted.replace(tzinfo=rule.zone).astimezone(UTC)
            if candidate.astimezone(rule.zone).replace(tzinfo=None) == shifted:
                return candidate
    return as_utc


def next_occurrence(rule: Rule, after: datetime, *, last_run: datetime | None = None) -> datetime:
    """Prochain déclenchement **strictement postérieur** à ``after``.

    ``last_run`` n'a de sens que pour un intervalle : il ancre la série sur le
    dernier déclenchement réel, pour qu'une exécution un peu tardive ne décale
    pas indéfiniment toutes les suivantes.
    """
    reference = after if after.tzinfo else after.replace(tzinfo=UTC)

    if rule.trigger is TriggerKind.INTERVAL:
        step = timedelta(minutes=rule.interval_minutes)
        anchor = last_run or reference
        if anchor.tzinfo is None:
            anchor = anchor.replace(tzinfo=UTC)
        candidate = anchor + step
        if candidate <= reference:
            # Rattrape le retard sans rejouer chaque pas manqué.
            missed = int((reference - anchor) // step)
            candidate = anchor + step * (missed + 1)
        return candidate

    local_day = reference.astimezone(rule.zone).replace(
        hour=0, minute=0, second=0, microsecond=0, tzinfo=None
    )
    allowed = rule.days if rule.trigger is TriggerKind.WEEKLY else tuple(range(7))

    # Huit jours suffisent : une semaine complète, plus la journée en cours.
    for offset in range(0, 9):
        day = local_day + timedelta(days=offset)
        if day.weekday() not in allowed:
            continue
        candidate = _at_local(day, rule)
        if candidate > reference:
            return candidate

    raise ValidationError(  # pragma: no cover - impossible avec `days` non vide
        "Planification impossible.",
        cause="Aucune occurrence trouvée dans les huit prochains jours.",
        remediation="Vérifier les jours sélectionnés.",
    )


def describe(rule: Rule) -> str:
    """Résumé lisible, affiché dans la liste et consigné dans l'audit."""
    if rule.trigger is TriggerKind.INTERVAL:
        minutes = rule.interval_minutes
        if minutes % 60 == 0:
            hours = minutes // 60
            return f"Toutes les {hours} h" if hours > 1 else "Toutes les heures"
        return f"Toutes les {minutes} min"

    moment = f"{rule.hour:02d}:{rule.minute:02d}"
    if rule.trigger is TriggerKind.DAILY:
        return f"Chaque jour à {moment} ({rule.timezone})"

    days = ", ".join(DAY_LABELS[day] for day in rule.days)
    return f"Chaque {days} à {moment} ({rule.timezone})"
