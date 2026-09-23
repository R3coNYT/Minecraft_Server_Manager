/**
 * Actions de modération sur un joueur.
 *
 * Chaque action affiche la **commande exacte** qui sera exécutée. L'administrateur
 * voit donc ce que MSM va faire avant de confirmer, et apprend au passage la
 * commande Minecraft correspondante.
 */

import { useEffect, useRef, useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Ban, Gift, MoreHorizontal, Shield, ShieldOff, Skull, UserMinus, UserCheck } from 'lucide-react'
import { api } from '@/lib/api'
import { hasPermission, useMe } from '@/hooks/useApi'
import { useToasts } from '@/stores/toasts'
import type { Player } from '@/lib/types'
import { Button } from '@/components/ui/Button'
import { Dialog } from '@/components/ui/Dialog'
import { ConfirmDialog } from '@/components/common/ConfirmDialog'
import { Field, Input } from '@/components/ui/primitives'
import { ErrorPanel } from '@/components/common/ErrorPanel'
import { cn } from '@/lib/cn'
import { t } from '@/i18n'

type ActionKey = 'op' | 'deop' | 'kick' | 'ban' | 'unban' | 'kill' | 'give'

interface PlayerActionsMenuProps {
  serverId: number
  player: Player
  serverRunning: boolean
}

