/** Journal d'audit : qui a fait quoi, quand, sur quel serveur. */

import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { ShieldAlert } from 'lucide-react'
import { api } from '@/lib/api'
import { queryKeys, useServers } from '@/hooks/useApi'
import { formatDateTime } from '@/lib/format'
import { cn } from '@/lib/cn'
import { Card, CardHeader, EmptyState, LoadingBlock, Select } from '@/components/ui/primitives'
import { Button } from '@/components/ui/Button'
import { ErrorPanel } from '@/components/common/ErrorPanel'

const PAGE_SIZE = 50

const RESULT_STYLES: Record<string, string> = {
  SUCCESS: 'text-slate-400',
  DENIED: 'text-amber-300',
  ERROR: 'text-red-300',
}

export function AuditPage() {
  const [serverId, setServerId] = useState<string>('')
  const [action, setAction] = useState<string>('')
  const [offset, setOffset] = useState(0)

  const { data: servers } = useServers()
  const { data: actions } = useQuery({
    queryKey: ['audit-actions'],
    queryFn: () => api.audit.actions(),
    staleTime: Infinity,
  })

  const params = {
    ...(serverId ? { server_id: Number(serverId) } : {}),
    ...(action ? { action } : {}),
    limit: PAGE_SIZE,
    offset,
  }

  const { data, isLoading, error } = useQuery({
    queryKey: queryKeys.audit(params),
    queryFn: () => api.audit.search(params),
  })

  const total = data?.total ?? 0
  const pageStart = total === 0 ? 0 : offset + 1
  const pageEnd = Math.min(offset + PAGE_SIZE, total)

  return (
    <div className="mx-auto max-w-6xl space-y-5 p-4 sm:p-6">
      <div>
        <h1 className="text-lg font-semibold text-slate-100">Journal d'audit</h1>
        <p className="text-sm text-slate-500">
          Chaque action passée par le panneau y est consignée, y compris les refus.
        </p>
      </div>

      <div className="flex flex-wrap gap-3">
        <div className="w-56">
          <Select
            value={serverId}
            onChange={(event) => {
              setServerId(event.target.value)
              setOffset(0)
            }}
          >
            <option value="">Tous les serveurs</option>
            {(servers ?? []).map((server) => (
              <option key={server.id} value={server.id}>
                {server.name}
              </option>
            ))}
          </Select>
        </div>
        <div className="w-64">
          <Select
            value={action}
            onChange={(event) => {
              setAction(event.target.value)
              setOffset(0)
            }}
          >
            <option value="">Toutes les actions</option>
            {(actions ?? []).map((value) => (
              <option key={value} value={value}>
                {value}
              </option>
            ))}
          </Select>
        </div>
      </div>

      <ErrorPanel error={error} />

      <Card>
        <CardHeader
          title="Événements"
          subtitle={total > 0 ? `${pageStart} – ${pageEnd} sur ${total}` : undefined}
        />

        {isLoading ? (
          <LoadingBlock />
        ) : data && data.entries.length > 0 ? (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[720px] text-sm">
              <thead>
                <tr className="border-b border-slate-800 text-left text-xs text-slate-500">
                  <th className="px-5 py-2.5 font-medium">Date</th>
                  <th className="px-5 py-2.5 font-medium">Auteur</th>
                  <th className="px-5 py-2.5 font-medium">Action</th>
                  <th className="px-5 py-2.5 font-medium">Détail</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-800/60">
                {data.entries.map((entry) => (
                  <tr key={entry.id} className="align-top">
                    <td className="whitespace-nowrap px-5 py-2.5 text-xs tabular-nums text-slate-500">
                      {formatDateTime(entry.ts)}
                    </td>
                    <td className="whitespace-nowrap px-5 py-2.5">
                      <span className="text-slate-200">{entry.actor_username}</span>
                      {entry.actor_role ? (
                        <span className="ml-1.5 text-xs text-slate-600">{entry.actor_role}</span>
                      ) : null}
                    </td>
                    <td className="whitespace-nowrap px-5 py-2.5">
                      <span className={cn('font-mono text-xs', RESULT_STYLES[entry.result])}>
                        {entry.action}
                      </span>
                      {entry.result !== 'SUCCESS' ? (
                        <span className="ml-1.5 inline-flex items-center gap-1 text-[11px] text-amber-400">
                          <ShieldAlert className="size-3" />
                          {entry.result === 'DENIED' ? 'refusé' : 'erreur'}
                        </span>
                      ) : null}
                    </td>
                    <td className="px-5 py-2.5 text-slate-300">{entry.summary}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <EmptyState title="Aucun événement" description="Aucune entrée ne correspond aux filtres." />
        )}

        {total > PAGE_SIZE ? (
          <div className="flex items-center justify-between border-t border-slate-800 px-5 py-3">
            <Button
              size="sm"
              variant="ghost"
              disabled={offset === 0}
              onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
            >
              Précédent
            </Button>
            <Button
              size="sm"
              variant="ghost"
              disabled={pageEnd >= total}
              onClick={() => setOffset(offset + PAGE_SIZE)}
            >
              Suivant
            </Button>
          </div>
        ) : null}
      </Card>
    </div>
  )
}
