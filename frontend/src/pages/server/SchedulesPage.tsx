/**
 * Tâches programmées d'un serveur.
 *
 * L'écran répond à trois questions, dans cet ordre : que va-t-il se passer,
 * quand, et qu'est-il arrivé la dernière fois. Une planification silencieuse qui
 * échoue depuis trois semaines est pire que pas de planification du tout.
 */

import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { CalendarClock, Pause, Pencil, Play, Plus, Trash2 } from 'lucide-react'
import { api } from '@/lib/api'
import { useToasts } from '@/stores/toasts'
import { formatRelative } from '@/lib/format'
import type { Schedule } from '@/lib/types'
import { useServerContext } from './context'
import { Badge, Card, CardHeader, EmptyState, LoadingBlock } from '@/components/ui/primitives'
import { Button } from '@/components/ui/Button'
import { ErrorPanel } from '@/components/common/ErrorPanel'
import { ConfirmDialog } from '@/components/common/ConfirmDialog'
import {
  ACTION_LABELS,
  ScheduleEditor,
  type ScheduleDraft,
} from '@/components/schedules/ScheduleEditor'
import { cn } from '@/lib/cn'
import { t, type MessageKey } from '@/i18n'

const STATUS_STYLES: Record<string, string> = {
  SUCCESS: 'bg-emerald-500/10 text-emerald-300 ring-emerald-500/30',
  FAILED: 'bg-red-500/10 text-red-300 ring-red-500/30',
  MISSED: 'bg-amber-500/10 text-amber-300 ring-amber-500/30',
  SKIPPED: 'bg-slate-700/40 text-slate-300 ring-slate-600',
  NEVER: 'bg-slate-700/40 text-slate-400 ring-slate-700',
}

function statusLabel(status: string): string {
  return t(`schedules.status.${status}` as MessageKey)
}

function toDraft(schedule: Schedule): ScheduleDraft {
  return {
    name: schedule.name,
    action: schedule.action,
    rule: schedule.rule,
    payload: schedule.payload,
    enabled: schedule.enabled,
  }
}

