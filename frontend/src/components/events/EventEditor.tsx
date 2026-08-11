/**
 * Éditeur de séquence d'événement.
 *
 * Une séquence est une suite ordonnée d'étapes : l'ordre porte tout le sens
 * — annoncer avant de distribuer, attendre avant de conclure — d'où les
 * commandes de déplacement plutôt qu'une simple liste.
 *
 * La validation reste côté serveur : il refuse une séquence invalide en
 * indiquant l'étape fautive, plutôt que de dupliquer ici des règles qui
 * divergeraient tôt ou tard.
 */

import { useState } from 'react'
import { ArrowDown, ArrowUp, Plus, Trash2 } from 'lucide-react'
import type { ActionType } from '@/lib/types'
import { Dialog } from '@/components/ui/Dialog'
import { Button } from '@/components/ui/Button'
import { Badge, Field, Input } from '@/components/ui/primitives'
import { ErrorPanel } from '@/components/common/ErrorPanel'
import { ActionForm, ActionSelect, defaultParams } from './ActionForm'
import { cn } from '@/lib/cn'

export interface DraftStep {
  action: string
  params: Record<string, unknown>
}

interface EventEditorProps {
  open: boolean
  actions: ActionType[]
  initialName?: string
  initialDescription?: string
  initialSteps?: DraftStep[]
  saving?: boolean
  error?: unknown
  onSave: (payload: { name: string; description: string; steps: DraftStep[] }) => void
  onClose: () => void
}

export function EventEditor({
  open,
  actions,
  initialName = '',
  initialDescription = '',
  initialSteps = [],
  saving = false,
  error,
  onSave,
  onClose,
}: EventEditorProps) {
  const [name, setName] = useState(initialName)
  const [description, setDescription] = useState(initialDescription)
  const [steps, setSteps] = useState<DraftStep[]>(initialSteps)
  const [adding, setAdding] = useState<string>(actions[0]?.key ?? 'say')
  const [draft, setDraft] = useState<Record<string, unknown>>({})

  const byKey = new Map(actions.map((action) => [action.key, action]))
  const addingAction = byKey.get(adding)

  const addStep = () => {
    if (!addingAction) return
    setSteps([...steps, { action: adding, params: { ...defaultParams(addingAction), ...draft } }])
    setDraft({})
  }

  const move = (index: number, delta: number) => {
    const target = index + delta
    if (target < 0 || target >= steps.length) return
    const next = [...steps]
    const [moved] = next.splice(index, 1)
    next.splice(target, 0, moved!)
    setSteps(next)
  }

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={initialName ? `Modifier « ${initialName} »` : 'Nouvel événement'}
      description="Les étapes s'exécutent dans l'ordre, de haut en bas."
      size="lg"
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Annuler
          </Button>
          <Button
            variant="primary"
            loading={saving}
            disabled={!name.trim() || steps.length === 0}
            onClick={() => onSave({ name: name.trim(), description, steps })}
          >
            Enregistrer
          </Button>
        </>
      }
    >
      <div className="space-y-5">
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Nom">
            <Input
              value={name}
              onChange={(event) => setName(event.target.value)}
              placeholder="Tournoi hebdomadaire"
              autoFocus
            />
          </Field>
          <Field label="Description (facultatif)">
            <Input
              value={description}
              onChange={(event) => setDescription(event.target.value)}
              placeholder="Ouverture du tournoi du dimanche"
            />
          </Field>
        </div>

        <div>
          <p className="mb-2 text-xs font-medium text-slate-300">
            Étapes {steps.length > 0 ? `(${steps.length})` : ''}
          </p>

          {steps.length === 0 ? (
            <p className="rounded-lg border border-dashed border-slate-700 px-4 py-6 text-center text-xs text-slate-600">
              Aucune étape. En ajouter une ci-dessous.
            </p>
          ) : (
            <ol className="space-y-1.5">
              {steps.map((step, index) => {
                const action = byKey.get(step.action)
                const destructive = action?.danger !== 'SAFE'
                return (
                  <li
                    key={`${step.action}-${index}`}
                    className={cn(
                      'flex items-center gap-2 rounded-lg border px-3 py-2 text-sm',
                      destructive
                        ? 'border-red-900/60 bg-red-950/20'
                        : 'border-slate-800 bg-slate-900/60',
                    )}
                  >
                    <span className="w-5 shrink-0 text-center text-xs tabular-nums text-slate-600">
                      {index + 1}
                    </span>
                    <span className="min-w-0 flex-1 truncate text-slate-200">
                      {action?.label ?? step.action}
                      <span className="ml-2 font-mono text-[11px] text-slate-500">
                        {Object.entries(step.params)
                          .filter(([, value]) => value !== '' && value !== undefined)
                          .map(([key, value]) => `${key}=${String(value)}`)
                          .join(' ')}
                      </span>
                    </span>
                    {destructive ? (
                      <Badge className="bg-red-500/10 text-red-300 ring-red-500/30">
                        irréversible
                      </Badge>
                    ) : null}
                    <button
                      className="rounded p-1 text-slate-500 hover:text-slate-200 disabled:opacity-30"
                      disabled={index === 0}
                      onClick={() => move(index, -1)}
                      aria-label="Monter"
                    >
                      <ArrowUp className="size-3.5" />
                    </button>
                    <button
                      className="rounded p-1 text-slate-500 hover:text-slate-200 disabled:opacity-30"
                      disabled={index === steps.length - 1}
                      onClick={() => move(index, 1)}
                      aria-label="Descendre"
                    >
                      <ArrowDown className="size-3.5" />
                    </button>
                    <button
                      className="rounded p-1 text-slate-500 hover:text-red-300"
                      onClick={() => setSteps(steps.filter((_, i) => i !== index))}
                      aria-label="Retirer"
                    >
                      <Trash2 className="size-3.5" />
                    </button>
                  </li>
                )
              })}
            </ol>
          )}
        </div>

        <div className="rounded-lg border border-slate-800 bg-slate-900/40 p-4">
          <p className="mb-3 text-xs font-medium text-slate-300">Ajouter une étape</p>
          <div className="space-y-3">
            <ActionSelect
              actions={actions}
              value={adding}
              onChange={(key) => {
                setAdding(key)
                setDraft({})
              }}
            />
            {addingAction ? (
              <>
                <p className="text-xs text-slate-500">{addingAction.description}</p>
                <ActionForm
                  action={addingAction}
                  values={{ ...defaultParams(addingAction), ...draft }}
                  onChange={setDraft}
                />
              </>
            ) : null}
            <Button
              size="sm"
              variant="secondary"
              icon={<Plus className="size-3.5" />}
              onClick={addStep}
            >
              Ajouter l'étape
            </Button>
          </div>
        </div>

        <ErrorPanel error={error} />
      </div>
    </Dialog>
  )
}
