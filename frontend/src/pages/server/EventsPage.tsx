/**
 * Événements d'un serveur.
 *
 * Deux usages, volontairement distincts : l'**action immédiate** — un message,
 * un titre, un don — déclenchée en un clic sans rien enregistrer, et
 * l'**événement enregistré**, séquence réutilisable exécutée en tâche de fond.
 *
 * La progression d'une séquence arrive par WebSocket : un événement de trente
 * minutes se suit sans que la page n'interroge le serveur.
 */

import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { CalendarClock, Pencil, Play, Plus, Send, Square, Trash2 } from 'lucide-react'
import { ApiError, api } from '@/lib/api'
import { hasPermission, useMe } from '@/hooks/useApi'
import { useToasts } from '@/stores/toasts'
import { useRealtime } from '@/stores/realtime'
import { formatRelative } from '@/lib/format'
import type { ActionType, GameEvent } from '@/lib/types'
import { useServerContext } from './context'
import { Badge, Card, CardHeader, EmptyState, LoadingBlock } from '@/components/ui/primitives'
import { Button } from '@/components/ui/Button'
import { ErrorPanel } from '@/components/common/ErrorPanel'
import { ConfirmDialog } from '@/components/common/ConfirmDialog'
import { ActionForm, ActionSelect, defaultParams } from '@/components/events/ActionForm'
import { EventEditor, type DraftStep } from '@/components/events/EventEditor'
import { cn } from '@/lib/cn'

const RUN_STYLES: Record<string, string> = {
  RUNNING: 'bg-amber-500/10 text-amber-300 ring-amber-500/30',
  COMPLETED: 'bg-emerald-500/10 text-emerald-300 ring-emerald-500/30',
  FAILED: 'bg-red-500/10 text-red-300 ring-red-500/30',
  CANCELLED: 'bg-slate-700/40 text-slate-300 ring-slate-600',
}

