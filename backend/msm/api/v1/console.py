"""Console : historique, recherche et envoi de commandes.

Le **temps réel passe exclusivement par le WebSocket**. Ces routes servent
l'historique, la recherche et la reprise après reconnexion — jamais un flux
poussé par interrogations répétées.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from msm.api.deps import ClientIp, CsrfProtected, DbSession, ServerAccess, SupervisorDep
from msm.api.schemas import (
    CommandInspectOut,
    CommandOut,
    CommandRequest,
    LogLineOut,
    LogsOut,
)
from msm.core.permissions import Permission
from msm.services.console_service import ConsoleService

router = APIRouter(prefix="/servers/{server_id}", tags=["console"])


def _console(session: DbSession, supervisor: SupervisorDep) -> ConsoleService:
    return ConsoleService(session, supervisor)


ConsoleDep = Annotated[ConsoleService, Depends(_console)]


@router.get("/logs", response_model=LogsOut, summary="Historique de console")
async def get_logs(
    access: ServerAccess,
    supervisor: SupervisorDep,
    limit: Annotated[int, Query(ge=1, le=5000)] = 500,
    since: Annotated[int | None, Query(description="Reprendre après ce numéro de ligne")] = None,
    before: Annotated[int | None, Query(description="Lignes précédant ce numéro")] = None,
    search: Annotated[str | None, Query(min_length=1, max_length=200)] = None,
    regex: bool = False,
) -> LogsOut:
    """Fenêtre d'historique.

    * ``since`` — reprise après coupure : renvoie exactement la suite ;
    * ``before`` — défilement vers le haut ;
    * ``search`` — recherche dans l'historique conservé.
    """
    server, context = access
    context.require(Permission.CONSOLE_READ, action="lire la console")

    runtime = supervisor.find(server.id)
    if runtime is None:
        return LogsOut(lines=[])

    if search:
        lines = runtime.logs_search(search, limit=limit, use_regex=regex)
    elif since is not None:
        lines = runtime.logs_since(since, limit=limit)
    elif before is not None:
        lines = runtime.logs_before(before, limit=limit)
    else:
        lines = runtime.logs_tail(limit)

    snapshot = runtime.snapshot()
    return LogsOut(
        lines=[LogLineOut(**line.to_dict()) for line in lines],
        first_seq=lines[0].seq if lines else None,
        last_seq=lines[-1].seq if lines else None,
        dropped=snapshot["log_dropped"],
    )


@router.post(
    "/command",
    response_model=CommandOut,
    summary="Envoyer une commande",
    dependencies=[CsrfProtected],
)
async def send_command(
    payload: CommandRequest,
    access: ServerAccess,
    console: ConsoleDep,
    ip: ClientIp,
) -> CommandOut:
    """Transmet une commande à la console du serveur.

    Une commande sensible exige la permission dédiée **et** ``confirm: true`` ;
    sinon la réponse est un 428 décrivant précisément ce que la commande ferait.
    """
    server, context = access
    result = await console.send_command(
        server,
        payload.command,
        context=context,
        confirm=payload.confirm,
        ip_address=ip,
    )
    return CommandOut(**result)


@router.post(
    "/command/inspect",
    response_model=CommandInspectOut,
    summary="Analyser une commande sans l'exécuter",
    dependencies=[CsrfProtected],
)
async def inspect_command(
    payload: CommandRequest,
    access: ServerAccess,
    console: ConsoleDep,
) -> CommandInspectOut:
    """Renseigne la boîte de confirmation avant l'envoi."""
    _, context = access
    context.require(Permission.CONSOLE_READ, action="analyser une commande")
    return CommandInspectOut(**console.inspect(payload.command))
