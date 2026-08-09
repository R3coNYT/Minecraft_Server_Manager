"""Politique de redémarrage automatique.

Le piège classique d'un redémarrage automatique est la boucle infinie : un serveur
qui plante instantanément (port occupé, JAR corrompu) est relancé sans fin et
sature la machine. Deux garde-fous sont donc appliqués :

* un **délai à croissance exponentielle** entre deux tentatives ;
* un **plafond de plantages consécutifs**, au-delà duquel MSM abandonne et le
  signale, plutôt que d'insister.

Le compteur de plantages est remis à zéro dès que le serveur a tenu en ligne
suffisamment longtemps pour être considéré comme stable.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class AutoRestartMode(str, Enum):
    """Quand MSM doit-il relancer un serveur qui s'est arrêté ?"""

    #: Jamais — l'administrateur relance à la main.
    NEVER = "NEVER"
    #: Uniquement après un arrêt non demandé (plantage).
    ON_CRASH = "ON_CRASH"
    #: Après tout arrêt non demandé depuis le panel, y compris un `/stop` en jeu.
    ALWAYS = "ALWAYS"


@dataclass(frozen=True, slots=True)
class RestartDecision:
    """Résultat de l'évaluation de la politique."""

    should_restart: bool
    delay_s: float = 0.0
    reason: str = ""

    def __bool__(self) -> bool:
        return self.should_restart


@dataclass(frozen=True, slots=True)
class RestartPolicy:
    """Paramètres de redémarrage automatique d'un serveur."""

    mode: AutoRestartMode = AutoRestartMode.NEVER
    #: Délai de base avant la première tentative.
    delay_s: float = 10.0
    #: Nombre de plantages consécutifs tolérés avant abandon.
    max_consecutive_crashes: int = 3
    #: Facteur de croissance du délai entre deux tentatives.
    backoff_factor: float = 2.0
    #: Plafond du délai, pour éviter des attentes absurdes.
    max_delay_s: float = 300.0
    #: Durée de fonctionnement au-delà de laquelle le serveur est jugé stable.
    stability_threshold_s: float = 120.0

    def evaluate(
        self,
        *,
        stop_requested: bool,
        exit_code: int | None,
        consecutive_crashes: int,
    ) -> RestartDecision:
        """Décide s'il faut relancer, et après combien de temps.

        :param stop_requested: l'arrêt a-t-il été demandé depuis le panel ?
        :param exit_code: code de sortie du processus (``None`` si tué par signal).
        :param consecutive_crashes: plantages consécutifs *déjà* comptabilisés,
            celui qui vient de survenir inclus.
        """
        if stop_requested:
            return RestartDecision(False, reason="Arrêt demandé depuis le panel.")

        if self.mode is AutoRestartMode.NEVER:
            return RestartDecision(False, reason="Redémarrage automatique désactivé.")

        crashed = exit_code is None or exit_code != 0
        if self.mode is AutoRestartMode.ON_CRASH and not crashed:
            return RestartDecision(
                False,
                reason="Le serveur s'est arrêté normalement ; le mode « à chaque plantage » "
                "ne relance que sur erreur.",
            )

        if consecutive_crashes >= self.max_consecutive_crashes:
            return RestartDecision(
                False,
                reason=(
                    f"{consecutive_crashes} plantages consécutifs : "
                    "redémarrage automatique interrompu pour éviter une boucle."
                ),
            )

        return RestartDecision(
            True,
            delay_s=self.compute_delay(consecutive_crashes),
            reason=(
                f"Redémarrage automatique ({self.mode.value}), "
                f"tentative {consecutive_crashes + 1}/{self.max_consecutive_crashes}."
            ),
        )

    def compute_delay(self, consecutive_crashes: int) -> float:
        """Délai avant la prochaine tentative, à croissance exponentielle bornée."""
        exponent = max(0, consecutive_crashes - 1)
        delay = self.delay_s * (self.backoff_factor**exponent)
        return min(delay, self.max_delay_s)

    def is_stable(self, uptime_s: float) -> bool:
        """Le serveur a-t-il tenu assez longtemps pour remettre le compteur à zéro ?"""
        return uptime_s >= self.stability_threshold_s
