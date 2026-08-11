import type { ServerState } from '@/lib/types'
import { STATE_LABELS, STATE_STYLES } from '@/lib/format'
import { cn } from '@/lib/cn'

export function StatusDot({ state, className }: { state: ServerState; className?: string }) {
  return <span className={cn('inline-block size-2 rounded-full', STATE_STYLES[state].dot, className)} />
}

export function ServerStatusBadge({ state }: { state: ServerState }) {
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-medium ring-1 ring-inset',
        STATE_STYLES[state].badge,
      )}
    >
      <StatusDot state={state} />
      {STATE_LABELS[state]}
    </span>
  )
}
