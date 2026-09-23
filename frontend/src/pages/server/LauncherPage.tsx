/**
 * Liaison d'un serveur avec le serveur de fichiers de son launcher.
 *
 * Deux sens, deux cartes :
 *
 * * **Synchronisation** — le serveur de fichiers fait autorité sur le contenu :
 *   ses mods arrivent ici, au prochain démarrage si le serveur tourne ;
 * * **Publication** — MSM fait autorité sur l'activation : un mod désactivé ici
 *   l'est aussi pour les joueurs, à leur prochaine synchronisation.
 */

import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import {
  AlertTriangle,
  CloudDownload,
  Link2,
  RefreshCw,
  Send,
  Trash2,
  Wrench,
} from 'lucide-react'
import { api } from '@/lib/api'
import { hasPermission, queryKeys, useLauncherLink, useMe } from '@/hooks/useApi'
import { useToasts } from '@/stores/toasts'
import { formatBytes, formatRelative } from '@/lib/format'
import type { LauncherLink, LauncherLinkMod, LauncherSyncStatus, ModSide } from '@/lib/types'
import { useServerContext } from './context'
import {
  Badge,
  Card,
  CardHeader,
  Checkbox,
  EmptyState,
  Field,
  Input,
  LoadingBlock,
  Select,
} from '@/components/ui/primitives'
import { Button } from '@/components/ui/Button'
import { ErrorPanel } from '@/components/common/ErrorPanel'
import { ConfirmDialog } from '@/components/common/ConfirmDialog'
import { SyncCountdown } from '@/components/launcher/SyncCountdown'

const STATUS: Record<LauncherSyncStatus, { label: string; style: string }> = {
  NEVER: { label: 'jamais synchronisé', style: 'bg-slate-700/40 text-slate-400 ring-slate-700' },
  UP_TO_DATE: { label: 'à jour', style: 'bg-emerald-500/10 text-emerald-300 ring-emerald-500/30' },
  APPLIED: { label: 'appliquée', style: 'bg-emerald-500/10 text-emerald-300 ring-emerald-500/30' },
  PENDING_RESTART: {
    label: 'en attente du redémarrage',
    style: 'bg-sky-500/10 text-sky-300 ring-sky-500/30',
  },
  BLOCKED: { label: 'bloquée', style: 'bg-amber-500/10 text-amber-300 ring-amber-500/30' },
  FAILED: { label: 'échec', style: 'bg-red-500/10 text-red-300 ring-red-500/30' },
}

const SIDE_LABELS: Record<ModSide, string> = {
  client: 'Client uniquement',
  server: 'Serveur uniquement',
  both: 'Client et serveur',
}

const SIDE_SOURCES: Record<LauncherLinkMod['side_source'], string> = {
  override: 'forcé dans MSM',
  manifest: 'déclaré par le manifest',
  detected: 'détecté dans le JAR',
  default: 'par défaut',
}

interface Draft {
  file_server_url: string
  push_token: string
  sync_paths: string
  interval_minutes: number
  enabled: boolean
}

function toDraft(link: LauncherLink | null): Draft {
  return {
    file_server_url: link?.file_server_url ?? '',
    push_token: '',
    sync_paths: (link?.sync_paths ?? ['mods/']).join(', '),
    interval_minutes: link?.interval_minutes ?? 30,
    enabled: link?.enabled ?? true,
  }
}

