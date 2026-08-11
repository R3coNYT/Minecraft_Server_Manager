"""Sauvegarde à chaud : suspendre l'écriture du monde le temps de la copie.

Un serveur démarré écrit ses régions en continu. Copier pendant qu'il écrit
produit une archive dont certains fichiers datent d'avant et d'autres d'après —
un monde légèrement incohérent, qui se restaure sans erreur et se découvre
corrompu des semaines plus tard.

La séquence est celle que pratiquent les administrateurs depuis toujours :

1. ``save-off``  — le serveur cesse d'écrire ;
2. ``save-all flush`` — il vide ce qu'il a en mémoire, puis confirme ;
3. copie ;
4. ``save-on`` — l'écriture reprend.

Deux garde-fous. La confirmation est **attendue** : sans elle, on copierait
pendant que le serveur écrit encore, ce que l'on voulait précisément éviter — la
sauvegarde est alors refusée plutôt que douteuse. Et ``save-on`` est envoyé dans
un ``finally`` : un serveur laissé avec l'écriture désactivée perdrait tout au
prochain plantage.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, Protocol

from msm.bus import EventBus, topics
from msm.core.log_line import LogLine
from msm.exceptions import MsmError
from msm.logging_conf import get_logger

logger = get_logger(__name__)

#: Attente maximale d'une confirmation. Un serveur chargé peut mettre plusieurs
#: secondes à vider ses régions ; au-delà, quelque chose ne va pas.
CONFIRMATION_TIMEOUT_S = 60.0

#: Les motifs sont **ancrés en début de message** : un joueur écrivant
#: « Saved the game » dans le chat produit la ligne « <Flavien> Saved the game »,
#: qui confirmerait à tort la fin de l'écriture et laisserait copier un monde
#: encore en mouvement.
#:
#: « Automatic saving is now disabled » (Vanilla récent),
#: « Turned off world auto-saving » (Bukkit et versions anciennes).
SAVE_OFF_RE = re.compile(
    r"^(?:Automatic saving is now disabled|Turned off world auto-saving)", re.IGNORECASE
)
#: « Saved the game » (Vanilla), « Saved the world » (Bukkit).
SAVED_RE = re.compile(r"^(?:Saved the game|Saved the world)", re.IGNORECASE)


class BackupNotSafe(MsmError):
    """Le serveur n'a pas confirmé la suspension des écritures."""

    code = "BACKUP_NOT_SAFE"
    status_code = 409


class _Runtime(Protocol):
    """Le strict nécessaire, pour que ce module reste testable sans processus."""

    id: int

    async def send_command(self, command: str, *, actor: str | None = ...) -> str: ...


async def _command_and_wait(
    runtime: _Runtime,
    bus: EventBus,
    command: str,
    pattern: re.Pattern[str],
    *,
    actor: str,
    timeout: float,
) -> bool:
    """Envoie une commande et attend la ligne qui la confirme.

    L'abonnement est ouvert **avant** l'envoi : une réponse arrivant en quelques
    millisecondes serait sinon manquée, et la sauvegarde refusée à tort.
    """
    subscription = bus.subscribe(topics.server_topic(runtime.id, topics.LOG))
    try:
        await runtime.send_command(command, actor=actor)
        async with asyncio.timeout(timeout):
            async for event in subscription:
                payload: Any = event.payload
                text = payload.text if isinstance(payload, LogLine) else str(payload)
                if pattern.search(text):
                    return True
    except TimeoutError:
        return False
    finally:
        subscription.close()
    return False


@asynccontextmanager
async def frozen_world(
    runtime: _Runtime,
    bus: EventBus,
    *,
    actor: str = "sauvegarde",
    timeout: float = CONFIRMATION_TIMEOUT_S,
) -> AsyncIterator[None]:
    """Suspend l'écriture du monde pendant la durée du bloc."""
    confirmed = await _command_and_wait(
        runtime, bus, "save-off", SAVE_OFF_RE, actor=actor, timeout=timeout
    )
    if not confirmed:
        # Rien n'a été suspendu, mais la commande est peut-être passée sans que
        # sa réponse soit reconnue : rétablir l'écriture est le seul geste sûr.
        await _restore(runtime, actor)
        raise BackupNotSafe(
            "Le serveur n'a pas confirmé la suspension des sauvegardes.",
            cause=(
                "Aucune réponse à `save-off` en "
                f"{timeout:.0f} s. Copier maintenant produirait un monde incohérent."
            ),
            remediation=(
                "Vérifier que le serveur répond dans la console, "
                "ou l'arrêter puis relancer la sauvegarde."
            ),
        )

    try:
        flushed = await _command_and_wait(
            runtime, bus, "save-all flush", SAVED_RE, actor=actor, timeout=timeout
        )
        if not flushed:
            raise BackupNotSafe(
                "Le serveur n'a pas confirmé l'écriture du monde sur le disque.",
                cause=f"Aucune réponse à `save-all flush` en {timeout:.0f} s.",
                remediation=(
                    "Vérifier que le serveur répond dans la console, "
                    "ou l'arrêter puis relancer la sauvegarde."
                ),
            )
        yield
    finally:
        await _restore(runtime, actor)


async def _restore(runtime: _Runtime, actor: str) -> None:
    """Réactive l'écriture. Ne lève jamais : on est dans un ``finally``."""
    try:
        await runtime.send_command("save-on", actor=actor)
    except Exception as exc:
        logger.error(
            "backup_save_on_failed",
            server_id=getattr(runtime, "id", None),
            error=str(exc),
        )
