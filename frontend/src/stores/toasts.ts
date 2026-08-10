/** File de notifications éphémères. */

import { create } from 'zustand'
import { ApiError } from '@/lib/api'

export type ToastKind = 'success' | 'error' | 'info' | 'warning'

export interface Toast {
  id: number
  kind: ToastKind
  title: string
  detail?: string
}

interface ToastState {
  toasts: Toast[]
  push: (toast: Omit<Toast, 'id'>) => void
  dismiss: (id: number) => void
  /** Raccourci : transforme une erreur d'API en notification lisible. */
  pushError: (error: unknown, fallback?: string) => void
}

let nextId = 1
const AUTO_DISMISS_MS = 6000

export const useToasts = create<ToastState>((set, get) => ({
  toasts: [],

  push: (toast) => {
    const id = nextId++
    set((state) => ({ toasts: [...state.toasts, { ...toast, id }] }))
    // Les erreurs restent affichées jusqu'à lecture : elles portent souvent une
    // action corrective qu'il serait dommage de faire disparaître trop vite.
    if (toast.kind !== 'error') {
      setTimeout(() => get().dismiss(id), AUTO_DISMISS_MS)
    }
  },

  dismiss: (id) => set((state) => ({ toasts: state.toasts.filter((t) => t.id !== id) })),

  pushError: (error, fallback = 'Action impossible') => {
    const apiError = error instanceof ApiError ? error : null
    get().push({
      kind: 'error',
      title: apiError?.message ?? (error instanceof Error ? error.message : fallback),
      detail: apiError?.remediation ?? apiError?.cause,
    })
  },
}))
