"""Retrouver un serveur Minecraft que MSM a perdu de vue.

MSM survit à ses propres redémarrages sans couper les serveurs : il les
réadopte grâce au PID enregistré en base. Si ce PID manque — MSM arrêté au
mauvais moment, base restaurée —, le serveur tourne toujours mais plus personne
ne le suit. Le relancer se heurte alors au verrou du monde (`session.lock`).

Le dossier du serveur permet de le retrouver : Java y est lancé, et y reste
comme répertoire de travail. Seul un processus **Java** (ou qui lance un
`.jar`) est retenu : un shell ouvert dans ce dossier par un administrateur n'est
pas un serveur. Et seuls les processus du compte de MSM sont lisibles, ce qui
borne naturellement la recherche.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import psutil

_FIELDS = ["pid", "ppid", "name", "cmdline", "cwd", "create_time"]


@dataclass(frozen=True, slots=True)
class FoundProcess:
    pid: int
    create_time: float
    group_id: int | None
    command: str


def _looks_like_server(info: dict[str, Any]) -> bool:
    name = (info.get("name") or "").lower()
    cmdline = [str(part) for part in (info.get("cmdline") or [])]
    program = Path(cmdline[0]).name.lower() if cmdline else ""
    return (
        "java" in name
        or program.startswith("java")
        or any(part.lower().endswith(".jar") for part in cmdline[1:])
    )


def _same_directory(cwd: str | None, target: Path) -> bool:
    if not cwd:
        return False
    try:
        return Path(cwd).resolve() == target
    except OSError:
        return False


def find_server_process(directory: Path) -> FoundProcess | None:
    """Le processus Java qui tourne dans `directory`, s'il y en a un."""
    try:
        target = directory.resolve()
    except OSError:
        return None

    own = os.getpid()
    candidates: list[dict[str, Any]] = []
    for process in psutil.process_iter(_FIELDS):
        info = process.info
        if info.get("pid") == own:
            continue
        if _same_directory(info.get("cwd"), target) and _looks_like_server(info):
            candidates.append(info)
    if not candidates:
        return None

    # Le processus racine : un Java lancé par `run.sh` a pour parent le shell,
    # mais c'est le Java lui-même qui porte le serveur ; entre deux Java, le
    # plus ancien est celui qui détient le monde.
    pids = {info["pid"] for info in candidates}
    roots = [info for info in candidates if info.get("ppid") not in pids] or candidates
    chosen = min(roots, key=lambda info: info.get("create_time") or 0.0)

    group_id: int | None = None
    if hasattr(os, "getpgid"):
        try:
            group_id = os.getpgid(chosen["pid"])
        except OSError:  # pragma: no cover - disparu entre-temps
            return None
    return FoundProcess(
        pid=int(chosen["pid"]),
        create_time=float(chosen.get("create_time") or 0.0),
        group_id=group_id,
        command=" ".join(str(part) for part in (chosen.get("cmdline") or [])[:3]),
    )
