/** Ossature de l'application : barre latérale, en-tête, zone de contenu. */

import { useState } from 'react'
import { Link, NavLink, Outlet, useNavigate } from 'react-router-dom'
import {
  LayoutDashboard,
  LogOut,
  Menu,
  ScrollText,
  Server as ServerIcon,
  SlidersHorizontal,
  Users,
  Wifi,
  WifiOff,
  X,
} from 'lucide-react'
import { hasPermission, useLogout, useMe, useServers } from '@/hooks/useApi'
import { useRealtime } from '@/stores/realtime'
import { cn } from '@/lib/cn'
import { StatusDot } from '@/components/servers/ServerStatusBadge'
import { Button } from '@/components/ui/Button'
import { MsmLogo } from '@/components/brand/MsmLogo'
import { Avatar } from '@/components/common/Avatar'
import { groupServers } from '@/lib/serverGroups'
import { t, type MessageKey } from '@/i18n'

function ConnectionIndicator() {
  const connection = useRealtime((state) => state.connection)
  const attempts = useRealtime((state) => state.reconnectAttempts)

  if (connection === 'open') {
    return (
      <span className="flex items-center gap-1.5 text-xs text-emerald-400" title={t('shell.realtimeActive')}>
        <Wifi className="size-3.5" />
        {t('shell.realtime')}
      </span>
    )
  }

  return (
    <span
      className="flex items-center gap-1.5 text-xs text-amber-400"
      title={t('shell.realtimeDown')}
    >
      <WifiOff className="size-3.5" />
      {connection === 'connecting' ? t('shell.connecting') : t('shell.reconnecting', { attempts })}
    </span>
  )
}

function SidebarContent({ onNavigate }: { onNavigate?: () => void }) {
  const { data: me } = useMe()
  const { data: servers } = useServers()
  const statuses = useRealtime((state) => state.statuses)
  const groups = groupServers(servers ?? [], me?.id)

  const linkClass = ({ isActive }: { isActive: boolean }) =>
    cn(
      'flex items-center gap-2.5 rounded-lg px-3 py-2 text-sm transition-colors',
      isActive ? 'bg-slate-800 text-slate-100' : 'text-slate-400 hover:bg-slate-800/60 hover:text-slate-200',
    )

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center gap-2.5 px-4 py-4">
        <MsmLogo className="size-7 shrink-0 text-emerald-500" />
        <div className="min-w-0">
          <p className="truncate text-sm font-semibold text-slate-100">Minecraft</p>
          <p className="truncate text-xs text-slate-500">Server Manager</p>
        </div>
      </div>

      <nav className="flex flex-col gap-1 px-2">
        <NavLink to="/" end className={linkClass} onClick={onNavigate}>
          <LayoutDashboard className="size-4" />
          {t('shell.dashboard')}
        </NavLink>
        {hasPermission(me, 'audit:view') ? (
          <NavLink to="/audit" className={linkClass} onClick={onNavigate}>
            <ScrollText className="size-4" />
            {t('shell.audit')}
          </NavLink>
        ) : null}
        {hasPermission(me, 'user:view') ? (
          <NavLink to="/users" className={linkClass} onClick={onNavigate}>
            <Users className="size-4" />
            {t('shell.users')}
          </NavLink>
        ) : null}
        {/* Tout le monde a des réglages : au moins sa langue. */}
        <NavLink to="/settings" className={linkClass} onClick={onNavigate}>
          <SlidersHorizontal className="size-4" />
          {t('shell.settings')}
        </NavLink>
      </nav>

      <div className="mt-6 px-4 text-xs font-medium uppercase tracking-wide text-slate-600">
        {t('shell.servers')}
      </div>
      <nav className="mt-2 flex min-h-0 flex-1 flex-col gap-0.5 overflow-y-auto px-2 pb-4">
        {groups.map((group) => (
          <div key={group.key} className="flex flex-col gap-0.5">
            {groups.length > 1 ? (
              <p className="truncate px-3 pb-0.5 pt-2 text-[11px] text-slate-600">{group.title}</p>
            ) : null}
            {group.servers.map((server) => {
              const state = statuses[server.id]?.state ?? server.status?.state ?? 'UNKNOWN'
              return (
                <NavLink
                  key={server.id}
                  to={`/servers/${server.id}`}
                  className={linkClass}
                  onClick={onNavigate}
                >
                  <StatusDot state={state} />
                  <span className="truncate">{server.name}</span>
                </NavLink>
              )
            })}
          </div>
        ))}
        {servers?.length === 0 ? (
          <p className="px-3 py-2 text-xs text-slate-600">{t('shell.noServers')}</p>
        ) : null}
      </nav>
    </div>
  )
}

export function AppShell() {
  const { data: me } = useMe()
  const logout = useLogout()
  const navigate = useNavigate()
  const [mobileOpen, setMobileOpen] = useState(false)

  const onLogout = async () => {
    await logout.mutateAsync().catch(() => undefined)
    navigate('/login', { replace: true })
  }

  return (
    <div className="flex h-screen overflow-hidden bg-slate-950">
      <aside className="hidden w-60 shrink-0 border-r border-slate-800 bg-slate-900/40 lg:block">
        <SidebarContent />
      </aside>

      {mobileOpen ? (
        <div className="fixed inset-0 z-40 lg:hidden">
          <div
            className="absolute inset-0 bg-slate-950/80"
            onClick={() => setMobileOpen(false)}
            aria-hidden
          />
          <aside className="absolute inset-y-0 left-0 w-64 border-r border-slate-800 bg-slate-900">
            <SidebarContent onNavigate={() => setMobileOpen(false)} />
          </aside>
        </div>
      ) : null}

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-14 shrink-0 items-center gap-3 border-b border-slate-800 bg-slate-900/40 px-4">
          <button
            className="rounded-lg p-1.5 text-slate-400 hover:bg-slate-800 hover:text-slate-200 lg:hidden"
            onClick={() => setMobileOpen((open) => !open)}
            aria-label={t('shell.menu')}
          >
            {mobileOpen ? <X className="size-5" /> : <Menu className="size-5" />}
          </button>

          <ServerIcon className="size-4 text-slate-600 lg:hidden" />
          <div className="flex-1" />

          <ConnectionIndicator />

          {me ? (
            <Link
              to="/profile"
              className="flex items-center gap-2 rounded-lg border-l border-slate-800 py-1 pl-3 pr-1 hover:bg-slate-800/60"
              title={t('shell.profile')}
            >
              <div className="hidden text-right sm:block">
                <p className="text-xs font-medium text-slate-200">{me.username}</p>
                <p className="text-[11px] text-slate-500">{t(`role.${me.role}` as MessageKey)}</p>
              </div>
              <Avatar url={me.avatar_url} name={me.username} />
            </Link>
          ) : null}

          <Button
            size="sm"
            variant="ghost"
            icon={<LogOut className="size-4" />}
            onClick={() => void onLogout()}
            loading={logout.isPending}
            title={t('shell.signOut')}
          >
            <span className="sr-only">{t('shell.signOut')}</span>
          </Button>
        </header>

        <main className="min-h-0 flex-1 overflow-y-auto">
          <Outlet />
        </main>
      </div>
    </div>
  )
}
