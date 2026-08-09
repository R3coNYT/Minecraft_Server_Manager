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
                f"{label} invalide.",
                cause=f"« {relative} » est un chemin absolu.",
                remediation=f"Indiquer un chemin relatif au dossier du serveur ({self.directory}).",
            )

        root = self.directory.resolve()
        resolved = (root / candidate).resolve()
        if resolved != root and root not in resolved.parents:
            raise LaunchError(
                f"{label} invalide.",
                cause=f"« {relative} » pointe en dehors du dossier du serveur.",
                remediation="Placer le fichier dans le dossier du serveur.",
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
                "Dossier du serveur introuvable.",
                cause=f"{directory} n'existe pas ou n'est pas un dossier.",
                remediation="Vérifier le chemin du serveur dans ses réglages.",
            )
        return directory.resolve()

    @staticmethod
    def _require_file(path: Path, *, label: str, remediation: str) -> Path:
        if not path.exists():
            raise LaunchError(
                f"{label} introuvable.",
                cause=f"{path} n'existe pas.",
                remediation=remediation,
            )
        if not path.is_file():
            raise LaunchError(
                f"{label} invalide.",
                cause=f"{path} n'est pas un fichier.",
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
                f"{label} introuvable.",
                cause=f"{command} n'existe pas ou n'est pas exécutable.",
                remediation=remediation,
            )

        found = shutil.which(command)
        if found is None:
            raise LaunchError(
                f"{label} introuvable.",
                cause=f"« {command} » n'est pas présent dans le PATH du système.",
                remediation=remediation,
            )
        return found
