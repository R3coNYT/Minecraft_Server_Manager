/**
 * Sauvegardes d'un serveur.
 *
 * L'interface énonce ce qu'une sauvegarde contient — et ce qu'elle ne contient
 * pas. Découvrir après un incendie que les mods n'étaient pas dans l'archive
 * serait le pire moment ; l'inventaire est donc consultable avant d'en avoir
 * besoin.
 *
 * La restauration est traitée comme ce qu'elle est : une opération destructive
 * sur des données irremplaçables. Serveur arrêté, confirmation par saisie du
 * nom, et sauvegarde de sécurité prise automatiquement par le serveur.
 */

import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Archive,
  Download,
  HardDriveDownload,
  Info,
  Plus,
  Square,
  Trash2,
} from 'lucide-react'
import { ApiError, api } from '@/lib/api'
import { hasPermission, useMe } from '@/hooks/useApi'
import { useToasts } from '@/stores/toasts'
import { useRealtime } from '@/stores/realtime'
import { formatBytes, formatRelative } from '@/lib/format'
import type { Backup } from '@/lib/types'
import { useServerContext } from './context'
import { Badge, Card, CardHeader, EmptyState, LoadingBlock } from '@/components/ui/primitives'
import { Button } from '@/components/ui/Button'
import { Dialog } from '@/components/ui/Dialog'
import { ErrorPanel } from '@/components/common/ErrorPanel'
import { ConfirmDialog } from '@/components/common/ConfirmDialog'
import { cn } from '@/lib/cn'

const STATUS_STYLES: Record<string, string> = {
  RUNNING: 'bg-amber-500/10 text-amber-300 ring-amber-500/30',
  COMPLETED: 'bg-emerald-500/10 text-emerald-300 ring-emerald-500/30',
  FAILED: 'bg-red-500/10 text-red-300 ring-red-500/30',
  PENDING: 'bg-slate-700/40 text-slate-300 ring-slate-600',
}

const KIND_LABELS: Record<string, string> = {
  manual: 'manuelle',
  'pre-restore': 'avant restauration',
}