export function SchedulesPage() {
  const { server } = useServerContext()
  const queryClient = useQueryClient()
  const push = useToasts((state) => state.push)
  const pushError = useToasts((state) => state.pushError)

  const [editing, setEditing] = useState<Schedule | 'new' | null>(null)
  const [toDelete, setToDelete] = useState<Schedule | null>(null)

  const schedules = useQuery({
    queryKey: ['schedules', server.id],
    queryFn: () => api.schedules.list(server.id),
  })

  // Les tâches « événement » ont besoin de la liste pour être choisies.
  const events = useQuery({
    queryKey: ['events', server.id],
    queryFn: () => api.events.list(server.id),
  })

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ['schedules', server.id] })

  const save = useMutation({
    mutationFn: (draft: ScheduleDraft) =>
      editing && editing !== 'new'
        ? api.schedules.update(server.id, editing.id, {
            name: draft.name,
            rule: draft.rule,
            payload: draft.payload,
            enabled: draft.enabled,
          })
        : api.schedules.create(server.id, {
            name: draft.name,
            action: draft.action,
            rule: draft.rule,
            payload: draft.payload,
            enabled: draft.enabled,
          }),
    onSuccess: (schedule) => {
      push({
        kind: 'success',
        title: t('schedules.saved', { name: schedule.name }),
        detail: schedule.next_run_at
          ? t('schedules.nextRun', { when: formatRelative(schedule.next_run_at) })
          : t('schedules.disabledTask'),
      })
      setEditing(null)
      void invalidate()
    },
  })

  const toggle = useMutation({
    mutationFn: (schedule: Schedule) =>
      api.schedules.update(server.id, schedule.id, { enabled: !schedule.enabled }),
    onSuccess: () => void invalidate(),
    onError: (error) => pushError(error),
  })

  const run = useMutation({
    mutationFn: (schedule: Schedule) => api.schedules.run(server.id, schedule.id),
    onSuccess: (schedule) => {
      push({
        kind: schedule.last_status === 'FAILED' ? 'error' : 'success',
        title: t('schedules.ran', { name: schedule.name, status: statusLabel(schedule.last_status) }),
        detail: schedule.last_error ?? undefined,
      })
      void invalidate()
    },
    onError: (error) => pushError(error),
  })

  const remove = useMutation({
    mutationFn: (schedule: Schedule) => api.schedules.remove(server.id, schedule.id),
    onSuccess: () => {
      push({ kind: 'success', title: t('schedules.deleted') })
      setToDelete(null)
      void invalidate()
    },
    onError: (error) => pushError(error),
  })

  if (schedules.isLoading) return <LoadingBlock />

  const items = schedules.data ?? []

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-4xl space-y-5 p-4 sm:p-6">
        <ErrorPanel error={schedules.error} />

        <Card>
          <CardHeader
            title={t('schedules.title', { count: items.length })}
            subtitle={t('schedules.subtitle')}
            action={
              <Button
                size="sm"
                variant="primary"
                icon={<Plus className="size-3.5" />}
                onClick={() => setEditing('new')}
              >
                {t('schedules.new')}
              </Button>
            }
          />

          {items.length === 0 ? (
            <EmptyState
              icon={<CalendarClock className="size-8" />}
              title={t('schedules.empty')}
              description={t('schedules.emptyHint')}
            />
          ) : (
            <ul className="divide-y divide-slate-800/60">
              {items.map((schedule) => (
                <li key={schedule.id} className="flex items-start gap-3 px-5 py-3">
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <span
                        className={cn(
                          'text-sm',
                          schedule.enabled ? 'text-slate-100' : 'text-slate-500 line-through',
                        )}
                      >
                        {schedule.name}
                      </span>
                      <Badge>{t(ACTION_LABELS[schedule.action])}</Badge>
                      <Badge className={cn(STATUS_STYLES[schedule.last_status])}>
                        {statusLabel(schedule.last_status)}
                      </Badge>
                    </div>

                    <p className="mt-0.5 text-xs text-slate-500">
                      {schedule.summary}
                      {schedule.enabled && schedule.next_run_at ? (
                        <>
                          {t('schedules.next')}
                          <span className="text-slate-400">
                            {formatRelative(schedule.next_run_at)}
                          </span>
                        </>
                      ) : (
                        t('schedules.paused')
                      )}
                    </p>

                    {schedule.last_error ? (
                      <p className="mt-1 text-xs text-red-400">{schedule.last_error}</p>
                    ) : null}
                  </div>

                  <div className="flex shrink-0 items-center gap-1">
                    <Button
                      size="sm"
                      variant="ghost"
                      icon={<Play className="size-3.5" />}
                      loading={run.isPending && run.variables?.id === schedule.id}
                      onClick={() => run.mutate(schedule)}
                    >
                      {t('schedules.run')}
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      icon={
                        schedule.enabled ? (
                          <Pause className="size-3.5" />
                        ) : (
                          <Play className="size-3.5" />
                        )
                      }
                      onClick={() => toggle.mutate(schedule)}
                    >
                      <span className="sr-only">
                        {schedule.enabled ? t('schedules.pause') : t('schedules.resume')}
                      </span>
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      icon={<Pencil className="size-3.5" />}
                      onClick={() => setEditing(schedule)}
                    >
                      <span className="sr-only">{t('common.edit')}</span>
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      icon={<Trash2 className="size-3.5" />}
                      onClick={() => setToDelete(schedule)}
                    >
                      <span className="sr-only">{t('common.delete')}</span>
                    </Button>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>

      {editing ? (
        <ScheduleEditor
          key={editing === 'new' ? 'new' : editing.id}
          open
          events={events.data ?? []}
          initial={editing === 'new' ? undefined : toDraft(editing)}
          locked={editing !== 'new'}
          saving={save.isPending}
          error={save.error}
          onSave={(draft) => save.mutate(draft)}
          onClose={() => setEditing(null)}
        />
      ) : null}

      <ConfirmDialog
        open={toDelete !== null}
        title={t('schedules.deleteTitle', { name: toDelete?.name ?? '' })}
        consequence={t('schedules.deleteConsequence')}
        confirmLabel={t('common.delete')}
        danger
        loading={remove.isPending}
        onConfirm={() => toDelete && remove.mutate(toDelete)}
        onClose={() => setToDelete(null)}
      />
    </div>
  )
}
