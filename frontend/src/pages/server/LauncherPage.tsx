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
  Copy,
  KeyRound,
  Link2,
  RefreshCw,
  Send,
  Trash2,
  Wrench,
} from 'lucide-react'
import { api } from '@/lib/api'
import { can, queryKeys, useLauncherLink } from '@/hooks/useApi'
import { useToasts } from '@/stores/toasts'
import { formatBytes, formatRelative } from '@/lib/format'
import type { LauncherLink, LauncherLinkMod, LauncherSyncStatus, ModSide } from '@/lib/types'
import { useServerContext } from './context'
import { copyText } from '@/lib/clipboard'
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
import { cn } from '@/lib/cn'
import { t, tn, type MessageKey } from '@/i18n'

const STATUS_STYLES: Record<LauncherSyncStatus, string> = {
  NEVER: 'bg-slate-700/40 text-slate-400 ring-slate-700',
  UP_TO_DATE: 'bg-emerald-500/10 text-emerald-300 ring-emerald-500/30',
  APPLIED: 'bg-emerald-500/10 text-emerald-300 ring-emerald-500/30',
  PENDING_RESTART: 'bg-sky-500/10 text-sky-300 ring-sky-500/30',
  BLOCKED: 'bg-amber-500/10 text-amber-300 ring-amber-500/30',
  FAILED: 'bg-red-500/10 text-red-300 ring-red-500/30',
}

function statusLabel(status: LauncherSyncStatus): string {
  return t(`launcherPage.status.${status}`)
}

const SIDES: ModSide[] = ['client', 'server', 'both']

function sideLabel(side: ModSide): string {
  return t(`launcherPage.side.${side}`)
}

