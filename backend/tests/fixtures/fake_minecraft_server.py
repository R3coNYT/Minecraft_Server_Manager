"""Faux serveur Minecraft, en Python pur.

Reproduit le comportement observable d'un vrai serveur — format de logs, message
de fin de démarrage, lecture des commandes sur l'entrée standard, arrêt sur
``stop`` — sans nécessiter Java ni un serveur réel.

C'est ce qui permet à toute la suite de tests du gestionnaire de processus de
s'exécuter en quelques secondes, **et sur les deux systèmes d'exploitation**. Sans
lui, l'isolation des processus ne serait vérifiable que manuellement.

Options de simulation :

``--startup-delay S``   temps avant le message « Done »
``--ready``/``--no-ready``  émettre ou non le message de fin de démarrage
``--ignore-stop``       ignorer la commande ``stop`` (serveur figé)
``--ignore-save``       ne pas répondre à ``save-off``/``save-all`` (sauvegarde à chaud refusée)
``--ignore-signals``    ignorer SIGTERM (force le recours à la terminaison brutale)
``--close-stdin``       fermer l'entrée standard (script qui ne relaie pas stdin)
``--crash-after S``     se terminer brutalement après S secondes
``--exit-code N``       code de sortie à utiliser en cas de plantage simulé
``--spawn-child``       lancer un sous-processus enfant (simule ``run.sh`` → Java)
``--heartbeat S``       émettre une ligne de log toutes les S secondes
``--survive-eof``       continuer après fermeture de l'entrée standard
"""

from __future__ import annotations

import argparse
import contextlib
import os
import signal
import sys
import threading
import time

START_TIME = time.time()


def emit(thread: str, level: str, message: str) -> None:
    """Écrit une ligne au format log4j d'un serveur Minecraft moderne."""
    stamp = time.strftime("%H:%M:%S")
    print(f"[{stamp}] [{thread}/{level}]: {message}", flush=True)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Faux serveur Minecraft pour les tests MSM")
    parser.add_argument("--startup-delay", type=float, default=0.05)
    parser.add_argument("--ready", dest="ready", action="store_true", default=True)
    parser.add_argument("--no-ready", dest="ready", action="store_false")
    parser.add_argument("--ignore-stop", action="store_true")
    #: Serveur qui ne répond pas aux commandes de sauvegarde : la sauvegarde à
    #: chaud doit alors être refusée plutôt que produire un monde incohérent.
    parser.add_argument("--ignore-save", action="store_true")
    parser.add_argument("--ignore-signals", action="store_true")
    parser.add_argument("--close-stdin", action="store_true")
    parser.add_argument("--crash-after", type=float, default=None)
    parser.add_argument("--exit-code", type=int, default=1)
    parser.add_argument("--spawn-child", action="store_true")
    parser.add_argument("--heartbeat", type=float, default=None)
    parser.add_argument("--survive-eof", action="store_true")
    parser.add_argument("--name", default="FakeServer")
    return parser.parse_args(argv)


def install_signal_handlers(ignore: bool) -> None:
    """Ignore SIGTERM si demandé, pour forcer l'étape de terminaison forcée."""
    if not ignore:
        return
    for name in ("SIGTERM", "SIGINT", "SIGBREAK"):
        sig = getattr(signal, name, None)
        if sig is not None:
            with contextlib.suppress(ValueError, OSError):
                signal.signal(sig, signal.SIG_IGN)


