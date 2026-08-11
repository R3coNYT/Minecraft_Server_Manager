/**
 * Éditeur de configurations.
 *
 * Deux colonnes : l'arborescence à gauche, le fichier à droite. L'éditeur est un
 * simple `textarea` en police à chasse fixe — un éditeur de code complet
 * alourdirait le paquet de plusieurs mégaoctets pour un usage qui reste
 * ponctuel. La validation syntaxique, elle, se fait côté serveur avant écriture,
 * donc une erreur est signalée avec sa ligne avant que le fichier ne soit touché.
 */

import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ChevronRight, FileCode, Folder, FolderOpen, Save } from 'lucide-react'
import { api } from '@/lib/api'
import { hasPermission, useMe } from '@/hooks/useApi'
import { useToasts } from '@/stores/toasts'
import { formatBytes, formatRelative } from '@/lib/format'
import { useServerContext } from './context'
import { EmptyState, LoadingBlock } from '@/components/ui/primitives'
import { Button } from '@/components/ui/Button'
import { ErrorPanel } from '@/components/common/ErrorPanel'
import { cn } from '@/lib/cn'

function Breadcrumb({
  path,
  onNavigate,
}: {
  path: string
  onNavigate: (path: string) => void
}) {
  const segments = path ? path.split('/') : []

  return (
    <div className="flex flex-wrap items-center gap-0.5 text-xs text-slate-500">
      <button className="hover:text-slate-200" onClick={() => onNavigate('')}>
        serveur
      </button>
      {segments.map((segment, index) => (
        <span key={`${segment}-${index}`} className="flex items-center gap-0.5">
          <ChevronRight className="size-3" />
          <button
            className="hover:text-slate-200"
            onClick={() => onNavigate(segments.slice(0, index + 1).join('/'))}
          >
            {segment}
          </button>
        </span>
      ))}
    </div>
  )
}

export function ConfigsPage() {
  const { server } = useServerContext()
  const queryClient = useQueryClient()
  const { data: me } = useMe()
  const push = useToasts((state) => state.push)

  const [directory, setDirectory] = useState('')
  const [selected, setSelected] = useState<string | null>(null)
  const [draft, setDraft] = useState('')

  const canWrite = hasPermission(me, 'config:write')

  const tree = useQuery({
    queryKey: ['configs', server.id, directory],
    queryFn: () => api.configs.browse(server.id, directory || undefined),
  })

  const file = useQuery({
    queryKey: ['config-file', server.id, selected],
    queryFn: () => api.configs.read(server.id, selected as string),
    enabled: selected !== null,
  })

  // Le brouillon suit le fichier chargé ; les modifications non enregistrées
  // sont perdues au changement de fichier, ce que le bouton signale.
  useEffect(() => {
    if (file.data) setDraft(file.data.content)
  }, [file.data])

  const save = useMutation({
    mutationFn: () => api.configs.write(server.id, selected as string, draft),
    onSuccess: () => {
      push({ kind: 'success', title: 'Fichier enregistré' })
      void queryClient.invalidateQueries({ queryKey: ['config-file', server.id, selected] })
      void queryClient.invalidateQueries({ queryKey: ['configs', server.id, directory] })
    },
  })

  const dirty = file.data !== undefined && draft !== file.data.content

  return (
    <div className="flex h-full min-h-0">
      <aside className="flex w-72 shrink-0 flex-col border-r border-slate-800">
        <div className="border-b border-slate-800 px-4 py-3">
          <Breadcrumb path={directory} onNavigate={setDirectory} />
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto p-2">
          {tree.isLoading ? (
            <LoadingBlock label="" />
          ) : tree.error ? (
            <div className="p-2">
              <ErrorPanel error={tree.error} compact />
            </div>
          ) : (
            <>
              {directory ? (
                <button
                  onClick={() => setDirectory(directory.split('/').slice(0, -1).join('/'))}
                  className="flex w-full items-center gap-2 rounded-lg px-2.5 py-1.5 text-sm text-slate-500 hover:bg-slate-800"
                >
                  <FolderOpen className="size-4" />
                  ..
                </button>
              ) : null}

              {(tree.data ?? []).map((entry) => (
                <button
                  key={entry.path}
                  onClick={() =>
                    entry.is_directory ? setDirectory(entry.path) : setSelected(entry.path)
                  }
                  disabled={!entry.is_directory && !entry.editable}
                  className={cn(
                    'flex w-full items-center gap-2 rounded-lg px-2.5 py-1.5 text-left text-sm transition-colors',
                    selected === entry.path
                      ? 'bg-slate-800 text-slate-100'
                      : 'text-slate-300 hover:bg-slate-800/60',
                    !entry.is_directory && !entry.editable && 'cursor-not-allowed opacity-40',
                  )}
                  title={
                    !entry.is_directory && !entry.editable
                      ? 'Fichier trop volumineux pour l’éditeur'
                      : undefined
                  }
                >
                  {entry.is_directory ? (
                    <Folder className="size-4 shrink-0 text-slate-500" />
                  ) : (
                    <FileCode className="size-4 shrink-0 text-slate-600" />
                  )}
                  <span className="truncate">{entry.name}</span>
                </button>
              ))}

              {tree.data?.length === 0 ? (
                <p className="px-2.5 py-3 text-xs text-slate-600">
                  Aucun fichier de configuration ici.
                </p>
              ) : null}
            </>
          )}
        </div>
      </aside>

      <section className="flex min-h-0 min-w-0 flex-1 flex-col">
        {selected === null ? (
          <EmptyState
            icon={<FileCode className="size-8" />}
            title="Aucun fichier sélectionné"
            description="Choisir un fichier dans l'arborescence pour l'ouvrir. Les formats JSON, YAML, TOML et properties sont validés avant enregistrement."
          />
        ) : file.isLoading ? (
          <LoadingBlock />
        ) : file.error ? (
          <div className="p-4">
            <ErrorPanel error={file.error} />
          </div>
        ) : file.data ? (
          <>
            <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-800 px-4 py-2.5">
              <div className="min-w-0">
                <p className="truncate font-mono text-xs text-slate-200">{file.data.path}</p>
                <p className="text-[11px] text-slate-600">
                  {file.data.format.toUpperCase()} · {formatBytes(file.data.size_bytes)} ·
                  modifié {formatRelative(file.data.modified_at)}
                  {file.data.encoding !== 'utf-8' ? ` · ${file.data.encoding}` : ''}
                </p>
              </div>
              <div className="flex items-center gap-2">
                {dirty ? (
                  <span className="text-xs text-amber-400">Modifications non enregistrées</span>
                ) : null}
                <Button
                  size="sm"
                  variant="primary"
                  icon={<Save className="size-3.5" />}
                  disabled={!canWrite || !dirty}
                  loading={save.isPending}
                  onClick={() => save.mutate()}
                >
                  Enregistrer
                </Button>
              </div>
            </div>

            {save.error ? (
              <div className="px-4 pt-3">
                <ErrorPanel error={save.error} />
              </div>
            ) : null}

            <textarea
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              readOnly={!canWrite}
              spellCheck={false}
              className="console-line min-h-0 flex-1 resize-none bg-slate-950/70 p-4 text-slate-200 focus:outline-none"
            />
          </>
        ) : null}
      </section>
    </div>
  )
}