export function LauncherPage() {
  const { server } = useServerContext()
  const { data: me } = useMe()
  const canEdit = hasPermission(me, 'server:edit')
  const queryClient = useQueryClient()
  const push = useToasts((state) => state.push)
  const pushError = useToasts((state) => state.pushError)

  const { data: link, isLoading, error } = useLauncherLink(server.id)
  const [editing, setEditing] = useState(false)
  const [confirmMassDelete, setConfirmMassDelete] = useState(false)
  const [confirmRemove, setConfirmRemove] = useState(false)

  const store = (next: LauncherLink) =>
    queryClient.setQueryData(queryKeys.launcherLink(server.id), next)

  const sync = useMutation({
    mutationFn: (allowMassDelete: boolean) => api.launcherLink.sync(server.id, allowMassDelete),
    onSuccess: (next) => {
      store(next)
      setConfirmMassDelete(false)
      const status = STATUS[next.last_sync_status]
      push({
        kind: next.last_sync_status === 'FAILED' ? 'error' : 'success',
        title: `Synchronisation ${status.label}`,
        detail: next.last_sync_error ?? undefined,
      })
      // Les fichiers ont pu changer sur le disque.
      void queryClient.invalidateQueries({ queryKey: ['files', server.id] })
    },
    onError: (syncError) => pushError(syncError),
  })

  const publish = useMutation({
    mutationFn: () => api.launcherLink.publish(server.id),
    onSuccess: (next) => {
      store(next)
      push(
        next.publish.up_to_date
          ? { kind: 'success', title: 'État publié aux joueurs' }
          : {
              kind: 'error',
              title: 'Publication impossible',
              detail: next.publish.last_push_error ?? 'Aucun jeton d’écriture configuré.',
            },
      )
    },
    onError: (publishError) => pushError(publishError),
  })

  const setSide = useMutation({
    mutationFn: ({ path, side }: { path: string; side: ModSide | null }) =>
      api.launcherLink.setSide(server.id, path, side),
    onSuccess: (next) => {
      store(next)
      push({
        kind: 'success',
        title: 'Côté enregistré',
        detail: 'Pris en compte à la prochaine synchronisation, lancée dans l’instant.',
      })
    },
    onError: (sideError) => pushError(sideError),
  })

  const remove = useMutation({
    mutationFn: () => api.launcherLink.remove(server.id),
    onSuccess: () => {
      queryClient.setQueryData(queryKeys.launcherLink(server.id), null)
      setConfirmRemove(false)
      push({ kind: 'success', title: 'Liaison retirée', detail: 'Les mods installés restent en place.' })
    },
    onError: (removeError) => pushError(removeError),
  })

  if (isLoading) return <LoadingBlock />

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-4xl space-y-5 p-4 sm:p-6">
        <ErrorPanel error={error} />

        {!link && !editing ? (
          <Card>
            <EmptyState
              icon={<Link2 className="size-8" />}
              title="Aucun serveur de fichiers relié"
              description={
                <>
                  Reliez ce serveur au serveur de fichiers de votre launcher : ses mods seront
                  installés ici automatiquement, et les mods désactivés dans MSM le seront aussi
                  pour les joueurs. Le protocole est décrit dans{' '}
                  <code className="text-slate-300">docs/LAUNCHER_INTEGRATION.md</code>.
                </>
              }
              action={
                canEdit ? (
                  <Button variant="primary" size="sm" onClick={() => setEditing(true)}>
                    Relier un serveur de fichiers
                  </Button>
                ) : null
              }
            />
          </Card>
        ) : null}

        {editing ? (
          <ConfigurationForm
            serverId={server.id}
            link={link ?? null}
            onDone={(next) => {
              if (next) store(next)
              setEditing(false)
            }}
          />
        ) : null}

        {link && !editing ? (
          <>
            <SyncCard
              serverId={server.id}
              link={link}
              canEdit={canEdit}
              syncing={sync.isPending}
              onSync={() => sync.mutate(false)}
              onConfirmMassDelete={() => setConfirmMassDelete(true)}
              onEdit={() => setEditing(true)}
              onRemove={() => setConfirmRemove(true)}
            />
            <PublishCard
              link={link}
              canEdit={canEdit}
              publishing={publish.isPending}
              onPublish={() => publish.mutate()}
            />
            <ModsCard
              link={link}
              canEdit={canEdit}
              busyPath={setSide.isPending ? setSide.variables?.path : undefined}
              onSide={(path, side) => setSide.mutate({ path, side })}
            />
          </>
        ) : null}
      </div>

      <ConfirmDialog
        open={confirmMassDelete}
        title="Confirmer la suppression massive"
        description={link?.last_sync_error ?? undefined}
        consequence={`Les fichiers retirés du manifest seront supprimés de « ${server.name} »${
          link?.pending?.server_running ? ' au prochain démarrage' : ''
        }. Les mods ajoutés à la main ne sont jamais touchés.`}
        confirmLabel="Synchroniser quand même"
        danger
        requireTyping={server.name}
        loading={sync.isPending}
        error={sync.error}
        onConfirm={() => sync.mutate(true)}
        onClose={() => setConfirmMassDelete(false)}
      />

      <ConfirmDialog
        open={confirmRemove}
        title="Retirer la liaison ?"
        consequence="MSM cessera de synchroniser ce serveur et de publier l'état de ses mods. Les mods déjà installés restent en place."
        confirmLabel="Retirer"
        danger
        loading={remove.isPending}
        error={remove.error}
        onConfirm={() => remove.mutate()}
        onClose={() => setConfirmRemove(false)}
      />
    </div>
  )
}

