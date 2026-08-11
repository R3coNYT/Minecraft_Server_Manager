/**
 * Historique des ressources d'un serveur.
 *
 * Les statistiques temps réel disent « combien maintenant » ; ce panneau répond
 * à « pourquoi ça ramait cette nuit ». Il lit l'historique persistant, pas le
 * WebSocket : recharger la page ne doit pas effacer la mémoire du passé.
 */

import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { formatMemory } from '@/lib/format'
import type { MetricRange } from '@/lib/types'
import { Card, CardHeader, LoadingBlock } from '@/components/ui/primitives'
import { ErrorPanel } from '@/components/common/ErrorPanel'
import { ResourceChart } from './ResourceChart'
import { cn } from '@/lib/cn'

const RANGES: { key: MetricRange; label: string }[] = [
  { key: '1h', label: '1 h' },
  { key: '6h', label: '6 h' },
  { key: '24h', label: '24 h' },
  { key: '7d', label: '7 j' },
]

export function ResourcePanel({ serverId }: { serverId: number }) {
  const [range, setRange] = useState<MetricRange>('24h')

  const history = useQuery({
    queryKey: ['metrics', serverId, range],
    queryFn: () => api.metrics.history(serverId, range),
    // L'échantillonnage est à 30 s : rafraîchir plus souvent ne montrerait rien
    // de nouveau.
    refetchInterval: 30_000,
  })

  const points = history.data?.points ?? []

  return (
    <Card>
      <CardHeader
        title="Ressources"
        subtitle="Historique conservé, indépendant de la page ouverte."
        action={
          <div className="flex gap-1 rounded-lg bg-slate-900 p-0.5">
            {RANGES.map((item) => (
              <button
                key={item.key}
                onClick={() => setRange(item.key)}
                className={cn(
                  'rounded-md px-2.5 py-1 text-xs transition-colors',
                  range === item.key
                    ? 'bg-slate-700 text-slate-100'
                    : 'text-slate-400 hover:text-slate-200',
                )}
              >
                {item.label}
              </button>
            ))}
          </div>
        }
      />

      <div className="space-y-5 px-5 py-4">
        <ErrorPanel error={history.error} />
        {history.isLoading ? (
          <LoadingBlock />
        ) : (
          <>
            <ResourceChart
              points={points}
              value={(point) => point.cpu_percent}
              max={100}
              format={(value) => `${Math.round(value)} %`}
              color="#38bdf8"
              label="Processeur"
            />
            <ResourceChart
              points={points}
              value={(point) => point.memory_mb}
              format={formatMemory}
              color="#a78bfa"
              label="Mémoire"
            />
            <ResourceChart
              points={points}
              value={(point) => point.players_online}
              format={(value) => `${Math.round(value)}`}
              color="#34d399"
              label="Joueurs connectés"
            />
          </>
        )}
      </div>
    </Card>
  )
}