def spawn_child() -> None:
    """Lance un processus enfant durable — simule ``run.sh`` démarrant Java.

    L'enfant doit être terminé par l'arrêt du *groupe* de processus ; s'il survit,
    c'est que l'isolation est mal implémentée.
    """
    import subprocess

    subprocess.Popen(
        [sys.executable, "-c", "import time\nwhile True: time.sleep(3600)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def schedule_crash(delay: float, exit_code: int) -> None:
    """Programme une sortie brutale, sans passer par l'arrêt propre."""

    def _crash() -> None:
        time.sleep(delay)
        emit("Server thread", "ERROR", "Encountered an unexpected exception")
        sys.stdout.flush()
        os._exit(exit_code)

    threading.Thread(target=_crash, daemon=True).start()


def schedule_heartbeat(interval: float) -> None:
    def _beat() -> None:
        counter = 0
        while True:
            time.sleep(interval)
            counter += 1
            emit("Server thread", "INFO", f"Heartbeat {counter}")

    threading.Thread(target=_beat, daemon=True).start()


def startup_sequence(args: argparse.Namespace) -> None:
    emit("main", "INFO", f"Starting minecraft server version 1.21.1 ({args.name})")
    emit("main", "INFO", "Loading properties")
    emit("main", "INFO", "Default game type: SURVIVAL")
    emit("Server thread", "INFO", 'Preparing level "world"')
    time.sleep(args.startup_delay)
    if args.ready:
        elapsed = time.time() - START_TIME
        emit("Server thread", "INFO", f'Done ({elapsed:.3f}s)! For help, type "help"')


def handle_command(command: str, args: argparse.Namespace) -> bool:
    """Traite une commande. Renvoie ``True`` s'il faut s'arrêter."""
    if command == "stop":
        if args.ignore_stop:
            emit("Server thread", "INFO", "Ignoring stop (mode simulation)")
            return False
        emit("Server thread", "INFO", "Stopping the server")
        emit("Server thread", "INFO", "Saving worlds")
        emit("Server thread", "INFO", "ThreadedAnvilChunkStorage: All chunks are saved")
        return True

    if command.startswith("say "):
        emit("Server thread", "INFO", f"[Server] {command[4:]}")
    elif command == "list":
        emit("Server thread", "INFO", "There are 0 of a max of 20 players online:")
    elif command.startswith("join "):
        # Aide de test : simule l'arrivée d'un joueur.
        player = command[5:].strip()
        emit(
            "User Authenticator #1",
            "INFO",
            f"UUID of player {player} is 069a79f4-44e9-4726-a5be-fca90e38aaf5",
        )
        emit("Server thread", "INFO", f"{player} joined the game")
    elif command.startswith("leave "):
        emit("Server thread", "INFO", f"{command[6:].strip()} left the game")
    elif command == "save-off":
        if args.ignore_save:
            return False
        emit("Server thread", "INFO", "Automatic saving is now disabled")
    elif command.startswith("save-all"):
        if args.ignore_save:
            return False
        emit("Server thread", "INFO", "Saved the game")
    elif command == "save-on":
        emit("Server thread", "INFO", "Automatic saving is now enabled")
    else:
        emit("Server thread", "INFO", f"Unknown command: {command}")
    return False


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    install_signal_handlers(args.ignore_signals)
    if args.spawn_child:
        spawn_child()
    if args.crash_after is not None:
        schedule_crash(args.crash_after, args.exit_code)
    if args.heartbeat is not None:
        schedule_heartbeat(args.heartbeat)

    startup_sequence(args)

    if args.close_stdin:
        emit("Server thread", "INFO", "stdin closed by launcher (mode simulation)")
        sys.stdin.close()
        # Sans entrée standard, seul un signal peut arrêter ce processus.
        while True:
            time.sleep(3600)

    while True:
        try:
            raw = sys.stdin.readline()
        except (KeyboardInterrupt, ValueError):
            break
        if not raw:  # EOF : le parent a fermé le tube
            if args.survive_eof:
                # Comportement d'un vrai serveur Minecraft : la fin de son flux
                # d'entrée arrête la lecture des commandes, pas le serveur. C'est
                # ce qui lui permet de survivre à l'arrêt de MSM.
                emit("Server thread", "INFO", "Console input closed; server keeps running")
                while True:
                    time.sleep(3600)
            break
        command = raw.strip()
        if not command:
            continue
        if handle_command(command, args):
            break

    emit("Server thread", "INFO", "Server stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