// --------------------------------------------------------------------------- //
//  Réglages
// --------------------------------------------------------------------------- //
function ConfigurationForm({
  serverId,
  link,
  onDone,
}: {
  serverId: number
  link: LauncherLink | null
  onDone: (next: LauncherLink | null) => void
}) {
  const push = useToasts((state) => state.push)
  const [draft, setDraft] = useState<Draft>(() => toDraft(link))
  const [clearToken, setClearToken] = useState(false)

  const save = useMutation({
    mutationFn: () =>
      api.launcherLink.configure(serverId, {
        file_server_url: draft.file_server_url.trim(),
        sync_paths: draft.sync_paths
          .split(',')
          .map((value) => value.trim())
          .filter(Boolean),
        interval_minutes: draft.interval_minutes,
        enabled: draft.enabled,
        push_token: draft.push_token.trim() || null,
        clear_push_token: clearToken,
      }),
    onSuccess: (next) => {
      push({
        kind: 'success',
        title: 'Liaison enregistrée',
        detail: next.enabled ? 'Première synchronisation dans quelques secondes.' : undefined,
      })
      onDone(next)
    },
  })

  const set = <K extends keyof Draft>(key: K, value: Draft[K]) =>
    setDraft((current) => ({ ...current, [key]: value }))

  return (
    <Card>
      <CardHeader
        title={link ? 'Modifier la liaison' : 'Relier un serveur de fichiers'}
        subtitle="Tout part de MSM : il n'a pas besoin d'être joignable depuis Internet."
      />
      <form
        className="space-y-4 px-5 py-4"
        onSubmit={(event) => {
          event.preventDefault()
          save.mutate()
        }}
      >
        <ErrorPanel error={save.error} />

        <Field
          label="Adresse du serveur de fichiers"
          hint="Celle que le launcher interroge : le manifest est lu à /manifest.json, les fichiers à /files/…"
        >
          <Input
            required
            value={draft.file_server_url}
            onChange={(event) => set('file_server_url', event.target.value)}
            placeholder="https://frankumc.frankulin.fr"
          />
        </Field>

        <Field
          label="Jeton d'écriture"
          hint={
            link?.push_configured && !clearToken
              ? `Enregistré (${link.push_token_hint ?? '…'}). Saisir un nouveau jeton pour le remplacer.`
              : 'Le même que celui configuré sur le serveur de fichiers. Sans lui, MSM synchronise mais ne publie rien aux joueurs.'
          }
        >
          <Input
            type="password"
            autoComplete="off"
            value={draft.push_token}
            onChange={(event) => set('push_token', event.target.value)}
            placeholder={link?.push_configured ? '••••••••' : ''}
            disabled={clearToken}
          />
        </Field>
        {link?.push_configured ? (
          <Checkbox
            label="Retirer le jeton"
            hint="La publication de l'état aux joueurs s'arrêtera."
            checked={clearToken}
            onChange={(event) => setClearToken(event.target.checked)}
          />
        ) : null}

        <div className="grid gap-4 sm:grid-cols-2">
          <Field
            label="Dossiers synchronisés"
            hint="Séparés par des virgules. Les autres fichiers du modpack sont ignorés."
          >
            <Input
              value={draft.sync_paths}
              onChange={(event) => set('sync_paths', event.target.value)}
              placeholder="mods/"
            />
          </Field>
          <Field label="Intervalle (minutes)" hint="Entre 5 minutes et une semaine.">
            <Input
              type="number"
              min={5}
              max={10080}
              value={draft.interval_minutes}
              onChange={(event) => set('interval_minutes', Number(event.target.value))}
            />
          </Field>
        </div>

        <Checkbox
          label="Synchronisation active"
          hint="Désactivée, plus rien n'est installé ni publié, y compris les changements en attente."
          checked={draft.enabled}
          onChange={(event) => set('enabled', event.target.checked)}
        />

        <div className="flex justify-end gap-2">
          <Button type="button" variant="ghost" size="sm" onClick={() => onDone(null)}>
            Annuler
          </Button>
          <Button type="submit" variant="primary" size="sm" loading={save.isPending}>
            Enregistrer
          </Button>
        </div>
      </form>
    </Card>
  )
}

