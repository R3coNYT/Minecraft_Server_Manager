/** Formatage des valeurs affichées : durées, tailles, dates, états. */

import { locale, t, type MessageKey } from '@/i18n'
import type { ServerState } from './types'

/** « 2 d 4 h », « 12 min », « 45 s ». */
export function formatUptime(seconds: number): string {
  if (!seconds || seconds < 1) return '—'
  const days = Math.floor(seconds / 86400)
  const hours = Math.floor((seconds % 86400) / 3600)
  const minutes = Math.floor((seconds % 3600) / 60)

  if (days > 0) return t('format.days', { days, hours })
  if (hours > 0) return t('format.hours', { hours, minutes })
  if (minutes > 0) return t('format.minutes', { minutes })
  return t('format.seconds', { seconds: Math.floor(seconds) })
}

/** Nombre décimal selon la langue : « 4.2 » ou « 4,2 ». */
function decimal(value: number, digits: number): string {
  return value.toLocaleString(locale(), {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })
}

/** Mébioctets → « 4.2 GB » ou « 512 MB ». */
export function formatMemory(megabytes: number): string {
  if (!megabytes) return `0 ${t('format.unitMB')}`
  if (megabytes >= 1024) return `${decimal(megabytes / 1024, 1)} ${t('format.unitGB')}`
  return `${Math.round(megabytes)} ${t('format.unitMB')}`
}

const BYTE_UNITS: MessageKey[] = ['format.unitB', 'format.unitKB', 'format.unitMB', 'format.unitGB']

export function formatBytes(bytes: number): string {
  let value = bytes
  let unit = 0
  while (value >= 1024 && unit < BYTE_UNITS.length - 1) {
    value /= 1024
    unit += 1
  }
  return `${decimal(value, unit === 0 ? 0 : 1)} ${t(BYTE_UNITS[unit] ?? 'format.unitB')}`
}

export function formatPercent(value: number): string {
  return t('format.percent', { value: Math.round(value) })
}

export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return '—'
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return '—'
  return new Intl.DateTimeFormat(locale(), { dateStyle: 'short', timeStyle: 'medium' }).format(
    date,
  )
}

/**
 * « 3 min ago », « in 4 h » — pour les instants proches, passés ou à venir.
 *
 * Le futur compte autant que le passé depuis qu'il y a des tâches programmées :
 * une prochaine exécution affichée « à l'instant » ferait croire à un
 * déclenchement imminent qui n'arrivera que cette nuit.
 */
export function formatRelative(iso: string | null | undefined): string {
  if (!iso) return '—'
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return '—'

  const seconds = Math.floor((Date.now() - date.getTime()) / 1000)
  const magnitude = Math.abs(seconds)
  if (magnitude < 10) return t('format.justNow')
  if (magnitude >= 86400) return formatDateTime(iso)

  const value =
    magnitude < 60
      ? t('format.seconds', { seconds: magnitude })
      : magnitude < 3600
        ? t('format.minutes', { minutes: Math.floor(magnitude / 60) })
        : `${Math.floor(magnitude / 3600)} h`
  return seconds >= 0 ? t('format.ago', { value }) : t('format.in', { value })
}

export function stateLabel(state: ServerState): string {
  return t(`state.${state}` as MessageKey)
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

export function capabilityLabel(capability: string): string {
  const key = `capability.${capability}` as MessageKey
  const label = t(key)
  return label === key ? capability : label
}

export function autoRestartLabel(mode: string): string {
  const key = `autoRestart.${mode}` as MessageKey
  const label = t(key)
  return label === key ? mode : label
}
