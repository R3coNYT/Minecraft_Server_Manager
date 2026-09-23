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
import { formatMemory, formatPercent } from '@/lib/format'
import type { MetricRange } from '@/lib/types'
import { Card, CardHeader, LoadingBlock } from '@/components/ui/primitives'
import { ErrorPanel } from '@/components/common/ErrorPanel'
import { ResourceChart } from './ResourceChart'
import { cn } from '@/lib/cn'
import { t, type MessageKey } from '@/i18n'

const RANGES: { key: MetricRange; label: MessageKey }[] = [
  { key: '1h', label: 'metrics.range1h' },
  { key: '6h', label: 'metrics.range6h' },
  { key: '24h', label: 'metrics.range24h' },
  { key: '7d', label: 'metrics.range7d' },
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
        title={t('metrics.title')}
        subtitle={t('metrics.subtitle')}
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
                {t(item.label)}
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
            {/* 100 % désigne **un cœur** saturé, pas la machine entière : la
                mesure est la somme des processus du serveur, et psutil compte
                par cœur. Un serveur Minecraft qui compile ses chunks dépasse
                donc couramment 100 %, et l'échelle doit suivre. */}
            <ResourceChart
              points={points}
              value={(point) => point.cpu_percent}
              floor={100}
              format={formatPercent}
              color="#38bdf8"
              label={t('metrics.cpu')}
              hint={t('metrics.cpuHint')}
            />
            <ResourceChart
              points={points}
              value={(point) => point.memory_mb}
              format={formatMemory}
              color="#a78bfa"
              label={t('metrics.memory')}
              hint={t('metrics.memoryHint')}
            />
            <ResourceChart
              points={points}
              value={(point) => point.players_online}
              // Plancher à 1 : sans lui, un serveur sans joueur donnerait une
              // échelle 0–0 et une ligne au milieu de nulle part.
              floor={1}
              format={(value) => `${Math.round(value)}`}
              color="#34d399"
              label={t('metrics.players')}
            />
          </>
        )}
      </div>
    </Card>
  )
}
