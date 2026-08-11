"""Tests du calcul des déclenchements."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from msm.exceptions import ValidationError
from msm.schedule.rules import (
    MIN_INTERVAL_MINUTES,
    Rule,
    TriggerKind,
    describe,
    next_occurrence,
    parse_rule,
)

PARIS = ZoneInfo("Europe/Paris")


def at(local: str, zone: ZoneInfo = PARIS) -> datetime:
    """Instant UTC correspondant à une heure locale, écrite « 2026-03-29 01:30 »."""
    naive = datetime.strptime(local, "%Y-%m-%d %H:%M")
    return naive.replace(tzinfo=zone).astimezone(UTC)


class TestParsing:
    def test_daily(self) -> None:
        rule = parse_rule({"trigger": "DAILY", "hour": 4, "minute": 30, "timezone": "Europe/Paris"})

        assert rule.trigger is TriggerKind.DAILY
        assert (rule.hour, rule.minute) == (4, 30)

    def test_weekly_sorts_and_deduplicates_days(self) -> None:
        rule = parse_rule({"trigger": "WEEKLY", "hour": 3, "days": [4, 0, 4]})

        assert rule.days == (0, 4)

    @pytest.mark.parametrize(
        "raw",
        [
            {"trigger": "INCONNU"},
            {"trigger": "INTERVAL", "interval_minutes": 1},
            {"trigger": "INTERVAL", "interval_minutes": "souvent"},
            {"trigger": "DAILY", "hour": 25},
            {"trigger": "DAILY", "hour": 3, "minute": 99},
            {"trigger": "WEEKLY", "hour": 3, "days": []},
            {"trigger": "WEEKLY", "hour": 3, "days": [9]},
            {"trigger": "DAILY", "hour": 3, "timezone": "Mars/Olympus"},
        ],
    )
    def test_invalid_rules_are_refused(self, raw: dict) -> None:
        with pytest.raises(ValidationError):
            parse_rule(raw)

    def test_errors_explain_the_correction(self) -> None:
        with pytest.raises(ValidationError) as excinfo:
            parse_rule({"trigger": "INTERVAL", "interval_minutes": 1})

        assert str(MIN_INTERVAL_MINUTES) in (excinfo.value.remediation or "")


class TestInterval:
    def test_next_is_one_step_ahead(self) -> None:
        rule = Rule(trigger=TriggerKind.INTERVAL, interval_minutes=360)
        now = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)

        assert next_occurrence(rule, now) == now + timedelta(hours=6)

    def test_series_stays_anchored_on_the_last_run(self) -> None:
        """Une exécution un peu tardive ne doit pas décaler toutes les suivantes."""
        rule = Rule(trigger=TriggerKind.INTERVAL, interval_minutes=60)
        last = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)
        now = datetime(2026, 8, 11, 12, 0, 40, tzinfo=UTC)

        assert next_occurrence(rule, now, last_run=last) == datetime(2026, 8, 11, 13, 0, tzinfo=UTC)

    def test_long_outage_does_not_replay_every_missed_step(self) -> None:
        """MSM arrêté douze heures ne doit pas produire douze déclenchements."""
        rule = Rule(trigger=TriggerKind.INTERVAL, interval_minutes=60)
        last = datetime(2026, 8, 11, 0, 0, tzinfo=UTC)
        now = datetime(2026, 8, 11, 12, 30, tzinfo=UTC)

        assert next_occurrence(rule, now, last_run=last) == datetime(2026, 8, 11, 13, 0, tzinfo=UTC)


class TestDaily:
    def test_today_if_still_ahead(self) -> None:
        rule = Rule(trigger=TriggerKind.DAILY, hour=4, minute=0, timezone="Europe/Paris")

        assert next_occurrence(rule, at("2026-08-11 01:00")) == at("2026-08-11 04:00")

    def test_tomorrow_once_passed(self) -> None:
        rule = Rule(trigger=TriggerKind.DAILY, hour=4, minute=0, timezone="Europe/Paris")

        assert next_occurrence(rule, at("2026-08-11 05:00")) == at("2026-08-12 04:00")

    def test_exactly_now_counts_as_passed(self) -> None:
        """Sans cela, la tâche se redéclencherait en boucle pendant sa minute."""
        rule = Rule(trigger=TriggerKind.DAILY, hour=4, minute=0, timezone="Europe/Paris")

        assert next_occurrence(rule, at("2026-08-11 04:00")) == at("2026-08-12 04:00")


class TestWeekly:
    def test_next_selected_day(self) -> None:
        # 2026-08-11 est un mardi ; la règle vise jeudi (3).
        rule = Rule(trigger=TriggerKind.WEEKLY, hour=3, days=(3,), timezone="Europe/Paris")

        result = next_occurrence(rule, at("2026-08-11 10:00"))

        assert result == at("2026-08-13 03:00")
        assert result.astimezone(PARIS).weekday() == 3

    def test_wraps_to_next_week(self) -> None:
        # Lundi visé, on est mardi : la prochaine occurrence est la semaine suivante.
        rule = Rule(trigger=TriggerKind.WEEKLY, hour=3, days=(0,), timezone="Europe/Paris")

        assert next_occurrence(rule, at("2026-08-11 10:00")) == at("2026-08-17 03:00")


class TestDaylightSaving:
    """Une sauvegarde « à 4 h » doit rester à 4 h des deux côtés du changement."""

    def test_local_hour_is_preserved_across_the_spring_change(self) -> None:
        # En 2026, la France passe à l'heure d'été le 29 mars.
        rule = Rule(trigger=TriggerKind.DAILY, hour=4, minute=0, timezone="Europe/Paris")

        result = next_occurrence(rule, at("2026-03-28 12:00"))

        assert result.astimezone(PARIS).hour == 4
        # 24 heures plus tôt il était 4 h aussi, mais l'écart réel n'est que de 23 h.
        assert result - at("2026-03-28 04:00") == timedelta(hours=23)

    def test_a_time_that_does_not_exist_falls_after_the_jump(self) -> None:
        """2 h 30 n'existe pas le 29 mars : l'heure saute de 2 h à 3 h."""
        rule = Rule(trigger=TriggerKind.DAILY, hour=2, minute=30, timezone="Europe/Paris")

        result = next_occurrence(rule, at("2026-03-28 12:00"))
        local = result.astimezone(PARIS)

        assert (local.year, local.month, local.day) == (2026, 3, 29)
        # Le premier instant valide après le saut, pas un déclenchement perdu.
        assert local.hour == 3

    def test_a_time_that_happens_twice_fires_once(self) -> None:
        """2 h 30 existe deux fois le 25 octobre : la première l'emporte."""
        rule = Rule(trigger=TriggerKind.DAILY, hour=2, minute=30, timezone="Europe/Paris")

        first = next_occurrence(rule, at("2026-10-24 12:00"))
        # L'occurrence suivante est le lendemain, pas la seconde du même jour.
        second = next_occurrence(rule, first)

        assert (second - first) > timedelta(hours=20)
        assert second.astimezone(PARIS).day == 26


class TestDescribe:
    @pytest.mark.parametrize(
        ("rule", "expected"),
        [
            (Rule(TriggerKind.INTERVAL, interval_minutes=360), "Toutes les 6 h"),
            (Rule(TriggerKind.INTERVAL, interval_minutes=60), "Toutes les heures"),
            (Rule(TriggerKind.INTERVAL, interval_minutes=90), "Toutes les 90 min"),
        ],
    )
    def test_interval(self, rule: Rule, expected: str) -> None:
        assert describe(rule) == expected

    def test_weekly_names_the_days(self) -> None:
        rule = Rule(TriggerKind.WEEKLY, hour=3, minute=30, days=(0, 3), timezone="Europe/Paris")

        assert describe(rule) == "Chaque lundi, jeudi à 03:30 (Europe/Paris)"
