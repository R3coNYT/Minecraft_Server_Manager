/**
 * Affichage d'une erreur d'API sous la forme « Message / Cause / Action ».
 *
 * C'est la contrepartie visible du format d'erreur du backend : l'utilisateur
 * lit ce qui n'a pas fonctionné, pourquoi, et ce qu'il peut faire — au lieu d'un
 * « 500 Internal Server Error » qui ne lui apprend rien.
 */

import { AlertTriangle, Wrench } from 'lucide-react'
import { ApiError } from '@/lib/api'
import { cn } from '@/lib/cn'

interface ErrorPanelProps {
  error: unknown
  className?: string
  compact?: boolean
}

export function ErrorPanel({ error, className, compact = false }: ErrorPanelProps) {
  if (!error) return null

  const apiError = error instanceof ApiError ? error : null
  const message = apiError?.message ?? (error instanceof Error ? error.message : String(error))

  return (
    <div
      className={cn(
        'rounded-lg border border-red-900/60 bg-red-950/40 px-4 py-3 text-sm',
        className,
      )}
      role="alert"
    >
      <div className="flex items-start gap-2.5">
        <AlertTriangle className="mt-0.5 size-4 shrink-0 text-red-400" />
        <div className="min-w-0 flex-1">
          <p className="font-medium text-red-200">{message}</p>

          {!compact && apiError?.cause ? (
            <p className="mt-1.5 text-xs text-red-300/80">
              <span className="font-medium">Cause : </span>
              {apiError.cause}
            </p>
          ) : null}

          {!compact && apiError?.remediation ? (
            <p className="mt-1.5 flex items-start gap-1.5 text-xs text-amber-200/90">
              <Wrench className="mt-0.5 size-3 shrink-0" />
              <span>
                <span className="font-medium">Action : </span>
                {apiError.remediation}
              </span>
            </p>
          ) : null}

          {!compact && apiError?.traceId ? (
            <p className="mt-2 font-mono text-[11px] text-red-400/50">
              trace {apiError.traceId}
            </p>
          ) : null}
        </div>
      </div>
    </div>
  )
}
