/**
 * Compte à rebours jusqu'à la prochaine synchronisation avec le launcher.
 *
 * Il avance chaque seconde sans interroger l'API ; arrivé à zéro, il demande
 * une relecture, pour afficher le résultat puis repartir sur l'échéance
 * suivante. La synchronisation elle-même est décidée par le serveur, à la
 * granularité de sa boucle : « imminente » couvre ces quelques secondes.
 */

import { useEffect, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { queryKeys } from '@/hooks/useApi'
import type { LauncherLink } from '@/lib/types'

function remaining(iso: string | null, now: number): number | null {
  if (!iso) return null
  const target = new Date(iso).getTime()
  return Number.isNaN(target) ? null : Math.round((target - now) / 1000)
}

export function formatCountdown(seconds: number): string {
  if (seconds <= 0) return 'imminente'
  const hours = Math.floor(seconds / 3600)
  const minutes = Math.floor((seconds % 3600) / 60)
  const secs = seconds % 60
  if (hours > 0) return `dans ${hours} h ${String(minutes).padStart(2, '0')} min`
  if (minutes > 0) return `dans ${minutes} min ${String(secs).padStart(2, '0')} s`
  return `dans ${secs} s`
}

export function SyncCountdown({
  serverId,
  link,
}: {
  serverId: number
  link: Pick<LauncherLink, 'enabled' | 'next_sync_at'>
}) {
  const queryClient = useQueryClient()
  const [now, setNow] = useState(() => Date.now())

  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [])

  const seconds = remaining(link.next_sync_at, now)

  // Échéance passée : relire toutes les 5 s jusqu'à ce que la boucle de fond
  // ait fait son travail et fixé la suivante.
  const overdue = seconds !== null && seconds <= 0
  const bucket = overdue ? Math.floor(-seconds / 5) : null
  useEffect(() => {
    if (bucket !== null) {
      void queryClient.invalidateQueries({ queryKey: queryKeys.launcherLink(serverId) })
    }
  }, [bucket, queryClient, serverId])

  if (!link.enabled) return <span className="text-slate-500">désactivée</span>
  if (seconds === null) return <span className="text-slate-500">—</span>
  return <span className="tabular-nums">{formatCountdown(seconds)}</span>
}