export function PlayerActionsMenu({ serverId, player, serverRunning }: PlayerActionsMenuProps) {
  const queryClient = useQueryClient()
  const { data: me } = useMe()
  const push = useToasts((state) => state.push)
  const pushError = useToasts((state) => state.pushError)

  const [open, setOpen] = useState(false)
  const [pending, setPending] = useState<ActionKey | null>(null)
  const [reason, setReason] = useState('')
  const [item, setItem] = useState('diamond')
  const [count, setCount] = useState('1')
  const containerRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const onClickOutside = (event: MouseEvent) => {
      if (!containerRef.current?.contains(event.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onClickOutside)
    return () => document.removeEventListener('mousedown', onClickOutside)
  }, [open])

  const refresh = () =>
    void queryClient.invalidateQueries({ queryKey: ['players', serverId] })

  const run = useMutation({
    mutationFn: async (action: ActionKey) => {
      const name = player.username
      switch (action) {
        case 'op':
          return api.players.op(serverId, name)
        case 'deop':
          return api.players.deop(serverId, name)
        case 'kick':
          return api.players.kick(serverId, name, reason)
        case 'ban':
          return api.players.ban(serverId, name, reason)
        case 'unban':
          return api.players.unban(serverId, name)
        case 'kill':
          return api.players.kill(serverId, name)
        case 'give':
          return api.players.give(serverId, name, item.trim(), Number(count) || 1)
      }
    },
    onSuccess: (result) => {
      push({ kind: 'success', title: t('playerActions.done'), detail: result?.command })
      setPending(null)
      setReason('')
      refresh()
    },
    onError: (error) => pushError(error),
  })

  const can = {
    op: hasPermission(me, 'player:op'),
    kick: hasPermission(me, 'player:kick'),
    ban: hasPermission(me, 'player:ban'),
    kill: hasPermission(me, 'player:kill'),
    give: hasPermission(me, 'player:give'),
  }

  const entries: { key: ActionKey; label: string; icon: typeof Shield; allowed: boolean; show: boolean }[] = [
    { key: 'op', label: t('playerActions.op'), icon: Shield, allowed: can.op, show: !player.is_op },
    { key: 'deop', label: t('playerActions.deop'), icon: ShieldOff, allowed: can.op, show: player.is_op },
    { key: 'give', label: t('playerActions.give'), icon: Gift, allowed: can.give, show: player.online },
    { key: 'kick', label: t('playerActions.kick'), icon: UserMinus, allowed: can.kick, show: player.online },
    { key: 'kill', label: t('playerActions.kill'), icon: Skull, allowed: can.kill, show: player.online },
    { key: 'ban', label: t('playerActions.ban'), icon: Ban, allowed: can.ban, show: !player.is_banned },
    { key: 'unban', label: t('playerActions.unban'), icon: UserCheck, allowed: can.ban, show: player.is_banned },
  ]

  const available = entries.filter((entry) => entry.show && entry.allowed)
  if (available.length === 0) return null

  const commandPreview: Record<ActionKey, string> = {
    op: `op ${player.username}`,
    deop: `deop ${player.username}`,
    kick: `kick ${player.username}${reason ? ` ${reason}` : ''}`,
    ban: `ban ${player.username}${reason ? ` ${reason}` : ''}`,
    unban: `pardon ${player.username}`,
    kill: `kill ${player.username}`,
    give: `give ${player.username} ${item} ${count}`,
  }

  const needsForm = pending === 'give' || pending === 'kick' || pending === 'ban'

  return (
    <div className="relative" ref={containerRef}>
      <Button
        size="sm"
        variant="ghost"
        icon={<MoreHorizontal className="size-4" />}
        onClick={() => setOpen((value) => !value)}
        disabled={!serverRunning}
        title={serverRunning ? t('playerActions.actions') : t('playerActions.mustRun')}
      >
        <span className="sr-only">{t('playerActions.actionsOn', { name: player.username })}</span>
      </Button>

      {open ? (
        <div className="absolute right-0 z-20 mt-1 w-56 overflow-hidden rounded-lg border border-slate-700 bg-slate-900 py-1 shadow-xl">
          {available.map((entry) => (
            <button
              key={entry.key}
              onClick={() => {
                setOpen(false)
                setPending(entry.key)
              }}
              className={cn(
                'flex w-full items-center gap-2.5 px-3 py-2 text-left text-sm transition-colors',
                entry.key === 'ban' || entry.key === 'kill'
                  ? 'text-red-300 hover:bg-red-950/40'
                  : 'text-slate-200 hover:bg-slate-800',
              )}
            >
              <entry.icon className="size-4" />
              {entry.label}
            </button>
          ))}
        </div>
      ) : null}

      {/* Actions immédiates : une simple confirmation suffit. */}
      <ConfirmDialog
        open={pending !== null && !needsForm}
        title={`${entries.find((e) => e.key === pending)?.label ?? t('playerActions.action')} — ${player.username}`}
        consequence={
          pending === 'op'
            ? t('playerActions.opConsequence')
            : pending === 'kill'
              ? t('playerActions.killConsequence')
              : undefined
        }
        description={pending ? t('playerActions.command', { command: commandPreview[pending] }) : undefined}
        confirmLabel={t('playerActions.run')}
        danger={pending === 'kill' || pending === 'op'}
        loading={run.isPending}
        onConfirm={() => pending && run.mutate(pending)}
        onClose={() => setPending(null)}
      />

      {/* Actions nécessitant une saisie. */}
      <Dialog
        open={needsForm}
        onClose={() => setPending(null)}
        title={`${entries.find((e) => e.key === pending)?.label ?? ''} — ${player.username}`}
        size="sm"
        footer={
          <>
            <Button variant="ghost" onClick={() => setPending(null)}>
              {t('common.cancel')}
            </Button>
            <Button
              variant={pending === 'ban' ? 'danger' : 'primary'}
              loading={run.isPending}
              onClick={() => pending && run.mutate(pending)}
            >
              {t('playerActions.run')}
            </Button>
          </>
        }
      >
        <div className="space-y-4">
          {pending === 'give' ? (
            <div className="grid grid-cols-3 gap-3">
              <div className="col-span-2">
                <Field label={t('playerActions.item')} hint={t('playerActions.itemHint')}>
                  <Input
                    value={item}
                    onChange={(event) => setItem(event.target.value)}
                    spellCheck={false}
                    autoFocus
                  />
                </Field>
              </div>
              <Field label={t('playerActions.quantity')}>
                <Input
                  type="number"
                  min={1}
                  max={6400}
                  value={count}
                  onChange={(event) => setCount(event.target.value)}
                />
              </Field>
            </div>
          ) : (
            <Field label={t('playerActions.reason')} hint={t('playerActions.reasonHint')}>
              <Input
                value={reason}
                onChange={(event) => setReason(event.target.value)}
                placeholder={t('playerActions.reasonPlaceholder')}
                autoFocus
              />
            </Field>
          )}

          <p className="rounded-lg bg-slate-950/60 px-3 py-2 font-mono text-xs text-slate-400">
            {pending ? commandPreview[pending] : ''}
          </p>

          <ErrorPanel error={run.error} />
        </div>
      </Dialog>
    </div>
  )
}
