/** Boutons de cycle de vie d'un serveur, avec confirmations adaptées. */

import { useState } from 'react'
import { Play, RotateCw, Square, Zap } from 'lucide-react'
import type { ServerState } from '@/lib/types'
import { useLifecycleActions } from '@/hooks/useApi'
import { useToasts } from '@/stores/toasts'
import { Button } from '@/components/ui/Button'
import { ConfirmDialog } from '@/components/common/ConfirmDialog'

interface ServerActionsProps {
  serverId: number
  serverName: string
  state: ServerState
  canStart: boolean
  canStop: boolean
  canRestart: boolean
  canKill: boolean
  size?: 'sm' | 'md'
}

type PendingAction = 'stop' | 'restart' | 'kill'

export function ServerActions({
  serverId,
  serverName,
  state,
  canStart,
  canStop,
  canRestart,
  canKill,
  size = 'md',
}: ServerActionsProps) {
  const actions = useLifecycleActions(serverId)
  const [pending, setPending] = useState<PendingAction | null>(null)
  const push = useToasts((state_) => state_.push)
  const pushError = useToasts((state_) => state_.pushError)

  const running = state === 'ONLINE' || state === 'STARTING' || state === 'UNKNOWN'
  const busy = state === 'STOPPING'

  const run = async (action: PendingAction) => {
    try {
      if (action === 'stop') {
        const result = await actions.stop.mutateAsync()
        push({
          kind: result.forced ? 'warning' : 'success',
          title: result.forced
            ? `« ${serverName} » a dû être arrêté de force`
            : `« ${serverName} » est arrêté`,
          detail: result.forced
            ? "Le serveur n'a pas répondu à la commande d'arrêt propre."
            : `Arrêt en ${result.duration_s.toFixed(1).replace('.', ',')} s.`,
        })
      } else if (action === 'restart') {
        await actions.restart.mutateAsync()
        push({ kind: 'success', title: `« ${serverName} » redémarre` })
      } else {
        await actions.kill.mutateAsync()
        push({
          kind: 'warning',
          title: `« ${serverName} » a été terminé`,
          detail: "Le monde n'a pas été sauvegardé.",
        })
      }
      setPending(null)
    } catch (error) {
      pushError(error)
      setPending(null)
    }
  }

  const start = async () => {
    try {
      await actions.start.mutateAsync()
      push({ kind: 'success', title: `« ${serverName} » démarre` })
    } catch (error) {
      pushError(error, 'Démarrage impossible')
    }
  }

  return (
    <>
      <div className="flex flex-wrap items-center gap-2">
        {!running ? (
          <Button
            size={size}
            variant="primary"
            icon={<Play className="size-4" />}
            disabled={!canStart || busy}
            loading={actions.start.isPending}
            onClick={() => void start()}
          >
            Démarrer
          </Button>
        ) : (
          <>
            <Button
              size={size}
              variant="secondary"
              icon={<RotateCw className="size-4" />}
              disabled={!canRestart || busy}
              loading={actions.restart.isPending}
              onClick={() => setPending('restart')}
            >
              Redémarrer
            </Button>
            <Button
              size={size}
              variant="danger"
              icon={<Square className="size-4" />}
              disabled={!canStop || busy}
              loading={actions.stop.isPending}
              onClick={() => setPending('stop')}
            >
              Arrêter
            </Button>
          </>
        )}

        {running && canKill ? (
          <Button
            size={size}
            variant="ghost"
            icon={<Zap className="size-4" />}
            onClick={() => setPending('kill')}
            title="Terminer le processus sans sauvegarder"
          >
            Forcer
          </Button>
        ) : null}
      </div>

      <ConfirmDialog
        open={pending === 'stop'}
        title={`Arrêter « ${serverName} » ?`}
        consequence="Les joueurs connectés seront déconnectés. Le monde est sauvegardé avant l'arrêt."
        confirmLabel="Arrêter"
        danger
        loading={actions.stop.isPending}
        onConfirm={() => void run('stop')}
        onClose={() => setPending(null)}
      />

      <ConfirmDialog
        open={pending === 'restart'}
        title={`Redémarrer « ${serverName} » ?`}
        consequence="Les joueurs seront déconnectés le temps du redémarrage."
        confirmLabel="Redémarrer"
        loading={actions.restart.isPending}
        onConfirm={() => void run('restart')}
        onClose={() => setPending(null)}
      />

      <ConfirmDialog
        open={pending === 'kill'}
        title={`Forcer l'arrêt de « ${serverName} » ?`}
        consequence="Le processus est terminé immédiatement : le monde n'est PAS sauvegardé et les dernières minutes de jeu seront perdues. À réserver aux serveurs qui ne répondent plus."
        confirmLabel="Terminer le processus"
        danger
        requireTyping={serverName}
        loading={actions.kill.isPending}
        onConfirm={() => void run('kill')}
        onClose={() => setPending(null)}
      />
    </>
  )
}
