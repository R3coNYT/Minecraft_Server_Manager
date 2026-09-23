/** Boutons de cycle de vie d'un serveur, avec confirmations adaptées. */

import { useState } from 'react'
import { Play, RotateCw, Square, Zap } from 'lucide-react'
import type { ServerState } from '@/lib/types'
import { useLifecycleActions } from '@/hooks/useApi'
import { useToasts } from '@/stores/toasts'
import { Button } from '@/components/ui/Button'
import { ConfirmDialog } from '@/components/common/ConfirmDialog'
import { locale, t } from '@/i18n'

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
            ? t('actions.forcedTitle', { name: serverName })
            : t('actions.stoppedTitle', { name: serverName }),
          detail: result.forced
            ? t('actions.forcedDetail')
            : t('actions.stoppedDetail', {
                seconds: result.duration_s.toLocaleString(locale(), { maximumFractionDigits: 1 }),
              }),
        })
      } else if (action === 'restart') {
        await actions.restart.mutateAsync()
        push({ kind: 'success', title: t('actions.restarting', { name: serverName }) })
      } else {
        await actions.kill.mutateAsync()
        push({
          kind: 'warning',
          title: t('actions.killedTitle', { name: serverName }),
          detail: t('actions.killedDetail'),
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
      push({ kind: 'success', title: t('actions.starting', { name: serverName }) })
    } catch (error) {
      pushError(error, t('actions.startFailed'))
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
            {t('actions.start')}
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
              {t('actions.restart')}
            </Button>
            <Button
              size={size}
              variant="danger"
              icon={<Square className="size-4" />}
              disabled={!canStop || busy}
              loading={actions.stop.isPending}
              onClick={() => setPending('stop')}
            >
              {t('actions.stop')}
            </Button>
          </>
        )}

        {running && canKill ? (
          <Button
            size={size}
            variant="ghost"
            icon={<Zap className="size-4" />}
            onClick={() => setPending('kill')}
            title={t('actions.killHint')}
          >
            {t('actions.kill')}
          </Button>
        ) : null}
      </div>

      <ConfirmDialog
        open={pending === 'stop'}
        title={t('actions.stopTitle', { name: serverName })}
        consequence={t('actions.stopConsequence')}
        confirmLabel={t('actions.stop')}
        danger
        loading={actions.stop.isPending}
        onConfirm={() => void run('stop')}
        onClose={() => setPending(null)}
      />

      <ConfirmDialog
        open={pending === 'restart'}
        title={t('actions.restartTitle', { name: serverName })}
        consequence={t('actions.restartConsequence')}
        confirmLabel={t('actions.restart')}
        loading={actions.restart.isPending}
        onConfirm={() => void run('restart')}
        onClose={() => setPending(null)}
      />

      <ConfirmDialog
        open={pending === 'kill'}
        title={t('actions.killTitle', { name: serverName })}
        consequence={t('actions.killConsequence')}
        confirmLabel={t('actions.killConfirm')}
        danger
        requireTyping={serverName}
        loading={actions.kill.isPending}
        onConfirm={() => void run('kill')}
        onClose={() => setPending(null)}
      />
    </>
  )
}
