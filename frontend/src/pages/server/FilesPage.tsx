/**
 * Mods et plugins.
 *
 * Une seule page pour les deux dossiers : la logique est identique, seul le
 * libellé change. Désactiver renomme le fichier plutôt que de le supprimer, ce
 * que l'interface annonce explicitement — sans quoi le bouton passerait pour
 * une suppression déguisée.
 */

import { useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Package, Trash2, Upload } from 'lucide-react'
import { api } from '@/lib/api'
import { hasPermission, useMe } from '@/hooks/useApi'
import { useToasts } from '@/stores/toasts'
import { formatBytes, formatRelative } from '@/lib/format'
import type { ManagedFile } from '@/lib/types'
import { useServerContext } from './context'
import { Card, CardHeader, EmptyState, LoadingBlock } from '@/components/ui/primitives'
import { Button } from '@/components/ui/Button'
import { ErrorPanel } from '@/components/common/ErrorPanel'
import { ConfirmDialog } from '@/components/common/ConfirmDialog'
import { cn } from '@/lib/cn'

interface FilesPageProps {
  area: 'mods' | 'plugins'
  label: string
}

export function FilesPage({ area, label }: FilesPageProps) {
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

  const { data, isLoading, error } = useQuery({
    queryKey: ['files', server.id, area],
    queryFn: () => api.files.list(server.id, area),
  })

  const refresh = () => void queryClient.invalidateQueries({ queryKey: ['files', server.id, area] })

  const upload = useMutation({
    mutationFn: ({ file, overwrite }: { file: File; overwrite: boolean }) =>
      api.files.upload(server.id, area, file, overwrite),
    onSuccess: (file) => {
      push({ kind: 'success', title: `« ${file.name} » déposé` })
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
      pushError(uploadError, 'Téléversement refusé')
    },
  })

  const toggle = useMutation({
    mutationFn: ({ name, enabled }: { name: string; enabled: boolean }) =>
      api.files.toggle(server.id, area, name, enabled),
    onSuccess: (file) => {
      push({
        kind: 'success',
        title: file.enabled ? `« ${file.name} » activé` : `« ${file.name} » désactivé`,
        detail: file.enabled ? undefined : 'Le fichier a été renommé, pas supprimé.',
      })
      refresh()
    },
    onError: (toggleError) => pushError(toggleError),
  })

  const remove = useMutation({
    mutationFn: (name: string) => api.files.remove(server.id, area, name),
    onSuccess: () => {
      push({ kind: 'success', title: 'Fichier supprimé' })
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

  const enabledCount = (data ?? []).filter((file) => file.enabled).length

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-4xl space-y-4 p-4 sm:p-6">
        <ErrorPanel error={error} />

        <Card>
          <CardHeader
            title={`${data?.length ?? 0} ${label.toLowerCase()}`}
            subtitle={
              data && data.length > 0
                ? `${enabledCount} actif${enabledCount > 1 ? 's' : ''}, ${data.length - enabledCount} désactivé${data.length - enabledCount > 1 ? 's' : ''}`
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
                    Téléverser
                  </Button>
                </>
              ) : undefined
            }
          />

          {!data || data.length === 0 ? (
            <EmptyState
              icon={<Package className="size-8" />}
              title={`Aucun ${label.toLowerCase().replace(/s$/, '')}`}
              description={
                canUpload
                  ? `Déposer un fichier .jar pour l'ajouter au dossier ${area}. Il ne sera chargé qu'au prochain démarrage du serveur.`
                  : `Le dossier ${area} est vide.`
              }
            />
          ) : (
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-slate-800 text-left text-xs text-slate-500">
                  <th className="px-5 py-2.5 font-medium">Fichier</th>
                  <th className="px-5 py-2.5 font-medium">Taille</th>
                  <th className="px-5 py-2.5 font-medium">Modifié</th>
                  <th className="px-5 py-2.5 font-medium">Actif</th>
                  <th className="px-5 py-2.5" />
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-800/60">
                {data.map((file) => (
                  <tr key={file.name} className={cn(!file.enabled && 'opacity-60')}>
                    <td className="px-5 py-2.5">
                      <span className="font-mono text-xs text-slate-200">{file.name}</span>
                      {!file.enabled ? (
                        <span className="ml-2 text-[11px] text-amber-400">désactivé</span>
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
                        title={
                          file.enabled
                            ? 'Désactiver — le fichier sera renommé, pas supprimé'
                            : 'Réactiver'
                        }
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
                          <span className="sr-only">Supprimer {file.name}</span>
                        </Button>
                      ) : null}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Card>

        <p className="px-1 text-xs text-slate-600">
          Les fichiers déposés ne sont jamais exécutés par MSM : seul le serveur Minecraft les
          chargera, à son prochain démarrage.
        </p>
      </div>

      <ConfirmDialog
        open={toDelete !== null}
        title={`Supprimer « ${toDelete?.name} » ?`}
        consequence="Le fichier sera définitivement effacé du disque. Pour le retirer temporairement, préférer la désactivation."
        confirmLabel="Supprimer"
        danger
        loading={remove.isPending}
        onConfirm={() => toDelete && remove.mutate(toDelete.name)}
        onClose={() => setToDelete(null)}
      />

      <ConfirmDialog
        open={pendingOverwrite !== null}
        title="Remplacer le fichier existant ?"
        consequence={`« ${pendingOverwrite?.name} » est déjà présent dans le dossier ${area}. Son contenu actuel sera écrasé.`}
        confirmLabel="Remplacer"
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
