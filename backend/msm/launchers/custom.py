"""Lancement par commande personnalisée.

Échappatoire assumée : certains serveurs ont un mode de démarrage exotique. La
commande est fournie sous forme de **liste d'arguments**, jamais de chaîne à
interpréter — l'administrateur garde la main sans qu'un shell ne s'intercale.
"""

from __future__ import annotations

from typing import ClassVar

from msm.exceptions import LaunchError
from msm.i18n import tr
from msm.launchers.base import LaunchContext, Launcher, ProcessSpec


class CustomLauncher(Launcher):
    """Démarre le serveur avec une liste d'arguments définie par l'administrateur."""

    key: ClassVar[str] = "custom"
    label: ClassVar[str] = "Custom command"
    description: ClassVar[str] = (
        "Free-form argument list, run without a shell interpreter. "
        "The first item is the program to launch."
    )

    def build_spec(self, ctx: LaunchContext) -> ProcessSpec:
        directory = self._require_directory(ctx)

        if not ctx.custom_argv:
            raise LaunchError(
                tr("No custom command configured."),
                cause=tr("The start argument list is empty."),
                remediation=tr(
                    "Enter the command as separate arguments, for example: "
                    "`java`, `-Xmx4G`, `-jar`, `server.jar`, `nogui`."
                ),
            )

        if any(not isinstance(arg, str) or not arg for arg in ctx.custom_argv):
            raise LaunchError(
                tr("Invalid custom command."),
                cause=tr("One of the arguments is empty or not text."),
                remediation=tr("Remove the empty arguments from the list."),
            )

        program, *arguments = ctx.custom_argv
        resolved = self._resolve_executable(
            program,
            label=tr("Start program"),
            remediation=tr(
                "Check that “{program}” exists, is executable and can be reached from the "
                "PATH or by its full path.",
                program=program,
            ),
        )

        argv = [resolved, *arguments, *ctx.extra_args]
        return ProcessSpec(argv=tuple(argv), cwd=directory, env=dict(ctx.env))