// --------------------------------------------------------------------------- //
//  Synchronisation
// --------------------------------------------------------------------------- //
function SyncCard({
  serverId,
  link,
  canEdit,
  syncing,
  onSync,
  onConfirmMassDelete,
  onEdit,
  onRemove,
}: {
  serverId: number
  link: LauncherLink
  canEdit: boolean
  syncing: boolean
  onSync: () => void
  onConfirmMassDelete: () => void
  onEdit: () => void
  onRemove: () => void
}) {
  const status = STATUS[link.last_sync_status]
  const summary = link.last_sync_summary

  return (
    <Card>
      <CardHeader
        title="Synchronisation des mods"
        subtitle={
          <span className="font-mono">
            {link.file_server_url} · {link.sync_paths.join(', ')}
          </span>
        }
        action={
          canEdit ? (
            <div className="flex shrink-0 gap-1.5">
              <Button size="sm" variant="ghost" onClick={onEdit}>
                Modifier
              </Button>
              <Button
                size="sm"
                variant="ghost"
                icon={<Trash2 className="size-3.5" />}
                onClick={onRemove}
                aria-label="Retirer la liaison"
              />
              <Button
                size="sm"
                variant="primary"
                icon={<RefreshCw className="size-3.5" />}
                loading={syncing}
                disabled={!link.enabled}
                onClick={onSync}
              >
                Synchroniser
              </Button>
            </div>
          ) : null
        }
      />

      <dl className="grid gap-x-6 gap-y-3 px-5 py-4 text-sm sm:grid-cols-4">
        <div>
          <dt className="text-xs text-slate-500">Prochaine synchronisation</dt>
          <dd className="mt-0.5 text-slate-200">
            <SyncCountdown serverId={serverId} link={link} />
          </dd>
        </div>
        <div>
          <dt className="text-xs text-slate-500">Dernière</dt>
          <dd className="mt-0.5 text-slate-200">{formatRelative(link.last_sync_at)}</dd>
        </div>
        <div>
          <dt className="text-xs text-slate-500">Résultat</dt>
          <dd className="mt-0.5">
            <Badge className={status.style}>{status.label}</Badge>
          </dd>
        </div>
        <div>
          <dt className="text-xs text-slate-500">Version du modpack</dt>
          <dd className="mt-0.5 text-slate-200">{link.pack_version ?? '—'}</dd>
        </div>
      </dl>

      {link.pending ? (
        <div className="mx-5 mb-4 flex items-start gap-2.5 rounded-lg border border-sky-900/60 bg-sky-950/30 px-4 py-3 text-sm text-sky-100">
          <CloudDownload className="mt-0.5 size-4 shrink-0 text-sky-300" />
          <span>
            {pendingText(link.pending)}{' '}
            {link.pending.server_running
              ? 'Ils seront appliqués au prochain démarrage du serveur, avant le lancement de Java.'
              : 'Ils seront appliqués dans quelques secondes.'}
          </span>
        </div>
      ) : null}

      {link.last_sync_error ? (
        <div className="mx-5 mb-4 rounded-lg border border-red-900/60 bg-red-950/40 px-4 py-3">
          <div className="flex items-start gap-2.5">
            <AlertTriangle className="mt-0.5 size-4 shrink-0 text-red-400" />
            <div className="min-w-0 flex-1">
              <p className="text-sm text-red-200">{link.last_sync_error}</p>
              {link.last_sync_status === 'BLOCKED' && canEdit ? (
                <Button
                  className="mt-2.5"
                  size="sm"
                  variant="danger"
                  icon={<Wrench className="size-3.5" />}
                  onClick={onConfirmMassDelete}
                >
                  Confirmer et synchroniser
                </Button>
              ) : null}
            </div>
          </div>
        </div>
      ) : null}

      {summary.installs !== undefined && !link.last_sync_error ? (
        <p className="border-t border-slate-800 px-5 py-3 text-xs text-slate-400">
          Dernier passage : {summary.installs} à installer, {summary.removes ?? 0} à retirer,{' '}
          {summary.unchanged ?? 0} inchangé{(summary.unchanged ?? 0) > 1 ? 's' : ''}
          {summary.adopted ? `, ${summary.adopted} repris en charge` : ''}
          {summary.client_only ? `, ${summary.client_only} réservé${summary.client_only > 1 ? 's' : ''} aux joueurs` : ''}
          .
          {summary.notes?.map((note) => (
            <span key={note} className="block text-amber-300/90">
              {note}
            </span>
          ))}
        </p>
      ) : null}
    </Card>
  )
}

