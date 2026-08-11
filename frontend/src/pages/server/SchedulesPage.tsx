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

const STATUS_STYLES: Record<string, string> = {
  SUCCESS: 'bg-emerald-500/10 text-emerald-300 ring-emerald-500/30',
  FAILED: 'bg-red-500/10 text-red-300 ring-red-500/30',
  MISSED: 'bg-amber-500/10 text-amber-300 ring-amber-500/30',
  SKIPPED: 'bg-slate-700/40 text-slate-300 ring-slate-600',
  NEVER: 'bg-slate-700/40 text-slate-400 ring-slate-700',
}

const STATUS_LABELS: Record<string, string> = {
  SUCCESS: 'réussie',
  FAILED: 'échec',
  MISSED: 'manquée',
  SKIPPED: 'sans objet',
  NEVER: 'jamais exécutée',
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
        title: `Tâche « ${schedule.name} » enregistrée`,
        detail: schedule.next_run_at
          ? `Prochaine exécution ${formatRelative(schedule.next_run_at)}.`
          : 'Tâche désactivée.',
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
        title: `Tâche « ${schedule.name} » : ${STATUS_LABELS[schedule.last_status]}`,
        detail: schedule.last_error ?? undefined,
      })
      void invalidate()
    },
    onError: (error) => pushError(error),
  })

  const remove = useMutation({
    mutationFn: (schedule: Schedule) => api.schedules.remove(server.id, schedule.id),
    onSuccess: () => {
      push({ kind: 'success', title: 'Tâche supprimée' })
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
            title={`Tâches programmées (${items.length})`}
            subtitle="Sauvegardes, redémarrages et événements automatiques."
            action={
              <Button
                size="sm"
                variant="primary"
                icon={<Plus className="size-3.5" />}
                onClick={() => setEditing('new')}
              >
                Nouvelle tâche
              </Button>
            }
          />

          {items.length === 0 ? (
            <EmptyState
              icon={<CalendarClock className="size-8" />}
              title="Aucune tâche programmée"
              description="Une sauvegarde nocturne est ce qui sauve un serveur le jour où plus rien ne va."
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
                      <Badge>{ACTION_LABELS[schedule.action]}</Badge>
                      <Badge className={cn(STATUS_STYLES[schedule.last_status])}>
                        {STATUS_LABELS[schedule.last_status]}
                      </Badge>
                    </div>

                    <p className="mt-0.5 text-xs text-slate-500">
                      {schedule.summary}
                      {schedule.enabled && schedule.next_run_at ? (
                        <>
                          {' · prochaine '}
                          <span className="text-slate-400">
                            {formatRelative(schedule.next_run_at)}
                          </span>
                        </>
                      ) : (
                        ' · suspendue'
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
                      Exécuter
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
                        {schedule.enabled ? 'Suspendre' : 'Reprendre'}
                      </span>
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      icon={<Pencil className="size-3.5" />}
                      onClick={() => setEditing(schedule)}
                    >
                      <span className="sr-only">Modifier</span>
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      icon={<Trash2 className="size-3.5" />}
                      onClick={() => setToDelete(schedule)}
                    >
                      <span className="sr-only">Supprimer</span>
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
        title={`Supprimer « ${toDelete?.name} » ?`}
        consequence="La tâche ne se déclenchera plus. Les exécutions passées restent dans le journal d'audit."
        confirmLabel="Supprimer"
        danger
        loading={remove.isPending}
        onConfirm={() => toDelete && remove.mutate(toDelete)}
        onClose={() => setToDelete(null)}
      />
    </div>
  )
}
