import { AlertTriangle, CheckCircle2, Info, X, XCircle } from 'lucide-react'
import { useToasts, type ToastKind } from '@/stores/toasts'
import { cn } from '@/lib/cn'

const STYLES: Record<ToastKind, { box: string; icon: JSX.Element }> = {
  success: {
    box: 'border-emerald-900/60 bg-emerald-950/70',
    icon: <CheckCircle2 className="size-4 text-emerald-400" />,
  },
  error: {
    box: 'border-red-900/60 bg-red-950/70',
    icon: <XCircle className="size-4 text-red-400" />,
  },
  warning: {
    box: 'border-amber-900/60 bg-amber-950/70',
    icon: <AlertTriangle className="size-4 text-amber-400" />,
  },
  info: {
    box: 'border-slate-700 bg-slate-900/90',
    icon: <Info className="size-4 text-slate-400" />,
  },
}

export function Toaster() {
  const toasts = useToasts((state) => state.toasts)
  const dismiss = useToasts((state) => state.dismiss)

  if (toasts.length === 0) return null

  return (
    <div className="pointer-events-none fixed bottom-4 right-4 z-50 flex w-full max-w-sm flex-col gap-2">
      {toasts.map((toast) => (
        <div
          key={toast.id}
          className={cn(
            'pointer-events-auto flex items-start gap-2.5 rounded-lg border px-3.5 py-3 shadow-lg backdrop-blur',
            STYLES[toast.kind].box,
          )}
          role="status"
        >
          <span className="mt-0.5 shrink-0">{STYLES[toast.kind].icon}</span>
          <div className="min-w-0 flex-1">
            <p className="text-sm font-medium text-slate-100">{toast.title}</p>
            {toast.detail ? (
              <p className="mt-0.5 text-xs text-slate-400">{toast.detail}</p>
            ) : null}
          </div>
          <button
            onClick={() => dismiss(toast.id)}
            className="rounded p-0.5 text-slate-500 transition-colors hover:text-slate-200"
            aria-label="Fermer la notification"
          >
            <X className="size-3.5" />
          </button>
        </div>
      ))}
    </div>
  )
}