function pendingText(pending: NonNullable<LauncherLink['pending']>): string {
  const parts: string[] = []
  if (pending.installs) {
    parts.push(
      `${pending.installs} fichier${pending.installs > 1 ? 's' : ''} à installer (${formatBytes(pending.download_bytes)}, déjà téléchargé${pending.installs > 1 ? 's' : ''})`,
    )
  }
  if (pending.removes) {
    parts.push(`${pending.removes} à retirer`)
  }
  return `Changements en attente : ${parts.join(', ')}.`
}

// --------------------------------------------------------------------------- //
//  Publication
// --------------------------------------------------------------------------- //
function PublishCard({
  link,
  canEdit,
  publishing,
  onPublish,
}: {
  link: LauncherLink
  canEdit: boolean
  publishing: boolean
  onPublish: () => void
}) {
  const { publish } = link

  return (
    <Card>
      <CardHeader
        title="Publication aux joueurs"
        subtitle="Les mods désactivés dans MSM sont retirés du modpack que les joueurs reçoivent."
        action={
          canEdit && link.push_configured ? (
            <Button
              size="sm"
              icon={<Send className="size-3.5" />}
              loading={publishing}
              disabled={!link.enabled}
              onClick={onPublish}
            >
              Publier
            </Button>
          ) : null
        }
      />
      <div className="space-y-3 px-5 py-4 text-sm">
        {link.push_token_unreadable ? (
          <p className="text-amber-300">
            Le jeton enregistré ne peut plus être déchiffré (clé secrète de MSM changée). Saisissez-le
            à nouveau.
          </p>
        ) : !link.push_configured ? (
          <p className="text-slate-400">
            Aucun jeton d'écriture : l'état des mods n'est pas publié. Ajoutez la route{' '}
            <code className="text-slate-300">PUT /msm/state</code> au serveur de fichiers, puis
            renseignez le même jeton ici.
          </p>
        ) : (
          <div className="flex flex-wrap items-center gap-3">
            <Badge
              className={
                publish.up_to_date
                  ? 'bg-emerald-500/10 text-emerald-300 ring-emerald-500/30'
                  : 'bg-amber-500/10 text-amber-300 ring-amber-500/30'
              }
            >
              {publish.up_to_date ? 'à jour' : 'envoi en attente'}
            </Badge>
            <span className="text-xs text-slate-400">
              Révision {publish.state_revision}
              {publish.last_push_at ? ` · envoyée ${formatRelative(publish.last_push_at)}` : ''}
            </span>
          </div>
        )}

        {publish.last_push_error ? (
          <p className="rounded-lg border border-red-900/60 bg-red-950/40 px-3 py-2 text-xs text-red-200">
            {publish.last_push_error}
          </p>
        ) : null}

        {publish.disabled_files.length > 0 ? (
          <div>
            <p className="mb-1.5 text-xs text-slate-500">
              Désactivés ({publish.disabled_files.length})
            </p>
            <ul className="flex flex-wrap gap-1.5">
              {publish.disabled_files.map((path) => (
                <li
                  key={path}
                  className="rounded bg-slate-800 px-2 py-0.5 font-mono text-xs text-slate-300"
                >
                  {path}
                </li>
              ))}
            </ul>
          </div>
        ) : (
          <p className="text-xs text-slate-500">Aucun mod désactivé.</p>
        )}
      </div>
    </Card>
  )
}

