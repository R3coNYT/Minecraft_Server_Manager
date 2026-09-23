"""Abstraction de lancement d'un serveur Minecraft.

Un *launcher* traduit la configuration d'un serveur en une commande exécutable.
C'est le seul endroit du code qui sait qu'un serveur peut démarrer par un JAR, un
script shell ou un batch Windows — le gestionnaire de processus, lui, ne manipule
que des :class:`ProcessSpec`.

Ajouter une nouvelle méthode de démarrage revient à écrire une sous-classe et à
l'enregistrer : aucune autre partie du code n'a besoin d'être modifiée.

**Invariant absolu** : ``ProcessSpec.argv`` est une *liste d'arguments*, jamais une
chaîne de commande. Le processus est lancé sans interpréteur (``shell=False``), ce
qui rend structurellement impossible l'injection de commandes shell.
"""

from __future__ import annotations

import os
import shutil
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar

from msm.exceptions import LaunchError
from msm.i18n import tr


@dataclass(frozen=True, slots=True)
class ProcessSpec:
    """Description complète d'un processus à lancer."""

    #: Programme et arguments, déjà découpés. Jamais interprété par un shell.
    argv: tuple[str, ...]
    #: Répertoire de travail — toujours le dossier du serveur.
    cwd: Path
    #: Variables d'environnement supplémentaires (fusionnées à celles du processus).
    env: dict[str, str] = field(default_factory=dict)

    def display(self) -> str:
        """Commande lisible, pour l'interface et les logs. Jamais exécutée."""
        return " ".join(f'"{a}"' if " " in a else a for a in self.argv)


@dataclass(frozen=True, slots=True)
class LaunchContext:
    """Données nécessaires à la construction d'une commande de démarrage.

    Volontairement découplé des modèles SQLAlchemy : les launchers restent
    testables sans base de données.
    """

    name: str
    directory: Path
    java_path: str | None = None
    jar_path: str | None = None
    script_path: str | None = None
    custom_argv: tuple[str, ...] = ()
    jvm_args: tuple[str, ...] = ()
    extra_args: tuple[str, ...] = ()
    memory_min_mb: int | None = None
    memory_max_mb: int | None = None
    env: dict[str, str] = field(default_factory=dict)

    def resolve_in_directory(self, relative: str, *, label: str) -> Path:
        """Résout un chemin **relatif au dossier du serveur**, sans sortie possible.

        Un chemin absolu est refusé : la configuration d'un serveur ne doit pas
        pouvoir désigner un exécutable situé ailleurs sur la machine.
        """
        candidate = Path(relative)
        if candidate.is_absolute():
            raise LaunchError(
                tr("{label}: invalid.", label=label),
                cause=tr("“{path}” is an absolute path.", path=relative),
                remediation=tr(
                    "Enter a path relative to the server folder ({folder}).", folder=self.directory
                ),
            )

        root = self.directory.resolve()
        resolved = (root / candidate).resolve()
        if resolved != root and root not in resolved.parents:
            raise LaunchError(
                tr("{label}: invalid.", label=label),
                cause=tr("“{path}” points outside the server folder.", path=relative),
                remediation=tr("Put the file in the server folder."),
            )
        return resolved


class Launcher(ABC):
    """Interface commune à toutes les méthodes de démarrage."""

    #: Identifiant stable, stocké en base et exposé par l'API.
    key: ClassVar[str]
    #: Libellé affiché dans l'interface.
    label: ClassVar[str]
    #: Description courte, affichée à la création d'un serveur.
    description: ClassVar[str] = ""

    @abstractmethod
    def build_spec(self, ctx: LaunchContext) -> ProcessSpec:
        """Construit la commande de démarrage, ou lève :class:`LaunchError`."""

    def validate(self, ctx: LaunchContext) -> None:
        """Vérifie que le lancement est possible. Lève :class:`LaunchError` sinon.

        Par défaut, construire la commande suffit à valider la configuration.
        """
        self.build_spec(ctx)

    def is_available(self) -> str | None:
        """Renvoie ``None`` si utilisable sur cette machine, sinon la raison.

        Permet à l'interface de griser les options impossibles (par exemple
        ``run.bat`` sous Linux) au lieu de laisser l'utilisateur échouer.
        """
        return None

    # ---------------------------------------------------------------- #
    #  Aides communes aux implémentations
    # ---------------------------------------------------------------- #
    @staticmethod
    def _require_directory(ctx: LaunchContext) -> Path:
        directory = ctx.directory
        if not directory.is_dir():
            raise LaunchError(
                tr("Server folder not found."),
                cause=tr("{path} does not exist or is not a folder.", path=directory),
                remediation=tr("Check the server path in its settings."),
            )
        return directory.resolve()

    @staticmethod
    def _require_file(path: Path, *, label: str, remediation: str) -> Path:
        if not path.exists():
            raise LaunchError(
                tr("{label}: not found.", label=label),
                cause=tr("{path} does not exist.", path=path),
                remediation=remediation,
            )
        if not path.is_file():
            raise LaunchError(
                tr("{label}: invalid.", label=label),
                cause=tr("{path} is not a file.", path=path),
                remediation=remediation,
            )
        return path

    @staticmethod
    def _resolve_executable(command: str, *, label: str, remediation: str) -> str:
        """Localise un exécutable, qu'il soit dans le PATH ou donné par son chemin."""
        candidate = Path(command)
        if candidate.is_absolute() or candidate.parent != Path():
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return str(candidate)
            raise LaunchError(
                tr("{label}: not found.", label=label),
                cause=tr("{command} does not exist or is not executable.", command=command),
                remediation=remediation,
            )

        found = shutil.which(command)
        if found is None:
            raise LaunchError(
                tr("{label}: not found.", label=label),
                cause=tr("“{command}” is not on the system PATH.", command=command),
                remediation=remediation,
            )
        return found
