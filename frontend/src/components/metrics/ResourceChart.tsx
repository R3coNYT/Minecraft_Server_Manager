/**
 * Courbe d'une série de métriques, dessinée en SVG.
 *
 * Écrit à la main plutôt qu'avec une bibliothèque de graphiques : celles-ci
 * pèsent plusieurs centaines de kilooctets pour un besoin qui tient en une
 * polyligne et deux axes. Le panneau est servi depuis la machine de
 * l'utilisateur, souvent la même que celle qui fait tourner les serveurs.
 *
 * Le graphique est **responsive sans JavaScript** : le `viewBox` fait le travail,
 * il n'y a donc ni observateur de redimensionnement ni recalcul au défilement.
 */

import { useId } from 'react'
import type { MetricPoint } from '@/lib/types'

const WIDTH = 600
const HEIGHT = 140
const PADDING = { top: 8, right: 4, bottom: 18, left: 4 }

interface ResourceChartProps {
  points: MetricPoint[]
  /** Valeur à tracer pour un point donné. */
  value: (point: MetricPoint) => number
  /** Maximum de l'axe vertical ; sinon calculé sur les données. */
  max?: number
  format: (value: number) => string
  color: string
  label: string
}

function niceMax(value: number): number {
  if (value <= 0) return 1
  const magnitude = 10 ** Math.floor(Math.log10(value))
  return Math.ceil(value / magnitude) * magnitude
}

export function ResourceChart({
  points,
  value,
  max,
  format,
  color,
  label,
}: ResourceChartProps) {
  const gradientId = useId()

  if (points.length === 0) {
    return (
      <div className="flex h-[140px] items-center justify-center rounded-lg border border-dashed border-slate-800 text-xs text-slate-600">
        Aucune mesure sur cette période.
      </div>
    )
  }

  const values = points.map(value)
  const ceiling = max ?? niceMax(Math.max(...values))
  const innerWidth = WIDTH - PADDING.left - PADDING.right
  const innerHeight = HEIGHT - PADDING.top - PADDING.bottom

  // Un point unique n'a pas d'écart : il est placé au centre plutôt que de
  // provoquer une division par zéro.
  const step = points.length > 1 ? innerWidth / (points.length - 1) : 0
  const x = (index: number) =>
    PADDING.left + (points.length > 1 ? index * step : innerWidth / 2)
  const y = (raw: number) => PADDING.top + innerHeight * (1 - Math.min(raw, ceiling) / ceiling)

  const line = values.map((raw, index) => `${x(index)},${y(raw)}`).join(' ')
  const area = `${PADDING.left},${PADDING.top + innerHeight} ${line} ${x(values.length - 1)},${PADDING.top + innerHeight}`

  const first = points[0]!
  const last = points[points.length - 1]!
  const hour = (iso: string) =>
    new Date(iso).toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit' })

  return (
    <figure className="m-0">
      <figcaption className="mb-1 flex items-baseline justify-between text-xs">
        <span className="text-slate-400">{label}</span>
        <span className="tabular-nums text-slate-500">
          max {format(Math.max(...values))}
        </span>
      </figcaption>

      <svg
        viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
        className="h-[140px] w-full"
        preserveAspectRatio="none"
        role="img"
        aria-label={`${label} : maximum ${format(Math.max(...values))}`}
      >
        <defs>
          <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={color} stopOpacity="0.35" />
            <stop offset="100%" stopColor={color} stopOpacity="0" />
          </linearGradient>
        </defs>

        {[0, 0.5, 1].map((ratio) => (
          <line
            key={ratio}
            x1={PADDING.left}
            x2={WIDTH - PADDING.right}
            y1={PADDING.top + innerHeight * ratio}
            y2={PADDING.top + innerHeight * ratio}
            stroke="currentColor"
            strokeWidth="1"
            className="text-slate-800"
            vectorEffect="non-scaling-stroke"
          />
        ))}

        <polygon points={area} fill={`url(#${gradientId})`} />
        <polyline
          points={line}
          fill="none"
          stroke={color}
          strokeWidth="1.5"
          strokeLinejoin="round"
          strokeLinecap="round"
          vectorEffect="non-scaling-stroke"
        />
        {/* Le dernier point est marqué : il situe la valeur courante, et rend
            visible une série qui n'a encore qu'une seule mesure.

            Un segment de longueur nulle à bout rond plutôt qu'un cercle : le
            `viewBox` est étiré horizontalement, ce qui transformerait un disque
            en ovale — l'épaisseur d'un trait, elle, ne se déforme pas. */}
        <line
          x1={x(values.length - 1)}
          y1={y(values[values.length - 1]!)}
          x2={x(values.length - 1)}
          y2={y(values[values.length - 1]!)}
          stroke={color}
          strokeWidth="5"
          strokeLinecap="round"
          vectorEffect="non-scaling-stroke"
        />
      </svg>

      <div className="flex justify-between text-[11px] tabular-nums text-slate-600">
        <span>{hour(first.ts)}</span>
        <span>{format(ceiling)} max</span>
        <span>{hour(last.ts)}</span>
      </div>
    </figure>
  )
}
