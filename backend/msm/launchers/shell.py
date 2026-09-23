"""Lancement par script shell — cas de Forge et NeoForge (``run.sh``).

Deux différences importantes avec le lancement direct d'un JAR :

* le processus créé est le **script**, pas Java ; le vrai processus Java est un
  descendant. Le gestionnaire de processus le découvre à l'exécution pour les
  statistiques, et agit sur le *groupe* de processus pour les signaux.
* la transmission de l'entrée standard dépend de l'écriture du script. Si celui-ci
  redirige ou ferme stdin, la console passe en lecture seule — MSM le détecte et
  le signale au lieu d'échouer silencieusement.
"""

from __future__ import annotations

import os
import sys
from typing import ClassVar

from msm.exceptions import LaunchError
from msm.i18n import tr
from msm.launchers.base import LaunchContext, Launcher, ProcessSpec


class ShellLauncher(Launcher):
    """Démarre le serveur via un script ``.sh``."""

    key: ClassVar[str] = "shell"
    label: ClassVar[str] = "Shell script (run.sh)"
    description: ClassVar[str] = "Runs a shell script, typically the Forge/NeoForge `run.sh`."

    def build_spec(self, ctx: LaunchContext) -> ProcessSpec:
        directory = self._require_directory(ctx)

        if not ctx.script_path:
            raise LaunchError(
                tr("No script configured."),
                cause=tr("The server is in “Shell script” mode but no script is set."),
                remediation=tr("Select the start script (for example `run.sh`)."),
            )

        script = ctx.resolve_in_directory(ctx.script_path, label=tr("Start script"))
        self._require_file(
            script,
            label=tr("Start script"),
            remediation=tr("Put the script in {folder}, then check its name.", folder=directory),
        )

        argv: list[str]
        if sys.platform == "win32":
            # Windows n'exécute pas nativement les scripts shell : il faut bash
            # (Git Bash, WSL, MSYS2…).
            bash = self._resolve_executable(
                "bash",
                label="bash",
                remediation=tr(
                    "Install Git for Windows (which provides bash) or WSL, then make sure "
                    "`bash` is on the PATH. On Windows, a Forge/NeoForge server can also be "
                    "started through `run.bat` by changing the start method."
                ),
            )
            argv = [bash, str(script)]
        else:
            if not os.access(script, os.X_OK):
                raise LaunchError(
                    tr("Cannot start the server."),
                    cause=tr("{script} is not executable.", script=script.name),
                    remediation=tr("Run: chmod +x {script}", script=script),
                )
            argv = [str(script)]

        argv.extend(ctx.extra_args)
        return ProcessSpec(argv=tuple(argv), cwd=directory, env=dict(ctx.env))
