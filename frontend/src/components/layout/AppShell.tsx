/** Ossature de l'application : barre latérale, en-tête, zone de contenu. */

import { useState } from 'react'
import { NavLink, Outlet, useNavigate } from 'react-router-dom'
import {
  Boxes,
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

function ConnectionIndicator() {
  const connection = useRealtime((state) => state.connection)
  const attempts = useRealtime((state) => state.reconnectAttempts)

  if (connection === 'open') {
    return (
      <span className="flex items-center gap-1.5 text-xs text-emerald-400" title="Flux temps réel actif">
        <Wifi className="size-3.5" />
        Temps réel
      </span>
    )
  }

  return (
    <span
      className="flex items-center gap-1.5 text-xs text-amber-400"
      title="Le flux temps réel est interrompu ; reconnexion automatique en cours"
    >
      <WifiOff className="size-3.5" />
      {connection === 'connecting' ? 'Connexion…' : `Reconnexion (${attempts})`}
    </span>
  )
}

function SidebarContent({ onNavigate }: { onNavigate?: () => void }) {
  const { data: me } = useMe()
  const { data: servers } = useServers()
  const statuses = useRealtime((state) => state.statuses)

  const linkClass = ({ isActive }: { isActive: boolean }) =>
    cn(
      'flex items-center gap-2.5 rounded-lg px-3 py-2 text-sm transition-colors',
      isActive ? 'bg-slate-800 text-slate-100' : 'text-slate-400 hover:bg-slate-800/60 hover:text-slate-200',
    )

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center gap-2.5 px-4 py-4">
        <Boxes className="size-5 text-emerald-500" />
        <div className="min-w-0">
          <p className="truncate text-sm font-semibold text-slate-100">Minecraft</p>
          <p className="truncate text-xs text-slate-500">Server Manager</p>
        </div>
      </div>

      <nav className="flex flex-col gap-1 px-2">
        <NavLink to="/" end className={linkClass} onClick={onNavigate}>
          <LayoutDashboard className="size-4" />
          Tableau de bord
        </NavLink>
        {hasPermission(me, 'audit:view') ? (
          <NavLink to="/audit" className={linkClass} onClick={onNavigate}>
            <ScrollText className="size-4" />
            Journal d'audit
          </NavLink>
        ) : null}
        {hasPermission(me, 'user:manage') ? (
          <NavLink to="/users" className={linkClass} onClick={onNavigate}>
            <Users className="size-4" />
            Utilisateurs
          </NavLink>
        ) : null}
        {hasPermission(me, 'settings:manage') ? (
          <NavLink to="/settings" className={linkClass} onClick={onNavigate}>
            <SlidersHorizontal className="size-4" />
            Réglages
          </NavLink>
        ) : null}
      </nav>

      <div className="mt-6 px-4 text-xs font-medium uppercase tracking-wide text-slate-600">
        Serveurs
      </div>
      <nav className="mt-2 flex min-h-0 flex-1 flex-col gap-0.5 overflow-y-auto px-2 pb-4">
        {(servers ?? []).map((server) => {
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
        {servers?.length === 0 ? (
          <p className="px-3 py-2 text-xs text-slate-600">Aucun serveur enregistré.</p>
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
            aria-label="Menu"
          >
            {mobileOpen ? <X className="size-5" /> : <Menu className="size-5" />}
          </button>

          <ServerIcon className="size-4 text-slate-600 lg:hidden" />
          <div className="flex-1" />

          <ConnectionIndicator />

          <div className="hidden items-center gap-2 border-l border-slate-800 pl-3 sm:flex">
            <div className="text-right">
              <p className="text-xs font-medium text-slate-200">{me?.username}</p>
              <p className="text-[11px] text-slate-500">{me?.role}</p>
            </div>
          </div>

          <Button
            size="sm"
            variant="ghost"
            icon={<LogOut className="size-4" />}
            onClick={() => void onLogout()}
            loading={logout.isPending}
            title="Se déconnecter"
          >
            <span className="sr-only">Se déconnecter</span>
          </Button>
        </header>

        <main className="min-h-0 flex-1 overflow-y-auto">
          <Outlet />
        </main>
      </div>
    </div>
  )
}
