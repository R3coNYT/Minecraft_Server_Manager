"""Relais entre MSM et un serveur isolé — lancé comme **script**, jamais importé.

Le serveur tourne dans sa propre unité systemd, démarrée par le helper root
``msm-sandbox``. Ce relais s'y connecte par son socket, envoie la demande, puis
fait passer la console dans les deux sens : pour MSM, c'est un processus comme
un autre, avec son entrée, sa sortie et son code de sortie — celui du serveur,
que le helper laisse dans un fichier une fois la connexion terminée.

Bibliothèque standard seulement, lancé avec ``python -I`` : il démarre vite et
n'hérite de rien.

Usage : ``sandbox_relay.py SOCKET FICHIER_STATUT CHAMP...``
"""

from __future__ import annotations

import contextlib
import os
import socket
import sys
import threading
from pathlib import Path

#: Code rendu quand le helper est injoignable ou n'a pas laissé de statut.
UNREACHABLE = 125


def _pump_input(connection: socket.socket) -> None:
    """Entrée de MSM → serveur. À sa fermeture, le serveur voit la fin de flux."""
    try:
        while chunk := os.read(0, 65536):
            connection.sendall(chunk)
    except OSError:
        pass
    finally:
        with contextlib.suppress(OSError):
            connection.shutdown(socket.SHUT_WR)


def _write_all(data: bytes) -> None:
    view = memoryview(data)
    while view:
        written = os.write(1, view)
        view = view[written:]


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print("usage: sandbox_relay.py SOCKET STATUS FIELD...", file=sys.stderr)
        return 2
    socket_path, status_path, *fields = argv

    connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        connection.connect(socket_path)
    except OSError as exc:
        _write_all(
            f"[MSM] Isolation helper unreachable ({socket_path}): {exc.strerror or exc}. "
            "Run install.sh again to install it.\n".encode()
        )
        return UNREACHABLE

    request = b"".join(field.encode() + b"\0" for field in fields) + b"\0"
    connection.sendall(request)

    threading.Thread(target=_pump_input, args=(connection,), daemon=True).start()

    try:
        while chunk := connection.recv(65536):
            _write_all(chunk)
    except OSError:
        # MSM a disparu (tube cassé) : le serveur, lui, continue sans console.
        return UNREACHABLE

    # La connexion ne se ferme qu'après le helper, qui a écrit le statut avant.
    try:
        return int(Path(status_path).read_text().strip())
    except (OSError, ValueError):
        return UNREACHABLE


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
