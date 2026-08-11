"""Analyse d'un dossier pour proposer une configuration de serveur.

L'analyse **suggère**, elle ne décide pas : l'administrateur reste libre de
choisir un autre JAR, un autre mode de démarrage ou un autre type. Toutes les
possibilités trouvées sont donc renvoyées, pas seulement la meilleure.

C'est ce qui évite l'écueil de la version 1, qui supposait qu'un fichier
``mohist.jar`` existait forcément.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from msm.minecraft.capabilities import detect_capabilities
from msm.minecraft.types import Capability, ServerType

#: Motifs de nom de JAR → type de serveur. Ordre significatif : le premier gagne.
_JAR_PATTERNS: tuple[tuple[re.Pattern[str], ServerType], ...] = (
    (re.compile(r"^neoforge.*\.jar$", re.I), ServerType.NEOFORGE),
    (re.compile(r"^forge.*\.jar$", re.I), ServerType.FORGE),
    (re.compile(r"^mohist.*\.jar$", re.I), ServerType.MOHIST),
    (re.compile(r"^purpur.*\.jar$", re.I), ServerType.PURPUR),
    (re.compile(r"^paper.*\.jar$", re.I), ServerType.PAPER),
    (re.compile(r"^spigot.*\.jar$", re.I), ServerType.SPIGOT),
    (re.compile(r"^craftbukkit.*\.jar$", re.I), ServerType.BUKKIT),
    (re.compile(r"^quilt.*\.jar$", re.I), ServerType.QUILT),
    (re.compile(r"^fabric.*\.jar$", re.I), ServerType.FABRIC),
    (re.compile(r"^(minecraft_)?server.*\.jar$", re.I), ServerType.VANILLA),
)

#: Scripts de démarrage reconnus, par ordre de préférence.
_SHELL_SCRIPTS = ("run.sh", "start.sh", "startserver.sh", "launch.sh")
_BATCH_SCRIPTS = ("run.bat", "start.bat", "startserver.bat", "launch.bat")

#: Version Minecraft dans un nom de fichier : « paper-1.20.1-196.jar ».
_VERSION_RE = re.compile(r"(?<![\d.])(1\.\d{1,2}(?:\.\d{1,2})?)(?![\d.])")

#: JAR à ignorer : ce sont des installateurs, pas des serveurs.
_IGNORED_JAR_RE = re.compile(r"(installer|sources|javadoc|shim)", re.I)


@dataclass(frozen=True, slots=True)
class JarCandidate:
    """Un JAR trouvé dans le dossier, avec le type qu'il suggère."""

    name: str
    size_bytes: int
    server_type: ServerType
    minecraft_version: str | None
    #: Plus le score est élevé, plus le candidat est probable.
    score: int


@dataclass(frozen=True, slots=True)
class DetectionResult:
    """Ce que MSM a trouvé dans un dossier, et ce qu'il propose."""

    directory: Path
    exists: bool
    #: Type le plus probable — modifiable par l'administrateur.
    server_type: ServerType = ServerType.UNKNOWN
    minecraft_version: str | None = None
    #: Clé du launcher suggéré (`jar`, `shell`, `batch`).
    launcher_key: str | None = None
    jar_path: str | None = None
    script_path: str | None = None
    jars: tuple[JarCandidate, ...] = ()
    scripts: tuple[str, ...] = ()
    capabilities: frozenset[Capability] = field(default_factory=frozenset)
    eula_accepted: bool | None = None
    port: int | None = None
    #: Observations destinées à l'utilisateur (« aucun JAR trouvé », …).
    notes: tuple[str, ...] = ()

    @property
    def is_configurable(self) -> bool:
        """A-t-on de quoi proposer un démarrage sans saisie manuelle ?"""
        return self.launcher_key is not None