export function EventsPage() {
  const { server, status } = useServerContext()
  const queryClient = useQueryClient()
  const { data: me } = useMe()
  const push = useToasts((state) => state.push)
  const pushError = useToasts((state) => state.pushError)

  const running = status?.state === 'ONLINE' || status?.state === 'STARTING'
  const canRun = hasPermission(me, 'event:run')
  const canEdit = hasPermission(me, 'event:edit')

  const [quickKey, setQuickKey] = useState('say')
  const [quickParams, setQuickParams] = useState<Record<string, unknown>>({})
  const [pendingQuick, setPendingQuick] = useState<string | null>(null)
  // `null` : éditeur fermé ; un événement : modification ; `'new'` : création.
  const [editing, setEditing] = useState<GameEvent | 'new' | null>(null)
  const [toDelete, setToDelete] = useState<GameEvent | null>(null)
  const [toConfirmRun, setToConfirmRun] = useState<{ event: GameEvent; cause: string } | null>(
    null,
  )

  const catalogue = useQuery({
    queryKey: ['event-actions'],
    queryFn: () => api.events.actions(),
    staleTime: Infinity,
  })

  const events = useQuery({
    queryKey: ['events', server.id],
    queryFn: () => api.events.list(server.id),
  })

  const runs = useQuery({
    queryKey: ['event-runs', server.id],
    queryFn: () => api.events.runs(server.id),
  })

  // La progression poussée par le WebSocket rafraîchit l'historique sans
  // interroger le serveur en boucle. Seul un changement d'état déclenche la
  // relecture : une séquence de cent étapes ne doit pas produire cent requêtes.
  const progress = useRealtime((state) => state.eventProgress[server.id])
  const progressState = progress ? `${progress.run_id}:${progress.status}` : ''
  useEffect(() => {
    if (progressState) void queryClient.invalidateQueries({ queryKey: ['event-runs', server.id] })
  }, [progressState, queryClient, server.id])

  const actions: ActionType[] = catalogue.data ?? []
  const quickAction = actions.find((action) => action.key === quickKey)

  const quick = useMutation({
    mutationFn: ({ confirm }: { confirm: boolean }) =>
      api.events.quick(
        server.id,
        quickKey,
        { ...defaultParams(quickAction!), ...quickParams },
        confirm,
      ),
    onSuccess: (result) => {
      push({ kind: 'success', title: result.summary, detail: result.commands.join(' · ') })
      setPendingQuick(null)
    },
    onError: (error) => {
      if (error instanceof ApiError && error.needsConfirmation) {
        setPendingQuick(error.cause ?? 'Cette action est irréversible.')
        return
      }
      pushError(error)
      setPendingQuick(null)
    },
  })

  const save = useMutation({
    mutationFn: (payload: { name: string; description: string; steps: DraftStep[] }) =>
      editing && editing !== 'new'
        ? api.events.update(server.id, editing.id, payload)
        : api.events.create(server.id, payload),
    onSuccess: (event) => {
      push({ kind: 'success', title: `Événement « ${event.name} » enregistré` })
      setEditing(null)
      void queryClient.invalidateQueries({ queryKey: ['events', server.id] })
    },
  })

  const run = useMutation({
    mutationFn: ({ event, confirm }: { event: GameEvent; confirm: boolean }) =>
      api.events.run(server.id, event.id, confirm),
    onSuccess: () => {
      push({ kind: 'success', title: 'Événement lancé' })
      setToConfirmRun(null)
      void queryClient.invalidateQueries({ queryKey: ['event-runs', server.id] })
    },
    onError: (error, variables) => {
      if (error instanceof ApiError && error.needsConfirmation) {
        setToConfirmRun({
          event: variables.event,
          cause: error.cause ?? 'Cet événement contient des actions irréversibles.',
        })
        return
      }
      pushError(error)
      setToConfirmRun(null)
    },
  })

  const remove = useMutation({
    mutationFn: (event: GameEvent) => api.events.remove(server.id, event.id),
    onSuccess: () => {
      push({ kind: 'success', title: 'Événement supprimé' })
      setToDelete(null)
      void queryClient.invalidateQueries({ queryKey: ['events', server.id] })
    },
    onError: (error) => pushError(error),
  })

  const cancel = useMutation({
    mutationFn: (runId: number) => api.events.cancel(server.id, runId),
    onSuccess: (result) => {
      push({
        kind: result.cancelled ? 'success' : 'info',
        title: result.cancelled ? 'Exécution annulée' : 'Cette exécution était déjà terminée',
      })
      void queryClient.invalidateQueries({ queryKey: ['event-runs', server.id] })
    },
  })

  if (catalogue.isLoading || events.isLoading) return <LoadingBlock />

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-4xl space-y-5 p-4 sm:p-6">
        <ErrorPanel error={catalogue.error ?? events.error} />

        {!running ? (
          <div className="rounded-lg border border-amber-900/60 bg-amber-950/30 px-4 py-3 text-sm text-amber-100">
            Le serveur est arrêté. Les événements passent par sa console : les déclencher exige
            qu'il soit démarré.
          </div>
        ) : null}

        {/* --- Action immédiate --- */}
        <Card>
          <CardHeader
            title="Action immédiate"
            subtitle="Déclenchée en un clic, sans être enregistrée."
          />
          <div className="space-y-4 px-5 py-4">
            <ActionSelect actions={actions} value={quickKey} onChange={(key) => {
              setQuickKey(key)
              setQuickParams({})
            }} />

            {quickAction ? (
              <>
                <p className="text-xs text-slate-500">{quickAction.description}</p>
                <ActionForm
                  action={quickAction}
                  values={{ ...defaultParams(quickAction), ...quickParams }}
                  onChange={setQuickParams}
                  disabled={!canRun || !running}
                />
              </>
            ) : null}

            <Button
              variant={quickAction?.danger === 'SAFE' ? 'primary' : 'danger'}
              icon={<Send className="size-4" />}
              disabled={!canRun || !running}
              loading={quick.isPending && pendingQuick === null}
              onClick={() => quick.mutate({ confirm: false })}
            >
              Déclencher
            </Button>
          </div>
        </Card>

        {/* --- Événements enregistrés --- */}
        <Card>
          <CardHeader
            title={`Événements enregistrés (${events.data?.length ?? 0})`}
            subtitle="Suites d'actions réutilisables, avec pauses possibles."
            action={
              canEdit ? (
                <Button
                  size="sm"
                  variant="secondary"
                  icon={<Plus className="size-3.5" />}
                  onClick={() => setEditing('new')}
                >
                  Nouvel événement
                </Button>
              ) : undefined
            }
          />

          {!events.data || events.data.length === 0 ? (
            <EmptyState
              icon={<CalendarClock className="size-8" />}
              title="Aucun événement enregistré"
              description="Créer une séquence — annoncer, attendre, distribuer — pour la rejouer d'un clic."
            />
          ) : (
            <ul className="divide-y divide-slate-800/60">
              {events.data.map((event) => (
                <li key={event.id} className="flex items-start gap-3 px-5 py-3">
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="text-sm text-slate-100">{event.name}</span>
                      <Badge>{event.steps.length} étape{event.steps.length > 1 ? 's' : ''}</Badge>
                      {event.danger !== 'SAFE' ? (
                        <Badge className="bg-red-500/10 text-red-300 ring-red-500/30">
                          irréversible
                        </Badge>
                      ) : null}
                    </div>
                    {event.description ? (
                      <p className="mt-0.5 text-xs text-slate-500">{event.description}</p>
                    ) : null}
                    <ol className="mt-1.5 space-y-0.5">
                      {event.steps.map((step, index) => (
                        <li key={index} className="text-[11px] text-slate-600">
                          {index + 1}. {step.summary}
                        </li>
                      ))}
                    </ol>
                  </div>

                  <div className="flex shrink-0 items-center gap-1">
                    <Button
                      size="sm"
                      variant={event.danger === 'SAFE' ? 'primary' : 'danger'}
                      icon={<Play className="size-3.5" />}
                      disabled={!canRun || !running}
                      loading={run.isPending && run.variables?.event.id === event.id}
                      onClick={() => run.mutate({ event, confirm: false })}
                    >
                      Lancer
                    </Button>
                    {canEdit ? (
                      <>
                        <Button
                          size="sm"
                          variant="ghost"
                          icon={<Pencil className="size-3.5" />}
                          onClick={() => setEditing(event)}
                        >
                          <span className="sr-only">Modifier {event.name}</span>
                        </Button>
                        <Button
                          size="sm"
                          variant="ghost"
                          icon={<Trash2 className="size-3.5" />}
                          onClick={() => setToDelete(event)}
                        >
                          <span className="sr-only">Supprimer {event.name}</span>
                        </Button>
                      </>
                    ) : null}
                  </div>
                </li>
              ))}
            </ul>
          )}
        </Card>

        {/* --- Historique --- */}
        {runs.data && runs.data.length > 0 ? (
          <Card>
            <CardHeader title="Exécutions récentes" />
            <table className="w-full text-sm">
              <tbody className="divide-y divide-slate-800/60">
                {runs.data.map((item) => {
                  const live = progress?.run_id === item.id ? progress : null
                  const step = live?.current_step ?? item.current_step
                  const total = live?.total_steps ?? item.total_steps
                  const state = live?.status ?? item.status
                  return (
                    <tr key={item.id}>
                      <td className="px-5 py-2.5 text-xs text-slate-500">
                        {formatRelative(item.started_at)}
                      </td>
                      <td className="px-5 py-2.5">
                        <Badge className={cn(RUN_STYLES[state])}>{state}</Badge>
                      </td>
                      <td className="px-5 py-2.5 text-xs tabular-nums text-slate-400">
                        étape {step} / {total}
                        {live?.summary ? (
                          <span className="ml-2 text-slate-500">{live.summary}</span>
                        ) : null}
                        {item.error ? (
                          <span className="ml-2 text-red-400">{item.error}</span>
                        ) : null}
                      </td>
                      <td className="px-5 py-2.5 text-right">
                        {state === 'RUNNING' ? (
                          <Button
                            size="sm"
                            variant="ghost"
                            icon={<Square className="size-3.5" />}
                            loading={cancel.isPending}
                            onClick={() => cancel.mutate(item.id)}
                          >
                            Annuler
                          </Button>
                        ) : null}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </Card>
        ) : null}
      </div>

      {editing ? (
        // Remonté par la clé : l'éditeur garde son brouillon dans son propre
        // état, il doit repartir de zéro quand on change d'événement.
        <EventEditor
          key={editing === 'new' ? 'new' : editing.id}
          open
          actions={actions}
          initialName={editing === 'new' ? '' : editing.name}
          initialDescription={editing === 'new' ? '' : (editing.description ?? '')}
          initialSteps={
            editing === 'new'
              ? []
              : editing.steps.map((step) => ({ action: step.action, params: step.params }))
          }
          saving={save.isPending}
          error={save.error}
          onSave={(payload) => save.mutate(payload)}
          onClose={() => setEditing(null)}
        />
      ) : null}

      <ConfirmDialog
        open={pendingQuick !== null}
        title="Action irréversible"
        consequence={pendingQuick ?? undefined}
        confirmLabel="Exécuter"
        danger
        requireTyping={server.name}
        loading={quick.isPending}
        onConfirm={() => quick.mutate({ confirm: true })}
        onClose={() => setPendingQuick(null)}
      />

      <ConfirmDialog
        open={toConfirmRun !== null}
        title={`Lancer « ${toConfirmRun?.event.name} » ?`}
        consequence={toConfirmRun?.cause}
        confirmLabel="Lancer"
        danger
        requireTyping={server.name}
        loading={run.isPending}
        onConfirm={() =>
          toConfirmRun && run.mutate({ event: toConfirmRun.event, confirm: true })
        }
        onClose={() => setToConfirmRun(null)}
      />

      <ConfirmDialog
        open={toDelete !== null}
        title={`Supprimer « ${toDelete?.name} » ?`}
        consequence="L'événement sera définitivement retiré. L'historique de ses exécutions est conservé."
        confirmLabel="Supprimer"
        danger
        loading={remove.isPending}
        onConfirm={() => toDelete && remove.mutate(toDelete)}
        onClose={() => setToDelete(null)}
      />
    </div>
  )
}
