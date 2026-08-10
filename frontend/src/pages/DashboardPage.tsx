/** Vue d'ensemble : agrégats, ressources de la machine et cartes des serveurs. */

import { useState } from 'react'
import { Link } from 'react-router-dom'
import { Cpu, HardDrive, MemoryStick, Plus, Server as ServerIcon, Users } from 'lucide-react'
import { hasPermission, useDashboard, useMe } from '@/hooks/useApi'
import { useRealtime } from '@/stores/realtime'
import { formatMemory, formatPercent, formatUptime } from '@/lib/format'
import type { Server } from '@/lib/types'
import { Card, EmptyState, LoadingBlock, StatTile } from '@/components/ui/primitives'
import { ErrorPanel } from '@/components/common/ErrorPanel'
import { ServerStatusBadge } from '@/components/servers/ServerStatusBadge'
import { ServerActions } from '@/components/servers/ServerActions'
import { Button } from '@/components/ui/Button'
import { CreateServerDialog } from '@/components/servers/CreateServerDialog'

function ServerCard({ server }: { server: Server }) {
  const { data: me } = useMe()
  const live = useRealtime((state) => state.statuses[server.id])
  const status = live ?? server.status

  const state = status?.state ?? 'UNKNOWN'
  const stats = status?.stats

  return (
    <Card className="flex flex-col">
      <div className="flex items-start justify-between gap-3 px-5 py-4">
        <div className="min-w-0">
          <Link
            to={`/servers/${server.id}`}
            className="truncate text-sm font-semibold text-slate-100 hover:text-emerald-400"
          >
            {server.name}
          </Link>
          <p className="mt-0.5 truncate text-xs text-slate-500">
            {server.server_type !== 'UNKNOWN' ? server.server_type : 'Type inconnu'}
            {server.minecraft_version ? ` · Minecraft ${server.minecraft_version}` : ''}
          </p>
        </div>
        <ServerStatusBadge state={state} />
      </div>

      <div className="grid grid-cols-3 gap-px border-y border-slate-800 bg-slate-800">
        <div className="bg-slate-900/60 px-3 py-2.5">
          <p className="text-[11px] text-slate-500">Joueurs</p>
          <p className="text-sm font-medium tabular-nums text-slate-200">
            {status?.players_online ?? 0}
          </p>
        </div>
        <div className="bg-slate-900/60 px-3 py-2.5">
          <p className="text-[11px] text-slate-500">Mémoire</p>
          <p className="text-sm font-medium tabular-nums text-slate-200">
            {stats && stats.memory_mb > 0 ? formatMemory(stats.memory_mb) : '—'}
          </p>
        </div>
        <div className="bg-slate-900/60 px-3 py-2.5">
          <p className="text-[11px] text-slate-500">Actif depuis</p>
          <p className="text-sm font-medium tabular-nums text-slate-200">
            {status ? formatUptime(status.uptime_s) : '—'}
          </p>
        </div>
      </div>

      {status?.last_error ? (
        <div className="px-4 pt-3">
          <p className="rounded-lg border border-red-900/60 bg-red-950/30 px-3 py-2 text-xs text-red-200">
            {status.last_error.cause ?? status.last_error.message}
          </p>
        </div>
      ) : null}

      <div className="flex items-center justify-between gap-3 px-4 py-3">
        <ServerActions
          serverId={server.id}
          serverName={server.name}
          state={state}
          size="sm"
          canStart={hasPermission(me, 'server:start')}
          canStop={hasPermission(me, 'server:stop')}
          canRestart={hasPermission(me, 'server:restart')}
          canKill={hasPermission(me, 'server:kill')}
        />
        <Link
          to={`/servers/${server.id}/console`}
          className="shrink-0 text-xs text-slate-400 hover:text-emerald-400"
        >
          Console →
        </Link>
      </div>
    </Card>
  )
}

export function DashboardPage() {
  const { data, isLoading, error } = useDashboard()
  const { data: me } = useMe()
  const liveSystem = useRealtime((state) => state.system)
  const statuses = useRealtime((state) => state.statuses)
  const [createOpen, setCreateOpen] = useState(false)

  if (isLoading) return <LoadingBlock />
  if (error) return <div className="p-6"><ErrorPanel error={error} /></div>
  if (!data) return null

  const system = liveSystem ?? data.system
  // Les compteurs vivants priment sur l'instantané REST, qui peut dater.
  const onlineCount = data.servers.filter((server) => {
    const state = statuses[server.id]?.state ?? server.status?.state
    return state === 'ONLINE' || state === 'STARTING'
  }).length
  const playersOnline = data.servers.reduce((total, server) => {
    const status = statuses[server.id] ?? server.status
    return total + (status?.players_online ?? 0)
  }, 0)

  return (
    <div className="mx-auto max-w-7xl space-y-6 p-4 sm:p-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold text-slate-100">Tableau de bord</h1>
          <p className="text-sm text-slate-500">
            {data.summary.servers_total} serveur{data.summary.servers_total > 1 ? 's' : ''} géré
            {data.summary.servers_total > 1 ? 's' : ''}, {onlineCount} en ligne
          </p>
        </div>
        {hasPermission(me, 'server:create') ? (
          <Button variant="primary" icon={<Plus className="size-4" />} onClick={() => setCreateOpen(true)}>
            Ajouter un serveur
          </Button>
        ) : null}
      </div>

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatTile
          label="Serveurs en ligne"
          value={`${onlineCount} / ${data.summary.servers_total}`}
          icon={<ServerIcon className="size-4" />}
        />
        <StatTile
          label="Joueurs connectés"
          value={playersOnline}
          icon={<Users className="size-4" />}
        />
        <StatTile
          label="Processeur"
          value={formatPercent(system.cpu_percent)}
          detail={`${system.cpu_count} cœurs`}
          icon={<Cpu className="size-4" />}
        />
        <StatTile
          label="Mémoire"
          value={formatPercent(system.memory_percent)}
          detail={`${formatMemory(system.memory_used_mb)} / ${formatMemory(system.memory_total_mb)}`}
          icon={<MemoryStick className="size-4" />}
        />
      </div>

      {system.disk_percent !== undefined ? (
        <Card className="flex items-center gap-4 px-5 py-3.5">
          <HardDrive className="size-4 shrink-0 text-slate-600" />
          <div className="min-w-0 flex-1">
            <div className="flex items-center justify-between text-xs">
              <span className="text-slate-400">Disque</span>
              <span className="tabular-nums text-slate-400">
                {system.disk_used_gb} Go / {system.disk_total_gb} Go
              </span>
            </div>
            <div className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-slate-800">
              <div
                className="h-full rounded-full bg-emerald-500/70"
                style={{ width: `${Math.min(system.disk_percent, 100)}%` }}
              />
            </div>
          </div>
        </Card>
      ) : null}

      {data.servers.length === 0 ? (
        <Card>
          <EmptyState
            icon={<ServerIcon className="size-8" />}
            title="Aucun serveur enregistré"
            description="Ajouter un serveur en indiquant le dossier qui le contient : MSM analysera son contenu et proposera une configuration de démarrage."
            action={
              hasPermission(me, 'server:create') ? (
                <Button variant="primary" icon={<Plus className="size-4" />} onClick={() => setCreateOpen(true)}>
                  Ajouter un serveur
                </Button>
              ) : null
            }
          />
        </Card>
      ) : (
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {data.servers.map((server) => (
            <ServerCard key={server.id} server={server} />
          ))}
        </div>
      )}

      <CreateServerDialog open={createOpen} onClose={() => setCreateOpen(false)} />
    </div>
  )
}
