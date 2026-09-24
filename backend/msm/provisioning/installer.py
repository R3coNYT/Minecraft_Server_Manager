"""Exécution de l'installeur officiel de NeoForge.

NeoForge ne publie pas de JAR de serveur : son installeur télécharge Minecraft
et les bibliothèques, puis produit `run.sh` / `run.bat`. Il s'exécute une fois,
dans le dossier du serveur, sous le compte de MSM :

* argv en liste, jamais une chaîne shell ;
* délai borné — un installeur bloqué ne doit pas geler la création ;
* la fin de sa sortie est rendue en cas d'échec, pour dire *pourquoi*.
"""

from __future__ import annotations

import asyncio
import shutil
import sys
from collections import deque
from collections.abc import Callable
from pathlib import Path

from msm.exceptions import MsmError
from msm.i18n import tr
from msm.logging_conf import get_logger

logger = get_logger(__name__)

#: Minecraft et ses bibliothèques : quelques minutes sur une connexion lente.
INSTALL_TIMEOUT_S = 20 * 60
#: Lignes de sortie gardées pour expliquer un échec.
_TAIL_LINES = 12

JAVA_REMEDIATION = (
    "Install Java (for example `apt install openjdk-21-jre-headless` on Debian/Ubuntu), "
    "then create the server again."
)


class InstallerFailed(MsmError):
    code = "PROVISIONING_INSTALLER_FAILED"
    status_code = 500


def find_java() -> str:
    java = shutil.which("java")
    if java is None:
        raise InstallerFailed(
            tr("Java is required to install NeoForge."),
            cause=tr("“java” is not on the system PATH."),
            remediation=tr(JAVA_REMEDIATION),
        )
    return java


def start_script(directory: Path) -> Path:
    """Script de démarrage produit par l'installeur, selon la plateforme."""
    return directory / ("run.bat" if sys.platform == "win32" else "run.sh")


async def run_installer(
    installer: Path,
    directory: Path,
    *,
    java: str,
    on_line: Callable[[str], None] | None = None,
    timeout: float = INSTALL_TIMEOUT_S,
) -> Path:
    """Installe le serveur NeoForge dans `directory` ; renvoie son script de démarrage."""
    process = await asyncio.create_subprocess_exec(
        java,
        "-jar",
        installer.name,
        "--installServer",
        cwd=directory,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    tail: deque[str] = deque(maxlen=_TAIL_LINES)

    async def read_output() -> None:
        assert process.stdout is not None
        async for raw in process.stdout:
            line = raw.decode("utf-8", errors="replace").rstrip()
            if line:
                tail.append(line)
                if on_line is not None:
                    on_line(line)

    try:
        await asyncio.wait_for(asyncio.gather(read_output(), process.wait()), timeout=timeout)
    except TimeoutError as exc:
        process.kill()
        await process.wait()
        raise InstallerFailed(
            tr("The NeoForge installer took too long."),
            cause=tr("It was stopped after {minutes} minutes.", minutes=int(timeout // 60)),
            remediation=tr("Check the machine's network connection, then try again."),
        ) from exc
    except BaseException:
        # Annulation (arrêt de MSM) : l'installeur ne doit pas survivre à la tâche.
        if process.returncode is None:
            process.kill()
            await process.wait()
        raise

    if process.returncode != 0:
        raise InstallerFailed(
            tr("The NeoForge installer failed."),
            cause=tr(
                "Exit code {code}. Last lines: {lines}",
                code=process.returncode,
                lines=" | ".join(tail) or "—",
            ),
            remediation=tr(
                "Check the machine's network connection and Java version, then try again."
            ),
        )

    script = start_script(directory)
    if not script.is_file():
        raise InstallerFailed(
            tr("The NeoForge installer produced no start script."),
            cause=tr("{script} is missing after installation.", script=script.name),
            remediation=tr("Choose another NeoForge version, then try again."),
        )
    if sys.platform != "win32":
        script.chmod(script.stat().st_mode | 0o750)

    # L'installeur et son journal n'ont plus d'usage : le dossier reste celui
    # d'un serveur, pas d'une installation.
    installer.unlink(missing_ok=True)
    for leftover in directory.glob(f"{installer.name}.log"):
        leftover.unlink(missing_ok=True)
    logger.info("neoforge_installed", directory=str(directory), script=script.name)
    return script
