"""Lancement par fichier JAR — le cas le plus courant.

Produit une commande de la forme ::

    java -Xms2G -Xmx4G <arguments JVM> -jar server.jar nogui

Aucun nom de JAR n'est supposé : le fichier est celui que l'administrateur a
désigné dans les réglages du serveur.
"""

from __future__ import annotations

from typing import ClassVar

from msm.exceptions import LaunchError
from msm.launchers.base import LaunchContext, Launcher, ProcessSpec

#: Arguments par défaut passés au serveur lui-même (après `-jar`).
DEFAULT_SERVER_ARGS: tuple[str, ...] = ("nogui",)

JAVA_REMEDIATION = (
    "Installer Java (par exemple `apt install openjdk-21-jre-headless` sous Debian/Ubuntu) "
    "ou renseigner le chemin complet de l'exécutable Java dans les réglages du serveur."
)


class JarLauncher(Launcher):
    """Démarre le serveur via ``java -jar``."""

    key: ClassVar[str] = "jar"
    label: ClassVar[str] = "Fichier JAR"
    description: ClassVar[str] = "Lance `java -jar <fichier>` avec les options mémoire choisies."

    def build_spec(self, ctx: LaunchContext) -> ProcessSpec:
        directory = self._require_directory(ctx)

        if not ctx.jar_path:
            raise LaunchError(
                "Aucun fichier JAR configuré.",
                cause="Le serveur est en mode « Fichier JAR » mais aucun JAR n'est renseigné.",
                remediation="Sélectionner le fichier .jar du serveur dans ses réglages.",
            )

        jar = ctx.resolve_in_directory(ctx.jar_path, label="Fichier JAR")
        self._require_file(
            jar,
            label="Fichier JAR",
            remediation=f"Placer le fichier .jar dans {directory} puis vérifier son nom.",
        )

        java = self._resolve_executable(
            ctx.java_path or "java", label="Java", remediation=JAVA_REMEDIATION
        )

        argv: list[str] = [java]
        argv.extend(self._memory_args(ctx))
        argv.extend(ctx.jvm_args)
        argv.extend(["-jar", str(jar)])
        argv.extend(ctx.extra_args or DEFAULT_SERVER_ARGS)

        return ProcessSpec(argv=tuple(argv), cwd=directory, env=dict(ctx.env))

    @staticmethod
    def _memory_args(ctx: LaunchContext) -> list[str]:
        """Traduit les réglages mémoire en options JVM, avec contrôle de cohérence."""
        args: list[str] = []
        minimum, maximum = ctx.memory_min_mb, ctx.memory_max_mb

        if minimum is not None and maximum is not None and minimum > maximum:
            raise LaunchError(
                "Réglages mémoire incohérents.",
                cause=f"La mémoire minimale ({minimum} Mo) dépasse la maximale ({maximum} Mo).",
                remediation="Corriger les valeurs de mémoire dans les réglages du serveur.",
            )

        # Les options mémoire explicites priment sur celles déduites des réglages.
        has_explicit_xms = any(a.startswith("-Xms") for a in ctx.jvm_args)
        has_explicit_xmx = any(a.startswith("-Xmx") for a in ctx.jvm_args)

        if minimum and not has_explicit_xms:
            args.append(f"-Xms{minimum}M")
        if maximum and not has_explicit_xmx:
            args.append(f"-Xmx{maximum}M")
        return args
