"""Lancement par script batch Windows (``run.bat``).

Disponible uniquement lorsque MSM tourne sous Windows. Sous Linux, le launcher
reste visible dans l'interface mais annonce clairement pourquoi il est inutilisable
— c'est plus utile qu'une option manquante sans explication.
"""

from __future__ import annotations

import sys
from typing import ClassVar

from msm.exceptions import LaunchError
from msm.i18n import tr
from msm.launchers.base import LaunchContext, Launcher, ProcessSpec

_UNAVAILABLE = (
    "Batch scripts (.bat) can only run on Windows. On this Linux machine, use the "
    "JAR or shell script start method."
)


class BatchLauncher(Launcher):
    """Démarre le serveur via ``cmd.exe /c script.bat``."""

    key: ClassVar[str] = "batch"
    label: ClassVar[str] = "Batch script (run.bat)"
    description: ClassVar[str] = "Runs a `.bat` script. Windows only."

    def is_available(self) -> str | None:
        return None if sys.platform == "win32" else tr(_UNAVAILABLE)

    def build_spec(self, ctx: LaunchContext) -> ProcessSpec:
        if (reason := self.is_available()) is not None:
            raise LaunchError(
                tr("Start method unavailable on this machine."),
                cause=reason,
                remediation=tr("Choose “JAR file” or “Shell script” in the settings."),
            )

        directory = self._require_directory(ctx)

        if not ctx.script_path:
            raise LaunchError(
                tr("No script configured."),
                cause=tr("The server is in “Batch script” mode but no script is set."),
                remediation=tr("Select the start script (for example `run.bat`)."),
            )

        script = ctx.resolve_in_directory(ctx.script_path, label=tr("Start script"))
        self._require_file(
            script,
            label=tr("Start script"),
            remediation=tr("Put the script in {folder}, then check its name.", folder=directory),
        )

        comspec = self._resolve_executable(
            "cmd.exe",
            label=tr("Windows command interpreter"),
            remediation=tr("Check that %SystemRoot%\\System32 is on the PATH."),
        )
        # `/c` exécute puis rend la main ; le script est passé en argument distinct,
        # donc jamais interprété comme une ligne de commande composite.
        argv = [comspec, "/c", str(script), *ctx.extra_args]
        return ProcessSpec(argv=tuple(argv), cwd=directory, env=dict(ctx.env))