def detect(directory: Path) -> DetectionResult:
    """Analyse un dossier et propose une configuration de démarrage."""
    directory = directory.expanduser()

    if not directory.is_dir():
        return DetectionResult(
            directory=directory,
            exists=False,
            notes=("Le dossier indiqué n'existe pas ou n'est pas accessible.",),
        )

    directory = directory.resolve()
    notes: list[str] = []

    jars = _scan_jars(directory)
    scripts = _scan_scripts(directory)
    capabilities = detect_capabilities(directory)
    properties = _read_properties(directory)

    best_jar = jars[0] if jars else None
    launcher_key, jar_path, script_path = _choose_launcher(best_jar, scripts)

    server_type = best_jar.server_type if best_jar else ServerType.UNKNOWN
    version = best_jar.minecraft_version if best_jar else None

    # Un dossier `plugins/` sur un serveur identifié Forge trahit un hybride
    # (Mohist, Magma…) : l'information est utile même si le nom du JAR ne le dit pas.
    if (
        Capability.PLUGINS in capabilities
        and Capability.MODS in capabilities
        and server_type in (ServerType.FORGE, ServerType.UNKNOWN)
    ):
        notes.append(
            "Ce serveur possède à la fois `mods/` et `plugins/` : il s'agit "
            "probablement d'un serveur hybride de type Mohist."
        )

    if not jars and not scripts:
        notes.append(
            "Aucun fichier .jar ni script de démarrage trouvé. "
            "Le mode de démarrage devra être saisi manuellement."
        )
    elif launcher_key is None:
        notes.append("Aucun mode de démarrage n'a pu être déduit automatiquement.")

    if len(jars) > 1:
        notes.append(f"{len(jars)} fichiers .jar trouvés : vérifier que celui proposé est le bon.")

    return DetectionResult(
        directory=directory,
        exists=True,
        server_type=server_type,
        minecraft_version=version or properties.get("_version"),
        launcher_key=launcher_key,
        jar_path=jar_path,
        script_path=script_path,
        jars=jars,
        scripts=scripts,
        capabilities=capabilities,
        eula_accepted=_read_eula(directory),
        port=_parse_port(properties.get("server-port")),
        notes=tuple(notes),
    )


# --------------------------------------------------------------------------- #
def _scan_jars(directory: Path) -> tuple[JarCandidate, ...]:
    """Liste les JAR du dossier, du plus probable au moins probable."""
    candidates: list[JarCandidate] = []

    try:
        entries = [entry for entry in directory.iterdir() if entry.is_file()]
    except OSError:
        return ()

    for entry in entries:
        if entry.suffix.lower() != ".jar" or _IGNORED_JAR_RE.search(entry.name):
            continue

        server_type = ServerType.UNKNOWN
        score = 0
        for index, (pattern, matched_type) in enumerate(_JAR_PATTERNS):
            if pattern.match(entry.name):
                server_type = matched_type
                score = len(_JAR_PATTERNS) - index
                break

        try:
            size = entry.stat().st_size
        except OSError:
            size = 0

        # Un serveur Minecraft pèse plusieurs mégaoctets ; un petit JAR est
        # presque toujours une bibliothèque déposée là par erreur.
        if size > 5 * 1024 * 1024:
            score += 2

        version_match = _VERSION_RE.search(entry.name)
        candidates.append(
            JarCandidate(
                name=entry.name,
                size_bytes=size,
                server_type=server_type,
                minecraft_version=version_match.group(1) if version_match else None,
                score=score,
            )
        )

    candidates.sort(key=lambda c: (-c.score, -c.size_bytes, c.name.casefold()))
    return tuple(candidates)


def _scan_scripts(directory: Path) -> tuple[str, ...]:
    found: list[str] = []
    for name in (*_SHELL_SCRIPTS, *_BATCH_SCRIPTS):
        if (directory / name).is_file():
            found.append(name)
    return tuple(found)


def _choose_launcher(
    best_jar: JarCandidate | None, scripts: tuple[str, ...]
) -> tuple[str | None, str | None, str | None]:
    """Choisit le mode de démarrage à proposer.

    Un JAR clairement identifié prime : le lancement direct donne à MSM la
    maîtrise complète du processus, sans script intermédiaire. Les scripts sont
    proposés quand aucun JAR ne se dégage — cas typique de NeoForge, dont le
    ``run.sh`` porte des arguments JVM indispensables.
    """
    if best_jar is not None and best_jar.server_type is not ServerType.UNKNOWN:
        return "jar", best_jar.name, None

    for name in _SHELL_SCRIPTS:
        if name in scripts:
            return "shell", None, name
    for name in _BATCH_SCRIPTS:
        if name in scripts:
            return "batch", None, name

    if best_jar is not None:
        return "jar", best_jar.name, None
    return None, None, None


def _read_properties(directory: Path) -> dict[str, str]:
    """Lecture minimale de ``server.properties`` (analyse complète en phase 3)."""
    path = directory / "server.properties"
    if not path.is_file():
        return {}
    values: dict[str, str] = {}
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        values[key.strip()] = value.strip()
    return values


def _parse_port(raw: str | None) -> int | None:
    try:
        port = int(raw) if raw else None
    except ValueError:
        return None
    return port if port and 1 <= port <= 65535 else None


def _read_eula(directory: Path) -> bool | None:
    """``None`` si le fichier n'existe pas encore (serveur jamais démarré)."""
    from msm.minecraft import eula

    status = eula.read_status(directory)
    return status.accepted if status.exists else None
