/**
 * Console d'un serveur.
 *
 * L'historique est chargé une fois en REST, puis le flux arrive par WebSocket.
 * Aucune interrogation périodique : c'était le défaut majeur de la version 1,
 * qui retéléchargeait l'intégralité du fichier de log cinq fois par seconde.
 */

import { useEffect } from 'react'
import { useQuery } from '@tanstack/react-query'
import { hasPermission, useMe } from '@/hooks/useApi'
import { api } from '@/lib/api'
import { useRealtime } from '@/stores/realtime'
import { useServerContext } from './context'
import { LogView } from '@/components/console/LogView'
import { CommandInput } from '@/components/console/CommandInput'
import { LoadingBlock } from '@/components/ui/primitives'
import { ErrorPanel } from '@/components/common/ErrorPanel'

export function ConsolePage() {
  const { server, status } = useServerContext()
  const { data: me } = useMe()

  const lines = useRealtime((state) => state.logs[server.id]) ?? []
  const missed = useRealtime((state) => state.missedLines[server.id]) ?? 0
  const appendLogs = useRealtime((state) => state.appendLogs)
  const clearLogs = useRealtime((state) => state.clearLogs)

  // Amorce : le WebSocket ne pousse que ce qui arrive après l'abonnement. Cette
  // requête garantit un contenu dès l'arrivée sur l'onglet ; le store écarte les
  // lignes déjà reçues grâce à leur numéro de séquence.
  const { data: initial, isLoading, error } = useQuery({
    queryKey: ['logs-initial', server.id],
    queryFn: () => api.console.logs(server.id, { limit: 500 }),
    staleTime: Infinity,
    retry: false,
  })

  useEffect(() => {
    if (initial?.lines?.length) appendLogs(server.id, initial.lines)
  }, [initial, server.id, appendLogs])

  const canWrite = hasPermission(me, 'console:write')
  const writable = status?.console_writable ?? false
  const running = status?.state === 'ONLINE' || status?.state === 'STARTING'

  const disabledReason = !canWrite
    ? "Votre rôle ne permet pas d'écrire dans la console."
    : !running
      ? 'Le serveur doit être démarré pour recevoir des commandes.'
      : !writable
        ? "L'entrée standard du serveur n'est pas accessible : ce script de démarrage ne la transmet pas. Activer le mode PTY ou configurer RCON."
        : undefined

  if (isLoading) return <LoadingBlock label="Chargement de l'historique…" />

  return (
    <div className="flex h-full min-h-0 flex-col">
      {error ? (
        <div className="p-4">
          <ErrorPanel error={error} compact />
        </div>
      ) : null}

      <LogView
        lines={lines}
        missed={missed}
        onClear={() => clearLogs(server.id)}
        emptyHint={
          running
            ? 'Le serveur tourne mais n’a encore rien écrit.'
            : 'Démarrer le serveur pour voir apparaître sa console.'
        }
      />

      <CommandInput
        serverId={server.id}
        serverName={server.name}
        disabled={Boolean(disabledReason)}
        disabledReason={disabledReason}
      />
    </div>
  )
}
