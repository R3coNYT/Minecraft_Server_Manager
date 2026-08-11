"""Lancement par script batch Windows (``run.bat``).

Disponible uniquement lorsque MSM tourne sous Windows. Sous Linux, le launcher
reste visible dans l'interface mais annonce clairement pourquoi il est inutilisable
— c'est plus utile qu'une option manquante sans explication.
"""

from __future__ import annotations

import sys
from typing import ClassVar

from msm.exceptions import LaunchError
from msm.launchers.base import LaunchContext, Launcher, ProcessSpec

_UNAVAILABLE = (
    "Les scripts batch (.bat) ne peuvent être exécutés que sous Windows. "
    "Sur cette machine Linux, utiliser le lancement par JAR ou par script shell."
)


class BatchLauncher(Launcher):
    """Démarre le serveur via ``cmd.exe /c script.bat``."""

    key: ClassVar[str] = "batch"
    label: ClassVar[str] = "Script batch (run.bat)"
    description: ClassVar[str] = "Exécute un script `.bat`. Windows uniquement."

    def is_available(self) -> str | None:
        return None if sys.platform == "win32" else _UNAVAILABLE

    def build_spec(self, ctx: LaunchContext) -> ProcessSpec:
        if (reason := self.is_available()) is not None:
            raise LaunchError(
                "Mode de démarrage indisponible sur cette machine.",
                cause=reason,
                remediation="Choisir « Fichier JAR » ou « Script shell » dans les réglages.",
            )

        directory = self._require_directory(ctx)

        if not ctx.script_path:
            raise LaunchError(
                "Aucun script configuré.",
                cause="Le serveur est en mode « Script batch » mais aucun script n'est renseigné.",
                remediation="Sélectionner le script de démarrage (par exemple `run.bat`).",
            )

        script = ctx.resolve_in_directory(ctx.script_path, label="Script de démarrage")
        self._require_file(
            script,
            label="Script de démarrage",
            remediation=f"Placer le script dans {directory} puis vérifier son nom.",
        )

        comspec = self._resolve_executable(
            "cmd.exe",
            label="Interpréteur de commandes Windows",
            remediation="Vérifier que %SystemRoot%\\System32 figure dans le PATH.",
        )
        # `/c` exécute puis rend la main ; le script est passé en argument distinct,
        # donc jamais interprété comme une ligne de commande composite.
        argv = [comspec, "/c", str(script), *ctx.extra_args]
        return ProcessSpec(argv=tuple(argv), cwd=directory, env=dict(ctx.env))
