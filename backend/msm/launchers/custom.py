"""Lancement par commande personnalisée.

Échappatoire assumée : certains serveurs ont un mode de démarrage exotique. La
commande est fournie sous forme de **liste d'arguments**, jamais de chaîne à
interpréter — l'administrateur garde la main sans qu'un shell ne s'intercale.
"""

from __future__ import annotations

from typing import ClassVar

from msm.exceptions import LaunchError
from msm.launchers.base import LaunchContext, Launcher, ProcessSpec


class CustomLauncher(Launcher):
    """Démarre le serveur avec une liste d'arguments définie par l'administrateur."""

    key: ClassVar[str] = "custom"
    label: ClassVar[str] = "Commande personnalisée"
    description: ClassVar[str] = (
        "Liste d'arguments libre, exécutée sans interpréteur shell. "
        "Le premier élément est le programme à lancer."
    )

    def build_spec(self, ctx: LaunchContext) -> ProcessSpec:
        directory = self._require_directory(ctx)

        if not ctx.custom_argv:
            raise LaunchError(
                "Aucune commande personnalisée configurée.",
                cause="La liste d'arguments de démarrage est vide.",
                remediation=(
                    "Saisir la commande sous forme d'arguments séparés, "
                    "par exemple : `java`, `-Xmx4G`, `-jar`, `server.jar`, `nogui`."
                ),
            )

        if any(not isinstance(arg, str) or not arg for arg in ctx.custom_argv):
            raise LaunchError(
                "Commande personnalisée invalide.",
                cause="Un des arguments est vide ou n'est pas du texte.",
                remediation="Supprimer les arguments vides de la liste.",
            )

        program, *arguments = ctx.custom_argv
        resolved = self._resolve_executable(
            program,
            label="Programme de démarrage",
            remediation=(
                f"Vérifier que « {program} » existe, est exécutable, "
                "et est accessible depuis le PATH ou par chemin complet."
            ),
        )

        argv = [resolved, *arguments, *ctx.extra_args]
        return ProcessSpec(argv=tuple(argv), cwd=directory, env=dict(ctx.env))
