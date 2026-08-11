/**
 * Confirmation d'une action sensible.
 *
 * Deux niveaux, calqués sur la classification du backend :
 *
 * * **simple** — un bouton de confirmation suffit ;
 * * **forte** — l'utilisateur doit ressaisir le nom du serveur. Une action
 *   irréversible ne doit pas pouvoir partir d'un double-clic malheureux.
 */

import { useEffect, useState } from 'react'
import { AlertTriangle } from 'lucide-react'
import { Dialog } from '@/components/ui/Dialog'
import { Button } from '@/components/ui/Button'
import { Input } from '@/components/ui/primitives'
import { ErrorPanel } from './ErrorPanel'

interface ConfirmDialogProps {
  open: boolean
  title: string
  description?: string
  /** Ce que l'action va provoquer, en clair. */
  consequence?: string | null
  confirmLabel?: string
  danger?: boolean
  /** Si fourni, l'utilisateur doit ressaisir exactement ce texte. */
  requireTyping?: string | null
  loading?: boolean
  error?: unknown
  onConfirm: () => void
  onClose: () => void
}

export function ConfirmDialog({
  open,
  title,
  description,
  consequence,
  confirmLabel = 'Confirmer',
  danger = false,
  requireTyping = null,
  loading = false,
  error,
  onConfirm,
  onClose,
}: ConfirmDialogProps) {
  const [typed, setTyped] = useState('')

  useEffect(() => {
    if (open) setTyped('')
  }, [open])

  const canConfirm = requireTyping === null || typed.trim() === requireTyping

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={title}
      description={description}
      size="sm"
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={loading}>
            Annuler
          </Button>
          <Button
            variant={danger ? 'danger' : 'primary'}
            onClick={onConfirm}
            loading={loading}
            disabled={!canConfirm}
          >
            {confirmLabel}
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        {consequence ? (
          <div className="flex items-start gap-2.5 rounded-lg border border-amber-900/60 bg-amber-950/30 px-3.5 py-3">
            <AlertTriangle className="mt-0.5 size-4 shrink-0 text-amber-400" />
            <p className="text-sm text-amber-100/90">{consequence}</p>
          </div>
        ) : null}

        {requireTyping !== null ? (
          <div>
            <p className="mb-2 text-sm text-slate-300">
              Pour confirmer, saisir{' '}
              <code className="rounded bg-slate-800 px-1.5 py-0.5 font-mono text-xs text-slate-100">
                {requireTyping}
              </code>
            </p>
            <Input
              value={typed}
              onChange={(event) => setTyped(event.target.value)}
              placeholder={requireTyping}
              autoFocus
            />
          </div>
        ) : null}

        <ErrorPanel error={error} />
      </div>
    </Dialog>
  )
}
