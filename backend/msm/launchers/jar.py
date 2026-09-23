"""Lancement par fichier JAR — le cas le plus courant.

Produit une commande de la forme ::

    java -Xms2G -Xmx4G <arguments JVM> -jar server.jar nogui

Aucun nom de JAR n'est supposé : le fichier est celui que l'administrateur a
désigné dans les réglages du serveur.
"""

from __future__ import annotations

from typing import ClassVar

from msm.exceptions import LaunchError
from msm.i18n import tr
from msm.launchers.base import LaunchContext, Launcher, ProcessSpec

#: Arguments par défaut passés au serveur lui-même (après `-jar`).
DEFAULT_SERVER_ARGS: tuple[str, ...] = ("nogui",)

JAVA_REMEDIATION = (
    "Install Java (for example `apt install openjdk-21-jre-headless` on Debian/Ubuntu) "
    "or enter the full path of the Java executable in the server settings."
)


class JarLauncher(Launcher):
    """Démarre le serveur via ``java -jar``."""

    key: ClassVar[str] = "jar"
    label: ClassVar[str] = "JAR file"
    description: ClassVar[str] = "Runs `java -jar <file>` with the chosen memory options."

    def build_spec(self, ctx: LaunchContext) -> ProcessSpec:
        directory = self._require_directory(ctx)

        if not ctx.jar_path:
            raise LaunchError(
                tr("No JAR file configured."),
                cause=tr("The server is in “JAR file” mode but no JAR is set."),
                remediation=tr("Select the server's .jar file in its settings."),
            )

        jar = ctx.resolve_in_directory(ctx.jar_path, label=tr("JAR file"))
        self._require_file(
            jar,
            label=tr("JAR file"),
            remediation=tr("Put the .jar file in {folder}, then check its name.", folder=directory),
        )

        java = self._resolve_executable(
            ctx.java_path or "java", label="Java", remediation=tr(JAVA_REMEDIATION)
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
                tr("Inconsistent memory settings."),
                cause=tr(
                    "The minimum memory ({minimum} MB) exceeds the maximum ({maximum} MB).",
                    minimum=minimum,
                    maximum=maximum,
                ),
                remediation=tr("Fix the memory values in the server settings."),
            )

        # Les options mémoire explicites priment sur celles déduites des réglages.
        has_explicit_xms = any(a.startswith("-Xms") for a in ctx.jvm_args)
        has_explicit_xmx = any(a.startswith("-Xmx") for a in ctx.jvm_args)

        if minimum and not has_explicit_xms:
            args.append(f"-Xms{minimum}M")
        if maximum and not has_explicit_xmx:
            args.append(f"-Xmx{maximum}M")
        return args
