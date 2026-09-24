/**
 * Ossature d'une page serveur : en-tête, onglets, contenu.
 *
 * Les onglets sont construits à partir des **capacités réellement détectées**
 * dans le dossier : un serveur Vanilla n'affiche pas d'onglet Mods, un Mohist
 * affiche Mods *et* Plugins. L'interface reflète le disque, pas une famille
 * déclarée à la création.
 */

import { NavLink, Outlet, useParams } from 'react-router-dom'
import {
  Archive,
  Bell,
  CalendarClock,
  FileCog,
  Link2,
  Package,
  Puzzle,
  Settings,
  Settings2,
  Terminal,
  Timer,
  Users,
} from 'lucide-react'
import { can, hasPermission, useLauncherLink, useMe, useServer, useServerStatus } from '@/hooks/useApi'
import { useServerSubscription } from '@/hooks/useServerSubscription'
import { formatUptime, formatMemory, formatPercent } from '@/lib/format'
import { cn } from '@/lib/cn'
import { LoadingBlock } from '@/components/ui/primitives'
import { ErrorPanel } from '@/components/common/ErrorPanel'
import { ServerStatusBadge } from '@/components/servers/ServerStatusBadge'
import { ServerActions } from '@/components/servers/ServerActions'
import { SyncCountdown } from '@/components/launcher/SyncCountdown'
import { t, type MessageKey } from '@/i18n'

interface TabDefinition {
  to: string
  label: MessageKey
  icon: typeof Terminal
  capability?: string
  /** Onglet masqué sans cette permission : son contenu serait refusé. */
  permission?: string
  end?: boolean
}

const TABS: TabDefinition[] = [
  { to: '', label: 'tab.overview', icon: Settings, end: true },
  { to: 'console', label: 'tab.console', icon: Terminal, capability: 'console', permission: 'console:read' },
  { to: 'players', label: 'tab.players', icon: Users, capability: 'players', permission: 'player:view' },
  { to: 'mods', label: 'tab.mods', icon: Package, capability: 'mods', permission: 'file:read' },
  { to: 'plugins', label: 'tab.plugins', icon: Puzzle, capability: 'plugins', permission: 'file:read' },
  { to: 'properties', label: 'tab.properties', icon: Settings2, capability: 'properties', permission: 'config:read' },
  { to: 'configs', label: 'tab.configs', icon: FileCog, capability: 'configs', permission: 'config:read' },
  { to: 'events', label: 'tab.events', icon: CalendarClock, capability: 'events', permission: 'event:run' },
  // Pas de capacité conditionnelle : tout serveur se sauvegarde, y compris celui
  // qui n'a pas encore de monde — c'est justement le moment d'y penser.
  { to: 'backups', label: 'tab.backups', icon: Archive, permission: 'backup:create' },
  { to: 'schedules', label: 'tab.schedules', icon: Timer, permission: 'server:edit' },
  { to: 'launcher', label: 'tab.launcher', icon: Link2, permission: 'server:edit' },
  { to: 'notifications', label: 'tab.notifications', icon: Bell, permission: 'server:edit' },
]

export function ServerLayout() {
  const params = useParams<{ serverId: string }>()
  const serverId = Number(params.serverId)
  const { data: server, isLoading, error } = useServer(serverId)
  const { data: me } = useMe()
  const status = useServerStatus(serverId, server?.status)
  const { data: launcherLink } = useLauncherLink(serverId, can(server, 'server:edit'))

  useServerSubscription(serverId)

  if (isLoading) return <LoadingBlock />
  if (error) return <div className="p-6"><ErrorPanel error={error} /></div>
  if (!server) return null

  const state = status?.state ?? 'UNKNOWN'
  const capabilities = new Set(server.capabilities)
  const tabs = TABS.filter(
    (tab) =>
      (!tab.capability || capabilities.has(tab.capability)) &&
      (!tab.permission || hasPermission(me, tab.permission)),
  )

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
            canStart={can(server, 'server:start')}
            canStop={can(server, 'server:stop')}
            canRestart={can(server, 'server:restart')}
            canKill={can(server, 'server:kill')}
          />
        </div>

        <dl className="mt-4 flex flex-wrap gap-x-6 gap-y-2 text-xs">
          <div className="flex gap-1.5">
            <dt className="text-slate-500">{t('serverHeader.type')}</dt>
            <dd className="text-slate-300">
              {server.server_type}
              {server.minecraft_version ? ` ${server.minecraft_version}` : ''}
            </dd>
          </div>
          <div className="flex gap-1.5">
            <dt className="text-slate-500">{t('serverHeader.uptime')}</dt>
            <dd className="tabular-nums text-slate-300">{formatUptime(status?.uptime_s ?? 0)}</dd>
          </div>
          <div className="flex gap-1.5">
            <dt className="text-slate-500">{t('serverHeader.cpu')}</dt>
            <dd className="tabular-nums text-slate-300">
              {status?.stats ? formatPercent(status.stats.cpu_percent) : '—'}
            </dd>
          </div>
          <div className="flex gap-1.5">
            <dt className="text-slate-500">{t('serverHeader.memory')}</dt>
            <dd className="tabular-nums text-slate-300">
              {status?.stats && status.stats.memory_mb > 0
                ? formatMemory(status.stats.memory_mb)
                : '—'}
            </dd>
          </div>
          <div className="flex gap-1.5">
            <dt className="text-slate-500">{t('serverHeader.pid')}</dt>
            <dd className="tabular-nums text-slate-300">{status?.pid ?? '—'}</dd>
          </div>
          <div className="flex gap-1.5">
            <dt className="text-slate-500">{t('serverHeader.players')}</dt>
            <dd className="tabular-nums text-slate-300">{status?.players_online ?? 0}</dd>
          </div>
          {launcherLink ? (
            <div className="flex gap-1.5">
              <dt className="text-slate-500">{t('serverHeader.launcherSync')}</dt>
              <dd className="text-slate-300">
                <SyncCountdown serverId={serverId} link={launcherLink} />
                {launcherLink.pending ? (
                  <span className="ml-1.5 text-sky-300">{t('serverHeader.pendingChanges')}</span>
                ) : null}
              </dd>
            </div>
          ) : null}
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
              {t(tab.label)}
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
