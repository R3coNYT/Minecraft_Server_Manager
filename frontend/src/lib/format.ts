/** Formatage des valeurs affichées : durées, tailles, dates, états. */

import type { ServerState } from './types'

/** « 2 j 4 h », « 12 min », « 45 s ». */
export function formatUptime(seconds: number): string {
  if (!seconds || seconds < 1) return '—'
  const days = Math.floor(seconds / 86400)
  const hours = Math.floor((seconds % 86400) / 3600)
  const minutes = Math.floor((seconds % 3600) / 60)

  if (days > 0) return `${days} j ${hours} h`
  if (hours > 0) return `${hours} h ${minutes} min`
  if (minutes > 0) return `${minutes} min`
  return `${Math.floor(seconds)} s`
}

/** Mébioctets → « 4,2 Go » ou « 512 Mo ». */
export function formatMemory(megabytes: number): string {
  if (!megabytes) return '0 Mo'
  if (megabytes >= 1024) return `${(megabytes / 1024).toFixed(1).replace('.', ',')} Go`
  return `${Math.round(megabytes)} Mo`
}

export function formatBytes(bytes: number): string {
  const units = ['o', 'Ko', 'Mo', 'Go']
  let value = bytes
  let unit = 0
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024
    unit += 1
  }
  return `${value.toFixed(unit === 0 ? 0 : 1).replace('.', ',')} ${units[unit]}`
}

export function formatPercent(value: number): string {
  return `${Math.round(value)} %`
}

const dateFormatter = new Intl.DateTimeFormat('fr-FR', {
  dateStyle: 'short',
  timeStyle: 'medium',
})

export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return '—'
  const date = new Date(iso)
  return Number.isNaN(date.getTime()) ? '—' : dateFormatter.format(date)
}

/** « il y a 3 min » — pour les états qui viennent de changer. */
export function formatRelative(iso: string | null | undefined): string {
  if (!iso) return '—'
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return '—'

  const seconds = Math.floor((Date.now() - date.getTime()) / 1000)
  if (seconds < 10) return "à l'instant"
  if (seconds < 60) return `il y a ${seconds} s`
  if (seconds < 3600) return `il y a ${Math.floor(seconds / 60)} min`
  if (seconds < 86400) return `il y a ${Math.floor(seconds / 3600)} h`
  return formatDateTime(iso)
}

export const STATE_LABELS: Record<ServerState, string> = {
  OFFLINE: 'Arrêté',
  STARTING: 'Démarrage',
  ONLINE: 'En ligne',
  STOPPING: 'Arrêt en cours',
  CRASHED: 'Planté',
  UNKNOWN: 'Indéterminé',
}

/** Classes Tailwind associées à chaque état, pour pastilles et badges. */
export const STATE_STYLES: Record<ServerState, { dot: string; text: string; badge: string }> = {
  OFFLINE: {
    dot: 'bg-slate-500',
    text: 'text-slate-400',
    badge: 'bg-slate-500/10 text-slate-300 ring-slate-500/30',
  },
  STARTING: {
    dot: 'bg-amber-400 animate-pulse',
    text: 'text-amber-300',
    badge: 'bg-amber-500/10 text-amber-300 ring-amber-500/30',
  },
  ONLINE: {
    dot: 'bg-emerald-400',
    text: 'text-emerald-300',
    badge: 'bg-emerald-500/10 text-emerald-300 ring-emerald-500/30',
  },
  STOPPING: {
    dot: 'bg-amber-400 animate-pulse',
    text: 'text-amber-300',
    badge: 'bg-amber-500/10 text-amber-300 ring-amber-500/30',
  },
  CRASHED: {
    dot: 'bg-red-500',
    text: 'text-red-300',
    badge: 'bg-red-500/10 text-red-300 ring-red-500/30',
  },
  UNKNOWN: {
    dot: 'bg-violet-400',
    text: 'text-violet-300',
    badge: 'bg-violet-500/10 text-violet-300 ring-violet-500/30',
  },
}

export const CAPABILITY_LABELS: Record<string, string> = {
  console: 'Console',
  players: 'Joueurs',
  mods: 'Mods',
  plugins: 'Plugins',
  configs: 'Configurations',
  properties: 'server.properties',
  datapacks: 'Datapacks',
  worlds: 'Mondes',
  events: 'Événements',
}

export const AUTO_RESTART_LABELS: Record<string, string> = {
  NEVER: 'Jamais',
  ON_CRASH: 'À chaque plantage',
  ALWAYS: 'Toujours',
}