const SIDE_SOURCES: Record<LauncherLinkMod['side_source'], MessageKey> = {
  override: 'launcherPage.source.override',
  manifest: 'launcherPage.source.manifest',
  detected: 'launcherPage.source.detected',
  default: 'launcherPage.source.default',
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

/**
 * Jeton aléatoire de même force que `msm secret` : 64 octets en base64url.
 *
 * Généré dans le navigateur : `crypto.getRandomValues` est une source
 * cryptographique, disponible même hors HTTPS, et le jeton ne transite pas par
 * le réseau avant d'être enregistré.
 */
function generateToken(): string {
  const bytes = crypto.getRandomValues(new Uint8Array(64))
  return btoa(String.fromCharCode(...bytes))
    .replace(/\+/g, '-')
    .replace(/\//g, '_')
    .replace(/=+$/, '')
}

export function LauncherPage() {
  const { server } = useServerContext()
  const canEdit = can(server, 'server:edit')
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
      push({
        kind: next.last_sync_status === 'FAILED' ? 'error' : 'success',
        title: t('launcherPage.syncResult', { status: statusLabel(next.last_sync_status) }),
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
          ? { kind: 'success', title: t('launcherPage.published') }
          : {
              kind: 'error',
              title: t('launcherPage.publishFailed'),
              detail: next.publish.last_push_error ?? t('launcherPage.noToken'),
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
        title: t('launcherPage.sideSaved'),
        detail: t('launcherPage.sideSavedDetail'),
      })
    },
    onError: (sideError) => pushError(sideError),
  })

  const remove = useMutation({
    mutationFn: () => api.launcherLink.remove(server.id),
    onSuccess: () => {
      queryClient.setQueryData(queryKeys.launcherLink(server.id), null)
      setConfirmRemove(false)
      push({
        kind: 'success',
        title: t('launcherPage.removed'),
        detail: t('launcherPage.removedDetail'),
      })
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
              title={t('launcherPage.emptyTitle')}
              description={
                <>
                  {t('launcherPage.emptyText')}{' '}
                  <code className="text-slate-300">docs/LAUNCHER_INTEGRATION.md</code>.
                </>
              }
              action={
                canEdit ? (
                  <Button variant="primary" size="sm" onClick={() => setEditing(true)}>
                    {t('launcherPage.link')}
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
        title={t('launcherPage.massDeleteTitle')}
        description={link?.last_sync_error ?? undefined}
        consequence={t(
          link?.pending?.server_running
            ? 'launcherPage.massDeleteConsequenceNextStart'
            : 'launcherPage.massDeleteConsequence',
          { server: server.name },
        )}
        confirmLabel={t('launcherPage.syncAnyway')}
        danger
        requireTyping={server.name}
        loading={sync.isPending}
        error={sync.error}
        onConfirm={() => sync.mutate(true)}
        onClose={() => setConfirmMassDelete(false)}
      />

      <ConfirmDialog
        open={confirmRemove}
        title={t('launcherPage.removeTitle')}
        consequence={t('launcherPage.removeConsequence')}
        confirmLabel={t('launcherPage.remove')}
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
  // Un jeton généré s'affiche en clair : il faut le recopier sur le serveur de
  // fichiers. Un jeton saisi à la main reste masqué.
  const [generated, setGenerated] = useState(false)

  const copyToken = async () => {
    const copied = await copyText(draft.push_token)
    push(
      copied
        ? {
            kind: 'success',
            title: t('launcherPage.tokenCopied'),
            detail: t('launcherPage.tokenCopiedDetail'),
          }
        : {
            kind: 'error',
            title: t('launcherPage.tokenCopyFailed'),
            detail: t('launcherPage.tokenCopyFailedDetail'),
          },
    )
  }

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
        title: t('launcherPage.saved'),
        detail: next.enabled ? t('launcherPage.savedDetail') : undefined,
      })
      onDone(next)
    },
  })

  const set = <K extends keyof Draft>(key: K, value: Draft[K]) =>
    setDraft((current) => ({ ...current, [key]: value }))

  return (
    <Card>
      <CardHeader
        title={link ? t('launcherPage.editLink') : t('launcherPage.link')}
        subtitle={t('launcherPage.formSubtitle')}
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
          label={t('launcherPage.url')}
          hint={t('launcherPage.urlHint')}
        >
          <Input
            required
            value={draft.file_server_url}
            onChange={(event) => set('file_server_url', event.target.value)}
            placeholder="https://frankumc.frankulin.fr"
          />
        </Field>

        <Field
          label={t('launcherPage.token')}
          hint={
            link?.push_configured && !clearToken
              ? t('launcherPage.tokenSaved', { hint: link.push_token_hint ?? '…' })
              : t('launcherPage.tokenHelp')
          }
        >
          <div className="flex flex-wrap gap-2">
            <Input
              type={generated ? 'text' : 'password'}
              autoComplete="off"
              spellCheck={false}
              className={cn('min-w-[12rem] flex-1', generated && 'font-mono text-xs')}
              value={draft.push_token}
              onChange={(event) => {
                setGenerated(false)
                set('push_token', event.target.value)
              }}
              placeholder={link?.push_configured ? '••••••••' : ''}
              disabled={clearToken}
            />
            <Button
              type="button"
              variant="secondary"
              icon={<KeyRound className="size-4" />}
              title={t('launcherPage.generateTokenHint')}
              disabled={clearToken}
              onClick={() => {
                setGenerated(true)
                set('push_token', generateToken())
              }}
            >
              {t('launcherPage.generateToken')}
            </Button>
            {draft.push_token ? (
              <Button
                type="button"
                variant="ghost"
                icon={<Copy className="size-4" />}
                disabled={clearToken}
                onClick={() => void copyToken()}
              >
                {t('launcherPage.copyToken')}
              </Button>
            ) : null}
          </div>
          {generated ? (
            <p className="mt-1.5 text-xs text-amber-300/90">{t('launcherPage.tokenGenerated')}</p>
          ) : null}
        </Field>
        {link?.push_configured ? (
          <Checkbox
            label={t('launcherPage.clearToken')}
            hint={t('launcherPage.clearTokenHint')}
            checked={clearToken}
            onChange={(event) => setClearToken(event.target.checked)}
          />
        ) : null}

        <div className="grid gap-4 sm:grid-cols-2">
          <Field
            label={t('launcherPage.paths')}
            hint={t('launcherPage.pathsHint')}
          >
            <Input
              value={draft.sync_paths}
              onChange={(event) => set('sync_paths', event.target.value)}
              placeholder="mods/"
            />
          </Field>
          <Field label={t('launcherPage.interval')} hint={t('launcherPage.intervalHint')}>
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
          label={t('launcherPage.enabled')}
          hint={t('launcherPage.enabledHint')}
          checked={draft.enabled}
          onChange={(event) => set('enabled', event.target.checked)}
        />

        <div className="flex justify-end gap-2">
          <Button type="button" variant="ghost" size="sm" onClick={() => onDone(null)}>
            {t('common.cancel')}
          </Button>
          <Button type="submit" variant="primary" size="sm" loading={save.isPending}>
            {t('common.save')}
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
  const summary = link.last_sync_summary

  return (
    <Card>
      <CardHeader
        title={t('launcherPage.syncTitle')}
        subtitle={
          <span className="font-mono">
            {link.file_server_url} · {link.sync_paths.join(', ')}
          </span>
        }
        action={
          canEdit ? (
            <div className="flex shrink-0 gap-1.5">
              <Button size="sm" variant="ghost" onClick={onEdit}>
                {t('common.edit')}
              </Button>
              <Button
                size="sm"
                variant="ghost"
                icon={<Trash2 className="size-3.5" />}
                onClick={onRemove}
                aria-label={t('launcherPage.removeLink')}
              />
              <Button
                size="sm"
                variant="primary"
                icon={<RefreshCw className="size-3.5" />}
                loading={syncing}
                disabled={!link.enabled}
                onClick={onSync}
              >
                {t('launcherPage.sync')}
              </Button>
            </div>
          ) : null
        }
      />

      <dl className="grid gap-x-6 gap-y-3 px-5 py-4 text-sm sm:grid-cols-4">
        <div>
          <dt className="text-xs text-slate-500">{t('launcherPage.nextSync')}</dt>
          <dd className="mt-0.5 text-slate-200">
            <SyncCountdown serverId={serverId} link={link} />
          </dd>
        </div>
        <div>
          <dt className="text-xs text-slate-500">{t('launcherPage.last')}</dt>
          <dd className="mt-0.5 text-slate-200">{formatRelative(link.last_sync_at)}</dd>
        </div>
        <div>
          <dt className="text-xs text-slate-500">{t('launcherPage.result')}</dt>
          <dd className="mt-0.5">
            <Badge className={STATUS_STYLES[link.last_sync_status]}>
              {statusLabel(link.last_sync_status)}
            </Badge>
          </dd>
        </div>
        <div>
          <dt className="text-xs text-slate-500">{t('launcherPage.packVersion')}</dt>
          <dd className="mt-0.5 text-slate-200">{link.pack_version ?? '—'}</dd>
        </div>
      </dl>

      {link.pending ? (
        <div className="mx-5 mb-4 flex items-start gap-2.5 rounded-lg border border-sky-900/60 bg-sky-950/30 px-4 py-3 text-sm text-sky-100">
          <CloudDownload className="mt-0.5 size-4 shrink-0 text-sky-300" />
          <span>
            {pendingText(link.pending)}{' '}
            {link.pending.server_running
              ? t('launcherPage.pendingAtStart')
              : t('launcherPage.pendingSoon')}
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
                  {t('launcherPage.confirmSync')}
                </Button>
              ) : null}
            </div>
          </div>
        </div>
      ) : null}

      {summary.installs !== undefined && !link.last_sync_error ? (
        <p className="border-t border-slate-800 px-5 py-3 text-xs text-slate-400">
          {summaryText(summary)}
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

function summaryText(summary: LauncherLink['last_sync_summary']): string {
  const parts = [
    t('launcherPage.toInstall', { count: summary.installs ?? 0 }),
    t('launcherPage.toRemove', { count: summary.removes ?? 0 }),
    tn('launcherPage.unchanged', summary.unchanged ?? 0),
  ]
  if (summary.adopted) parts.push(t('launcherPage.adopted', { count: summary.adopted }))
  if (summary.client_only) parts.push(tn('launcherPage.clientOnly', summary.client_only))
  return t('launcherPage.lastRun', { parts: parts.join(', ') })
}

function pendingText(pending: NonNullable<LauncherLink['pending']>): string {
  const parts: string[] = []
  if (pending.installs) {
    parts.push(
      tn('launcherPage.pendingInstalls', pending.installs, {
        size: formatBytes(pending.download_bytes),
      }),
    )
  }
  if (pending.removes) {
    parts.push(t('launcherPage.toRemove', { count: pending.removes }))
  }
  return t('launcherPage.pending', { parts: parts.join(', ') })
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
        title={t('launcherPage.publishTitle')}
        subtitle={t('launcherPage.publishSubtitle')}
        action={
          canEdit && link.push_configured ? (
            <Button
              size="sm"
              icon={<Send className="size-3.5" />}
              loading={publishing}
              disabled={!link.enabled}
              onClick={onPublish}
            >
              {t('launcherPage.publish')}
            </Button>
          ) : null
        }
      />
      <div className="space-y-3 px-5 py-4 text-sm">
        {link.push_token_unreadable ? (
          <p className="text-amber-300">
            {t('launcherPage.tokenUnreadable')}
          </p>
        ) : !link.push_configured ? (
          <p className="text-slate-400">
            {t('launcherPage.noTokenText', { route: 'PUT /msm/state' })}
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
              {publish.up_to_date ? t('launcherPage.upToDate') : t('launcherPage.sendPending')}
            </Badge>
            <span className="text-xs text-slate-400">
              {t('launcherPage.revision', { revision: publish.state_revision })}
              {publish.last_push_at
                ? t('launcherPage.sent', { when: formatRelative(publish.last_push_at) })
                : ''}
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
              {t('launcherPage.disabledFiles', { count: publish.disabled_files.length })}
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
          <p className="text-xs text-slate-500">{t('launcherPage.noDisabled')}</p>
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
        title={t('launcherPage.modpack', { count: link.mods.length })}
        subtitle={tn('launcherPage.modpackSubtitle', clientOnly)}
      />
      <ul className="divide-y divide-slate-800/60">
        {link.mods.map((mod) => (
          <li key={mod.path} className="flex flex-wrap items-center gap-3 px-5 py-2.5">
            <div className="min-w-0 flex-1">
              <p className="truncate font-mono text-sm text-slate-200">{mod.path}</p>
              <p className="text-xs text-slate-500">
                {formatBytes(mod.size)} · {t(SIDE_SOURCES[mod.side_source])}
                {mod.disabled_upstream ? t('launcherPage.removedUpstream') : ''}
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
              aria-label={t('launcherPage.sideOf', { path: mod.path })}
            >
              <option value="">
                {mod.side_source === 'override'
                  ? t('launcherPage.automatic')
                  : t('launcherPage.auto', { side: sideLabel(mod.side).toLowerCase() })}
              </option>
              {SIDES.map((side) => (
                <option key={side} value={side}>
                  {sideLabel(side)}
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
    return <Badge className="bg-slate-700/40 text-slate-400 ring-slate-700">{t('launcherPage.playersOnly')}</Badge>
  }
  if (mod.on_server === 'enabled') {
    return <Badge className="bg-emerald-500/10 text-emerald-300 ring-emerald-500/30">{t('launcherPage.active')}</Badge>
  }
  if (mod.on_server === 'disabled') {
    return <Badge className="bg-amber-500/10 text-amber-300 ring-amber-500/30">{t('launcherPage.disabled')}</Badge>
  }
  return <Badge className="bg-sky-500/10 text-sky-300 ring-sky-500/30">{t('launcherPage.toInstallBadge')}</Badge>
}
