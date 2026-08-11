"""Règles de déclenchement périodique."""

from msm.schedule.rules import (
    MAX_INTERVAL_MINUTES,
    MIN_INTERVAL_MINUTES,
    Rule,
    TriggerKind,
    describe,
    next_occurrence,
    parse_rule,
)

__all__ = [
    "MAX_INTERVAL_MINUTES",
    "MIN_INTERVAL_MINUTES",
    "Rule",
    "TriggerKind",
    "describe",
    "next_occurrence",
    "parse_rule",
]
