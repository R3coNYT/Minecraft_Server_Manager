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
from msm.launchers.base import LaunchContext, Launcher, ProcessSpec


class ShellLauncher(Launcher):
    """Démarre le serveur via un script ``.sh``."""

    key: ClassVar[str] = "shell"
    label: ClassVar[str] = "Script shell (run.sh)"
    description: ClassVar[str] = "Exécute un script shell, typiquement `run.sh` de Forge/NeoForge."

    def build_spec(self, ctx: LaunchContext) -> ProcessSpec:
        directory = self._require_directory(ctx)

        if not ctx.script_path:
            raise LaunchError(
                "Aucun script configuré.",
                cause="Le serveur est en mode « Script shell » mais aucun script n'est renseigné.",
                remediation="Sélectionner le script de démarrage (par exemple `run.sh`).",
            )

        script = ctx.resolve_in_directory(ctx.script_path, label="Script de démarrage")
        self._require_file(
            script,
            label="Script de démarrage",
            remediation=f"Placer le script dans {directory} puis vérifier son nom.",
        )

        argv: list[str]
        if sys.platform == "win32":
            # Windows n'exécute pas nativement les scripts shell : il faut bash
            # (Git Bash, WSL, MSYS2…).
            bash = self._resolve_executable(
                "bash",
                label="bash",
                remediation=(
                    "Installer Git for Windows (qui fournit bash) ou WSL, puis s'assurer "
                    "que `bash` est accessible dans le PATH. "
                    "Sous Windows, un serveur Forge/NeoForge peut aussi être démarré "
                    "via `run.bat` en changeant le mode de lancement."
                ),
            )
            argv = [bash, str(script)]
        else:
            if not os.access(script, os.X_OK):
                raise LaunchError(
                    "Impossible de démarrer le serveur.",
                    cause=f"{script.name} n'est pas exécutable.",
                    remediation=f"chmod +x {script}",
                )
            argv = [str(script)]

        argv.extend(ctx.extra_args)
        return ProcessSpec(argv=tuple(argv), cwd=directory, env=dict(ctx.env))