// --------------------------------------------------------------------------- //
//  Mods du modpack
// --------------------------------------------------------------------------- //
function ModsCard({
  link,
  canEdit,
  busyPath,
  onSide,
}: {
  link: LauncherLink
  canEdit: boolean
  busyPath: string | undefined
  onSide: (path: string, side: ModSide | null) => void
}) {
  if (link.mods.length === 0) return null
  const clientOnly = link.mods.filter((mod) => mod.side === 'client').length

  return (
    <Card>
      <CardHeader
        title={`Modpack (${link.mods.length})`}
        subtitle={`${clientOnly} réservé${clientOnly > 1 ? 's' : ''} aux joueurs, jamais installé${clientOnly > 1 ? 's' : ''} sur le serveur. Un mod mal détecté peut être forcé ici.`}
      />
      <ul className="divide-y divide-slate-800/60">
        {link.mods.map((mod) => (
          <li key={mod.path} className="flex flex-wrap items-center gap-3 px-5 py-2.5">
            <div className="min-w-0 flex-1">
              <p className="truncate font-mono text-sm text-slate-200">{mod.path}</p>
              <p className="text-xs text-slate-500">
                {formatBytes(mod.size)} · {SIDE_SOURCES[mod.side_source]}
                {mod.disabled_upstream ? ' · retiré du modpack des joueurs' : ''}
              </p>
            </div>
            <OnServerBadge mod={mod} />
            <Select
              className="w-44"
              value={mod.override ?? ''}
              disabled={!canEdit || busyPath === mod.path}
              onChange={(event) =>
                onSide(mod.path, event.target.value ? (event.target.value as ModSide) : null)
              }
              aria-label={`Côté de ${mod.path}`}
            >
              <option value="">
                {mod.side_source === 'override'
                  ? 'Automatique'
                  : `Auto : ${SIDE_LABELS[mod.side].toLowerCase()}`}
              </option>
              {(Object.keys(SIDE_LABELS) as ModSide[]).map((side) => (
                <option key={side} value={side}>
                  {SIDE_LABELS[side]}
                </option>
              ))}
            </Select>
          </li>
        ))}
      </ul>
    </Card>
  )
}

function OnServerBadge({ mod }: { mod: LauncherLinkMod }) {
  if (mod.side === 'client') {
    return <Badge className="bg-slate-700/40 text-slate-400 ring-slate-700">joueurs seulement</Badge>
  }
  if (mod.on_server === 'enabled') {
    return <Badge className="bg-emerald-500/10 text-emerald-300 ring-emerald-500/30">actif</Badge>
  }
  if (mod.on_server === 'disabled') {
    return <Badge className="bg-amber-500/10 text-amber-300 ring-amber-500/30">désactivé</Badge>
  }
  return <Badge className="bg-sky-500/10 text-sky-300 ring-sky-500/30">à installer</Badge>
}
