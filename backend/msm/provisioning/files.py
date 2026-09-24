"""Écritures dans le dossier d'un serveur en cours de création."""

from __future__ import annotations

import re
import shutil
from pathlib import Path

EULA_URL = "https://aka.ms/MinecraftEULA"


def is_empty_or_missing(directory: Path) -> bool:
    """Un dossier absent ou vide peut accueillir un nouveau serveur."""
    return not directory.exists() or (directory.is_dir() and not any(directory.iterdir()))


def clear_directory(directory: Path, *, remove_itself: bool) -> None:
    """Défait une création échouée : rien de ce qu'elle a écrit ne doit rester."""
    if not directory.is_dir():
        return
    if remove_itself:
        shutil.rmtree(directory, ignore_errors=True)
        return
    for child in directory.iterdir():
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child, ignore_errors=True)
        else:
            child.unlink(missing_ok=True)


def write_eula(directory: Path) -> None:
    """Accepte le CLUF de Mojang — seulement quand l'utilisateur l'a accepté."""
    (directory / "eula.txt").write_text(
        f"# Accepted in Minecraft Server Manager ({EULA_URL}).\neula=true\n", encoding="utf-8"
    )


def write_port(directory: Path, port: int) -> None:
    """Fixe `server-port` sans toucher au reste de `server.properties`."""
    path = directory / "server.properties"
    lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    for index, line in enumerate(lines):
        if re.match(r"^\s*server-port\s*=", line):
            lines[index] = f"server-port={port}"
            break
    else:
        lines.append(f"server-port={port}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_jvm_memory(directory: Path, memory_min_mb: int, memory_max_mb: int) -> None:
    """Mémoire d'un serveur NeoForge : `run.sh` lit ses arguments JVM dans ce fichier."""
    path = directory / "user_jvm_args.txt"
    lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    kept = [line for line in lines if not re.match(r"^\s*-Xm[sx]", line)]
    kept += [f"-Xms{memory_min_mb}M", f"-Xmx{memory_max_mb}M"]
    path.write_text("\n".join(kept) + "\n", encoding="utf-8")
