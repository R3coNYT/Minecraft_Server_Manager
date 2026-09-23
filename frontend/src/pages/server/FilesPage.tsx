/**
 * Mods et plugins.
 *
 * Une seule page pour les deux dossiers : la logique est identique, seul le
 * libellé change. Désactiver renomme le fichier plutôt que de le supprimer, ce
 * que l'interface annonce explicitement — sans quoi le bouton passerait pour
 * une suppression déguisée.
 */

import { useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { CloudDownload, Package, Trash2, Upload } from 'lucide-react'
import { api } from '@/lib/api'
import { hasPermission, useLauncherLink, useMe } from '@/hooks/useApi'
import { useToasts } from '@/stores/toasts'
import { formatBytes, formatRelative } from '@/lib/format'
import type { LauncherLink, ManagedFile } from '@/lib/types'
import { t, tn } from '@/i18n'
import { useServerContext } from './context'
import { Card, CardHeader, EmptyState, LoadingBlock } from '@/components/ui/primitives'
import { Button } from '@/components/ui/Button'
import { ErrorPanel } from '@/components/common/ErrorPanel'
import { ConfirmDialog } from '@/components/common/ConfirmDialog'
import { cn } from '@/lib/cn'

interface FilesPageProps {
  area: 'mods' | 'plugins'
}

export function FilesPage({ area }: FilesPageProps) {
  const { server } = useServerContext()
  const queryClient = useQueryClient()
  const { data: me } = useMe()
  const push = useToasts((state) => state.push)
  const pushError = useToasts((state) => state.pushError)

  const inputRef = useRef<HTMLInputElement>(null)
  const [toDelete, setToDelete] = useState<ManagedFile | null>(null)
  const [pendingOverwrite, setPendingOverwrite] = useState<File | null>(null)

  const canUpload = hasPermission(me, 'file:upload')
  const canDelete = hasPermission(me, 'file:delete')
  const canToggle = hasPermission(me, 'file:toggle')

  const { data: launcherLink } = useLauncherLink(server.id)
  const { data, isLoading, error } = useQuery({
    queryKey: ['files', server.id, area],
    queryFn: () => api.files.list(server.id, area),
  })

  const refresh = () => void queryClient.invalidateQueries({ queryKey: ['files', server.id, area] })

  const upload = useMutation({
    mutationFn: ({ file, overwrite }: { file: File; overwrite: boolean }) =>
      api.files.upload(server.id, area, file, overwrite),
    onSuccess: (file) => {
      push({ kind: 'success', title: t('files.uploaded', { name: file.name }) })
      setPendingOverwrite(null)
      refresh()
    },
    onError: (uploadError, variables) => {
      // 409 : le fichier existe déjà. On propose le remplacement plutôt que
      // d'imposer à l'utilisateur de le supprimer d'abord.
      if (uploadError instanceof Error && 'status' in uploadError && uploadError.status === 409) {
        setPendingOverwrite(variables.file)
        return
      }
      pushError(uploadError, t('files.uploadRefused'))
    },
  })

  const toggle = useMutation({
    mutationFn: ({ name, enabled }: { name: string; enabled: boolean }) =>
      api.files.toggle(server.id, area, name, enabled),
    onSuccess: (file) => {
      push({
        kind: 'success',
        title: file.enabled
          ? t('files.enabledToast', { name: file.name })
          : t('files.disabledToast', { name: file.name }),
        detail: file.enabled ? undefined : t('files.renamedNotDeleted'),
      })
      refresh()
    },
    onError: (toggleError) => pushError(toggleError),
  })

  const remove = useMutation({
    mutationFn: (name: string) => api.files.remove(server.id, area, name),
    onSuccess: () => {
      push({ kind: 'success', title: t('files.deleted') })
      setToDelete(null)
      refresh()
    },
    onError: (deleteError) => pushError(deleteError),
  })

  const onPick = (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0]
    if (file) upload.mutate({ file, overwrite: false })
    event.target.value = ''
  }

  if (isLoading) return <LoadingBlock />

  const total = data?.length ?? 0
  const enabledCount = (data ?? []).filter((file) => file.enabled).length

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-4xl space-y-4 p-4 sm:p-6">
        <ErrorPanel error={error} />

        {launcherLink ? <LauncherNotice area={area} link={launcherLink} /> : null}

        <Card>
          <CardHeader
            title={area === 'mods' ? tn('files.modCount', total) : tn('files.pluginCount', total)}
            subtitle={
              data && data.length > 0
                ? `${tn('files.enabledCount', enabledCount)}, ${tn('files.disabledCount', total - enabledCount)}`
                : undefined
            }
            action={
              canUpload ? (
                <>
                  <input
                    ref={inputRef}
                    type="file"
                    accept=".jar"
                    onChange={onPick}
                    className="hidden"
                  />
                  <Button
                    size="sm"
                    variant="primary"
                    icon={<Upload className="size-4" />}
                    loading={upload.isPending}
                    onClick={() => inputRef.current?.click()}
                  >
                    {t('files.upload')}
                  </Button>
                </>
              ) : undefined
            }
          />

          {!data || data.length === 0 ? (
            <EmptyState
              icon={<Package className="size-8" />}
              title={area === 'mods' ? t('files.noMod') : t('files.noPlugin')}
              description={
                canUpload ? t('files.emptyUpload', { folder: area }) : t('files.empty', { folder: area })
              }
            />
          ) : (
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-slate-800 text-left text-xs text-slate-500">
                  <th className="px-5 py-2.5 font-medium">{t('files.file')}</th>
                  <th className="px-5 py-2.5 font-medium">{t('files.size')}</th>
                  <th className="px-5 py-2.5 font-medium">{t('files.modified')}</th>
                  <th className="px-5 py-2.5 font-medium">{t('files.active')}</th>
                  <th className="px-5 py-2.5" />
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-800/60">
                {data.map((file) => (
                  <tr key={file.name} className={cn(!file.enabled && 'opacity-60')}>
                    <td className="px-5 py-2.5">
                      <span className="font-mono text-xs text-slate-200">{file.name}</span>
                      {!file.enabled ? (
                        <span className="ml-2 text-[11px] text-amber-400">
                          {t('common.disabled')}
                        </span>
                      ) : null}
                    </td>
                    <td className="px-5 py-2.5 tabular-nums text-slate-400">
                      {formatBytes(file.size_bytes)}
                    </td>
                    <td className="px-5 py-2.5 text-xs text-slate-500">
                      {formatRelative(file.modified_at)}
                    </td>
                    <td className="px-5 py-2.5">
                      <input
                        type="checkbox"
                        className="size-4 rounded border-slate-600 bg-slate-900 text-emerald-600"
                        checked={file.enabled}
                        disabled={!canToggle || toggle.isPending}
                        onChange={(event) =>
                          toggle.mutate({ name: file.name, enabled: event.target.checked })
                        }
                        title={file.enabled ? t('files.disableHint') : t('files.enableHint')}
                      />
                    </td>
                    <td className="px-5 py-2.5 text-right">
                      {canDelete ? (
                        <Button
                          size="sm"
                          variant="ghost"
                          icon={<Trash2 className="size-3.5" />}
                          onClick={() => setToDelete(file)}
                        >
                          <span className="sr-only">{t('files.deleteNamed', { name: file.name })}</span>
                        </Button>
                      ) : null}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Card>

        <p className="px-1 text-xs text-slate-600">{t('files.neverExecuted')}</p>
      </div>

      <ConfirmDialog
        open={toDelete !== null}
        title={t('files.deleteTitle', { name: toDelete?.name ?? '' })}
        consequence={t('files.deleteConsequence')}
        confirmLabel={t('common.delete')}
        danger
        loading={remove.isPending}
        onConfirm={() => toDelete && remove.mutate(toDelete.name)}
        onClose={() => setToDelete(null)}
      />

      <ConfirmDialog
        open={pendingOverwrite !== null}
        title={t('files.replaceTitle')}
        consequence={t('files.replaceConsequence', {
          name: pendingOverwrite?.name ?? '',
          folder: area,
        })}
        confirmLabel={t('files.replace')}
        danger
        loading={upload.isPending}
        onConfirm={() =>
          pendingOverwrite && upload.mutate({ file: pendingOverwrite, overwrite: true })
        }
        onClose={() => setPendingOverwrite(null)}
      />
    </div>
  )
}

/** Rappelle que ce dossier est relié au launcher, et ce qui attend le redémarrage. */
function LauncherNotice({ area, link }: { area: string; link: LauncherLink }) {
  const synced = link.sync_paths.some((path) => path.startsWith(`${area}/`) || path === `${area}/`)
  if (!synced || !link.enabled) return null
  return (
    <div className="flex items-start gap-2.5 rounded-lg border border-sky-900/60 bg-sky-950/30 px-4 py-3 text-sm text-sky-100">
      <CloudDownload className="mt-0.5 size-4 shrink-0 text-sky-300" />
      <span>
        {link.pending
          ? t('files.launcherPending', {
              installs: link.pending.installs,
              removes: link.pending.removes,
            })
          : t('files.launcherSynced')}{' '}
        {link.push_configured ? t('files.launcherToggle') : t('files.launcherToggleNoToken')}{' '}
        <Link to="../launcher" relative="path" className="text-sky-300 underline-offset-2 hover:underline">
          {t('files.launcherLink')}
        </Link>
      </span>
    </div>
  )
}