/** Détail du contenu d'une archive, lu à la demande. */
function ManifestDialog({
  serverId,
  backup,
  onClose,
}: {
  serverId: number
  backup: Backup
  onClose: () => void
}) {
  const manifest = useQuery({
    queryKey: ['backup-manifest', serverId, backup.id],
    queryFn: () => api.backups.manifest(serverId, backup.id),
  })

  const data = manifest.data

  return (
    <Dialog
      open
      onClose={onClose}
      title={`Sauvegarde du ${new Date(backup.created_at).toLocaleString('fr-FR')}`}
      description="Contenu déclaré par l'archive."
      size="lg"
      footer={
        <Button variant="ghost" onClick={onClose}>
          Fermer
        </Button>
      }
    >
      {manifest.isLoading ? <LoadingBlock /> : null}
      <ErrorPanel error={manifest.error} />

      {data ? (
        <div className="space-y-4 text-sm">
          <div className="rounded-lg border border-slate-800 bg-slate-900/60 px-4 py-3">
            <p className="text-slate-300">
              Mondes sauvegardés : {(data.content.worlds ?? []).join(', ') || '—'}
            </p>
            <p className="mt-1 text-xs text-slate-500">
              {data.content.file_count ?? 0} fichiers,{' '}
              {formatBytes(data.content.total_bytes ?? 0)} avant compression
            </p>
          </div>

          <div className="flex items-start gap-2.5 rounded-lg border border-slate-800 bg-slate-900/40 px-4 py-3">
            <Info className="mt-0.5 size-4 shrink-0 text-slate-500" />
            <p className="text-xs text-slate-400">
              Les mods et plugins ne sont pas dans l'archive — ils se retéléchargent. Voici la
              liste de ceux qui étaient installés, pour les remettre en place après une
              reconstruction.
            </p>
          </div>

          {(['mods', 'plugins'] as const).map((kind) => (
            <div key={kind}>
              <p className="mb-1.5 text-xs font-medium uppercase tracking-wide text-slate-500">
                {kind} ({data[kind].length})
              </p>
              {data[kind].length === 0 ? (
                <p className="text-xs text-slate-600">Aucun.</p>
              ) : (
                <ul className="max-h-48 space-y-0.5 overflow-y-auto">
                  {data[kind].map((item) => (
                    <li
                      key={item.name}
                      className="flex items-baseline justify-between gap-3 text-xs"
                    >
                      <span className={cn('truncate', item.enabled ? 'text-slate-300' : 'text-slate-600')}>
                        {item.name}
                        {item.enabled ? '' : ' (désactivé)'}
                      </span>
                      <span className="shrink-0 tabular-nums text-slate-600">
                        {formatBytes(item.size_bytes)}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          ))}
        </div>
      ) : null}
    </Dialog>
  )
}

export function BackupsPage() {
  const { server, status } = useServerContext()
  const queryClient = useQueryClient()
  const { data: me } = useMe()
  const push = useToasts((state) => state.push)
  const pushError = useToasts((state) => state.pushError)

  const canBackup = hasPermission(me, 'backup:create')
  const canRestore = hasPermission(me, 'backup:restore')
  const running = status?.state === 'ONLINE' || status?.state === 'STARTING'

  const [toRestore, setToRestore] = useState<Backup | null>(null)
  const [toDelete, setToDelete] = useState<Backup | null>(null)
  const [toInspect, setToInspect] = useState<Backup | null>(null)

  const backups = useQuery({
    queryKey: ['backups', server.id],
    queryFn: () => api.backups.list(server.id),
  })

  // La progression arrive par WebSocket ; seule la fin justifie de relire la
  // liste, une sauvegarde de plusieurs minutes n'a pas à produire cent requêtes.
  const progress = useRealtime((state) => state.backupProgress[server.id])
  const progressState = progress ? `${progress.backup_id}:${progress.status}` : ''
  useEffect(() => {
    if (progressState) void queryClient.invalidateQueries({ queryKey: ['backups', server.id] })
  }, [progressState, queryClient, server.id])

  const create = useMutation({
    mutationFn: () => api.backups.create(server.id),
    onSuccess: () => {
      push({ kind: 'info', title: 'Sauvegarde lancée', detail: 'Sa progression suit ci-dessous.' })
      void queryClient.invalidateQueries({ queryKey: ['backups', server.id] })
    },
    onError: (error) => pushError(error),
  })

  const cancel = useMutation({
    mutationFn: (backup: Backup) => api.backups.cancel(server.id, backup.id),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['backups', server.id] }),
    onError: (error) => pushError(error),
  })

  const restore = useMutation({
    mutationFn: (backup: Backup) => api.backups.restore(server.id, backup.id, true),
    onSuccess: () => {
      push({
        kind: 'success',
        title: 'Restauration terminée',
        detail: 'Une sauvegarde de sécurité a été prise avant remplacement.',
      })
      setToRestore(null)
      void queryClient.invalidateQueries({ queryKey: ['backups', server.id] })
    },
    onError: (error) => {
      if (!(error instanceof ApiError && error.needsConfirmation)) pushError(error)
    },
  })

  const remove = useMutation({
    mutationFn: (backup: Backup) => api.backups.remove(server.id, backup.id),
    onSuccess: () => {
      push({ kind: 'success', title: 'Sauvegarde supprimée' })
      setToDelete(null)
      void queryClient.invalidateQueries({ queryKey: ['backups', server.id] })
    },
    onError: (error) => pushError(error),
  })

  if (backups.isLoading) return <LoadingBlock />

  const items = backups.data ?? []

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-4xl space-y-5 p-4 sm:p-6">
        <ErrorPanel error={backups.error} />

        <Card>
          <CardHeader
            title="Sauvegardes"
            subtitle="Mondes et configurations. Les mods sont inventoriés, pas archivés."
            action={
              canBackup ? (
                <Button
                  size="sm"
                  variant="primary"
                  icon={<Plus className="size-3.5" />}
                  loading={create.isPending}
                  onClick={() => create.mutate()}
                >
                  Sauvegarder maintenant
                </Button>
              ) : undefined
            }
          />

          {running ? (
            <p className="border-b border-slate-800/60 px-5 py-2.5 text-xs text-slate-500">
              Le serveur tourne : ses écritures seront suspendues le temps de la copie, puis
              rétablies. Les joueurs ne sont pas déconnectés.
            </p>
          ) : null}

          {items.length === 0 ? (
            <EmptyState
              icon={<Archive className="size-8" />}
              title="Aucune sauvegarde"
              description="Une sauvegarde qu'on ne fait pas ne protège de rien."
            />
          ) : (
            <ul className="divide-y divide-slate-800/60">
              {items.map((backup) => {
                const live = progress?.backup_id === backup.id ? progress : null
                const state = live?.status ?? backup.status
                return (
                  <li key={backup.id} className="flex items-center gap-3 px-5 py-3">
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="text-sm text-slate-200">
                          {new Date(backup.created_at).toLocaleString('fr-FR')}
                        </span>
                        <Badge className={cn(STATUS_STYLES[state])}>{state}</Badge>
                        <Badge>{KIND_LABELS[backup.kind] ?? backup.kind}</Badge>
                        {backup.size_bytes ? (
                          <span className="text-xs tabular-nums text-slate-500">
                            {formatBytes(backup.size_bytes)}
                          </span>
                        ) : null}
                      </div>

                      {state === 'RUNNING' ? (
                        <div className="mt-1.5">
                          <div className="h-1 overflow-hidden rounded-full bg-slate-800">
                            <div
                              className="h-full rounded-full bg-sky-500 transition-[width] duration-300"
                              style={{ width: `${live?.percent ?? 0}%` }}
                            />
                          </div>
                          <p className="mt-1 text-[11px] text-slate-500">
                            {live?.phase ?? 'En cours'} — {live?.percent ?? 0} %
                          </p>
                        </div>
                      ) : (
                        <p className="mt-0.5 text-xs text-slate-500">
                          {formatRelative(backup.created_at)}
                          {backup.error ? (
                            <span className="ml-2 text-red-400">{backup.error}</span>
                          ) : null}
                        </p>
                      )}
                    </div>

                    <div className="flex shrink-0 items-center gap-1">
                      {state === 'RUNNING' ? (
                        <Button
                          size="sm"
                          variant="ghost"
                          icon={<Square className="size-3.5" />}
                          loading={cancel.isPending}
                          onClick={() => cancel.mutate(backup)}
                        >
                          Annuler
                        </Button>
                      ) : null}

                      {backup.available ? (
                        <>
                          <Button
                            size="sm"
                            variant="ghost"
                            icon={<Info className="size-3.5" />}
                            onClick={() => setToInspect(backup)}
                          >
                            <span className="sr-only">Contenu</span>
                          </Button>
                          {canBackup ? (
                            <a
                              href={api.backups.downloadUrl(server.id, backup.id)}
                              download
                              className="rounded-lg p-1.5 text-slate-400 hover:bg-slate-800 hover:text-slate-100"
                              title="Télécharger l'archive"
                            >
                              <Download className="size-3.5" />
                            </a>
                          ) : null}
                          {canRestore ? (
                            <Button
                              size="sm"
                              variant="secondary"
                              icon={<HardDriveDownload className="size-3.5" />}
                              disabled={running}
                              title={
                                running ? 'Arrêter le serveur pour pouvoir restaurer' : undefined
                              }
                              onClick={() => setToRestore(backup)}
                            >
                              Restaurer
                            </Button>
                          ) : null}
                        </>
                      ) : null}

                      {canBackup && state !== 'RUNNING' ? (
                        <Button
                          size="sm"
                          variant="ghost"
                          icon={<Trash2 className="size-3.5" />}
                          onClick={() => setToDelete(backup)}
                        >
                          <span className="sr-only">Supprimer</span>
                        </Button>
                      ) : null}
                    </div>
                  </li>
                )
              })}
            </ul>
          )}
        </Card>
      </div>

      {toInspect ? (
        <ManifestDialog
          serverId={server.id}
          backup={toInspect}
          onClose={() => setToInspect(null)}
        />
      ) : null}

      <ConfirmDialog
        open={toRestore !== null}
        title="Restaurer cette sauvegarde ?"
        consequence={
          toRestore
            ? `Les mondes et configurations actuels seront remplacés par ceux du ${new Date(
                toRestore.created_at,
              ).toLocaleString('fr-FR')}. Une sauvegarde de sécurité de l'état actuel est prise automatiquement avant.`
            : undefined
        }
        confirmLabel="Restaurer"
        danger
        requireTyping={server.name}
        loading={restore.isPending}
        error={restore.error}
        onConfirm={() => toRestore && restore.mutate(toRestore)}
        onClose={() => setToRestore(null)}
      />

      <ConfirmDialog
        open={toDelete !== null}
        title="Supprimer cette sauvegarde ?"
        consequence="L'archive sera définitivement effacée du disque."
        confirmLabel="Supprimer"
        danger
        loading={remove.isPending}
        onConfirm={() => toDelete && remove.mutate(toDelete)}
        onClose={() => setToDelete(null)}
      />
    </div>
  )
}
