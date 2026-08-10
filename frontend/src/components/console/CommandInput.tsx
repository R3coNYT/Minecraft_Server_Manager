/**
 * Saisie de commandes console.
 *
 * L'historique se parcourt aux flèches haut/bas, comme dans un shell : une
 * commande d'administration se rejoue souvent à une variable près.
 *
 * Quand le backend répond 428 (« confirmation requise »), la boîte de dialogue
 * affiche l'explication renvoyée par le serveur puis rejoue la requête avec
 * `confirm: true`. La liste des commandes sensibles n'est donc dupliquée nulle
 * part côté client — c'est le backend qui fait autorité.
 */

import { useRef, useState, type KeyboardEvent } from 'react'
import { ChevronRight, Send } from 'lucide-react'
import { ApiError, api } from '@/lib/api'
import { useToasts } from '@/stores/toasts'
import { Button } from '@/components/ui/Button'
import { ConfirmDialog } from '@/components/common/ConfirmDialog'

interface CommandInputProps {
  serverId: number
  serverName: string
  disabled?: boolean
  disabledReason?: string
}

interface PendingConfirmation {
  command: string
  consequence: string
  strong: boolean
}

const MAX_HISTORY = 100

export function CommandInput({
  serverId,
  serverName,
  disabled = false,
  disabledReason,
}: CommandInputProps) {
  const [value, setValue] = useState('')
  const [history, setHistory] = useState<string[]>([])
  const [historyIndex, setHistoryIndex] = useState<number | null>(null)
  const [sending, setSending] = useState(false)
  const [pending, setPending] = useState<PendingConfirmation | null>(null)
  const [confirmError, setConfirmError] = useState<unknown>(null)
  const inputRef = useRef<HTMLInputElement>(null)
  const pushError = useToasts((state) => state.pushError)

  const remember = (command: string) => {
    setHistory((previous) => [command, ...previous.filter((c) => c !== command)].slice(0, MAX_HISTORY))
    setHistoryIndex(null)
  }

  const send = async (command: string, confirm: boolean) => {
    setSending(true)
    setConfirmError(null)
    try {
      await api.console.send(serverId, command, confirm)
      remember(command)
      setValue('')
      setPending(null)
    } catch (error) {
      if (error instanceof ApiError && error.needsConfirmation) {
        // Le serveur décrit lui-même la conséquence : on l'affiche telle quelle.
        setPending({
          command,
          consequence: error.cause ?? 'Cette commande est sensible.',
          strong: command.trim().toLowerCase().startsWith('stop'),
        })
        return
      }
      if (pending) setConfirmError(error)
      else pushError(error, 'Commande refusée')
    } finally {
      setSending(false)
    }
  }

  const onKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === 'Enter' && value.trim()) {
      event.preventDefault()
      void send(value.trim(), false)
      return
    }

    if (event.key === 'ArrowUp') {
      event.preventDefault()
      if (history.length === 0) return
      const next = historyIndex === null ? 0 : Math.min(historyIndex + 1, history.length - 1)
      setHistoryIndex(next)
      setValue(history[next] ?? '')
      return
    }

    if (event.key === 'ArrowDown') {
      event.preventDefault()
      if (historyIndex === null) return
      const next = historyIndex - 1
      if (next < 0) {
        setHistoryIndex(null)
        setValue('')
      } else {
        setHistoryIndex(next)
        setValue(history[next] ?? '')
      }
    }
  }

  return (
    <>
      <div className="border-t border-slate-800 bg-slate-900/80 px-3 py-2.5">
        <div className="flex items-center gap-2">
          <ChevronRight className="size-4 shrink-0 text-emerald-500" />
          <input
            ref={inputRef}
            value={value}
            onChange={(event) => setValue(event.target.value)}
            onKeyDown={onKeyDown}
            disabled={disabled || sending}
            placeholder={
              disabled
                ? (disabledReason ?? 'Console indisponible')
                : 'Commande (sans le / initial) — ↑ pour rappeler l’historique'
            }
            className="console-line min-w-0 flex-1 bg-transparent text-slate-100 placeholder:text-slate-600 focus:outline-none disabled:cursor-not-allowed"
            spellCheck={false}
            autoComplete="off"
          />
          <Button
            size="sm"
            variant="primary"
            icon={<Send className="size-3.5" />}
            disabled={disabled || !value.trim()}
            loading={sending && !pending}
            onClick={() => value.trim() && void send(value.trim(), false)}
          >
            Envoyer
          </Button>
        </div>
        {disabled && disabledReason ? (
          <p className="mt-1.5 pl-6 text-xs text-amber-400/80">{disabledReason}</p>
        ) : null}
      </div>

      <ConfirmDialog
        open={pending !== null}
        title="Commande sensible"
        description={pending ? `« ${pending.command} » sur ${serverName}` : undefined}
        consequence={pending?.consequence}
        confirmLabel="Exécuter"
        danger
        requireTyping={pending?.strong ? serverName : null}
        loading={sending}
        error={confirmError}
        onConfirm={() => pending && void send(pending.command, true)}
        onClose={() => {
          setPending(null)
          setConfirmError(null)
          inputRef.current?.focus()
        }}
      />
    </>
  )
}
