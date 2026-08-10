/**
 * Ossature d'une page serveur : en-tête, onglets, contenu.
 *
 * Les onglets sont construits à partir des **capacités réellement détectées**
 * dans le dossier : un serveur Vanilla n'affiche pas d'onglet Mods, un Mohist
 * affiche Mods *et* Plugins. L'interface reflète le disque, pas une famille
 * déclarée à la création.
 */

import { NavLink, Outlet, useParams } from 'react-router-dom'
import { Terminal, Users, Package, Puzzle, Settings, FileCog, CalendarClock } from 'lucide-react'
import { hasPermission, useMe, useServer, useServerStatus } from '@/hooks/useApi'
import { useServerSubscription } from '@/hooks/useServerSubscription'
import { formatUptime, formatMemory, formatPercent } from '@/lib/format'
import { cn } from '@/lib/cn'
import { LoadingBlock } from '@/components/ui/primitives'
import { ErrorPanel } from '@/components/common/ErrorPanel'
import { ServerStatusBadge } from '@/components/servers/ServerStatusBadge'
import { ServerActions } from '@/components/servers/ServerActions'

interface TabDefinition {
  to: string
  label: string
  icon: typeof Terminal
  capability?: string
  end?: boolean
}

const TABS: TabDefinition[] = [
  { to: '', label: 'Aperçu', icon: Settings, end: true },
  { to: 'console', label: 'Console', icon: Terminal, capability: 'console' },
  { to: 'players', label: 'Joueurs', icon: Users, capability: 'players' },
  { to: 'mods', label: 'Mods', icon: Package, capability: 'mods' },
  { to: 'plugins', label: 'Plugins', icon: Puzzle, capability: 'plugins' },
  { to: 'configs', label: 'Configurations', icon: FileCog, capability: 'configs' },
  { to: 'events', label: 'Événements', icon: CalendarClock, capability: 'events' },
]

export function ServerLayout() {
  const params = useParams<{ serverId: string }>()
  const serverId = Number(params.serverId)
  const { data: server, isLoading, error } = useServer(serverId)
  const { data: me } = useMe()
  const status = useServerStatus(serverId, server?.status)

  useServerSubscription(serverId)

  if (isLoading) return <LoadingBlock />
  if (error) return <div className="p-6"><ErrorPanel error={error} /></div>
  if (!server) return null

  const state = status?.state ?? 'UNKNOWN'
  const capabilities = new Set(server.capabilities)
  const tabs = TABS.filter((tab) => !tab.capability || capabilities.has(tab.capability))

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="shrink-0 border-b border-slate-800 bg-slate-900/40 px-4 pt-4 sm:px-6">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-3">
              <h1 className="truncate text-lg font-semibold text-slate-100">{server.name}</h1>
              <ServerStatusBadge state={state} />
            </div>
            <p className="mt-1 truncate font-mono text-xs text-slate-500">{server.directory}</p>
          </div>

          <ServerActions
            serverId={serverId}
            serverName={server.name}
            state={state}
            canStart={hasPermission(me, 'server:start')}
            canStop={hasPermission(me, 'server:stop')}
            canRestart={hasPermission(me, 'server:restart')}
            canKill={hasPermission(me, 'server:kill')}
          />
        </div>

        <dl className="mt-4 flex flex-wrap gap-x-6 gap-y-2 text-xs">
          <div className="flex gap-1.5">
            <dt className="text-slate-500">Type</dt>
            <dd className="text-slate-300">
              {server.server_type}
              {server.minecraft_version ? ` ${server.minecraft_version}` : ''}
            </dd>
          </div>
          <div className="flex gap-1.5">
            <dt className="text-slate-500">Actif depuis</dt>
            <dd className="tabular-nums text-slate-300">{formatUptime(status?.uptime_s ?? 0)}</dd>
          </div>
          <div className="flex gap-1.5">
            <dt className="text-slate-500">Processeur</dt>
            <dd className="tabular-nums text-slate-300">
              {status?.stats ? formatPercent(status.stats.cpu_percent) : '—'}
            </dd>
          </div>
          <div className="flex gap-1.5">
            <dt className="text-slate-500">Mémoire</dt>
            <dd className="tabular-nums text-slate-300">
              {status?.stats && status.stats.memory_mb > 0
                ? formatMemory(status.stats.memory_mb)
                : '—'}
            </dd>
          </div>
          <div className="flex gap-1.5">
            <dt className="text-slate-500">PID</dt>
            <dd className="tabular-nums text-slate-300">{status?.pid ?? '—'}</dd>
          </div>
          <div className="flex gap-1.5">
            <dt className="text-slate-500">Joueurs</dt>
            <dd className="tabular-nums text-slate-300">{status?.players_online ?? 0}</dd>
          </div>
        </dl>

        <nav className="mt-4 flex gap-1 overflow-x-auto">
          {tabs.map((tab) => (
            <NavLink
              key={tab.to || 'overview'}
              to={tab.to}
              end={tab.end}
              className={({ isActive }) =>
                cn(
                  'flex shrink-0 items-center gap-1.5 rounded-t-lg border-b-2 px-3 py-2 text-sm transition-colors',
                  isActive
                    ? 'border-emerald-500 text-slate-100'
                    : 'border-transparent text-slate-400 hover:text-slate-200',
                )
              }
            >
              <tab.icon className="size-4" />
              {tab.label}
            </NavLink>
          ))}
        </nav>
      </div>

      <div className="min-h-0 flex-1 overflow-hidden">
        <Outlet context={{ server, status }} />
      </div>
    </div>
  )
}
