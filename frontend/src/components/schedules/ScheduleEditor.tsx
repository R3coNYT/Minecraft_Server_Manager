/**
 * Création et modification d'une tâche programmée.
 *
 * Le formulaire dit la règle en français au fur et à mesure — « Chaque jeudi à
 * 03:30 » — parce qu'une planification mal comprise ne se découvre qu'au moment
 * où elle se déclenche, souvent la nuit.
 *
 * Le fuseau est celui du navigateur par défaut : celui qui programme une
 * sauvegarde « à 4 h » pense à 4 h chez lui, pas à 4 h UTC.
 */

import { useState } from 'react'
import type { GameEvent, ScheduleAction, ScheduleRule, TriggerKind } from '@/lib/types'
import { Dialog } from '@/components/ui/Dialog'
import { Button } from '@/components/ui/Button'
import { Field, Input, Select } from '@/components/ui/primitives'
import { ErrorPanel } from '@/components/common/ErrorPanel'
import { cn } from '@/lib/cn'

const ACTION_LABELS: Record<ScheduleAction, string> = {
  BACKUP: 'Sauvegarder',
  RESTART: 'Redémarrer',
  START: 'Démarrer',
  STOP: 'Arrêter',
  EVENT: 'Déclencher un événement',
  COMMAND: 'Envoyer une commande',
}

const TRIGGER_LABELS: Record<TriggerKind, string> = {
  INTERVAL: 'À intervalle régulier',
  DAILY: 'Chaque jour',
  WEEKLY: 'Certains jours',
}

const DAYS = ['lun', 'mar', 'mer', 'jeu', 'ven', 'sam', 'dim']

/** Fuseau du navigateur, ou UTC si l'environnement ne le dit pas. */
function browserTimezone(): string {
  return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'
}

export interface ScheduleDraft {
  name: string
  action: ScheduleAction
  rule: ScheduleRule
  payload: Record<string, unknown>
  enabled: boolean
}

export function emptyDraft(): ScheduleDraft {
  return {
    name: '',
    action: 'BACKUP',
    rule: { trigger: 'DAILY', hour: 4, minute: 0, timezone: browserTimezone() },
    payload: {},
    enabled: true,
  }
}

interface ScheduleEditorProps {
  open: boolean
  initial?: ScheduleDraft
  events: GameEvent[]
  /** Modification : l'action ne change plus, sa permission a déjà été accordée. */
  locked?: boolean
  saving?: boolean
  error?: unknown
  onSave: (draft: ScheduleDraft) => void
  onClose: () => void
}

export function ScheduleEditor({
  open,
  initial,
  events,
  locked = false,
  saving = false,
  error,
  onSave,
  onClose,
}: ScheduleEditorProps) {
  const [draft, setDraft] = useState<ScheduleDraft>(initial ?? emptyDraft())
  const rule = draft.rule

  const setRule = (patch: Partial<ScheduleRule>) =>
    setDraft({ ...draft, rule: { ...rule, ...patch } })

  const toggleDay = (day: number) => {
    const days = new Set(rule.days ?? [])
    if (days.has(day)) days.delete(day)
    else days.add(day)
    setRule({ days: [...days].sort() })
  }

  const incomplete =
    !draft.name.trim() ||
    (draft.action === 'EVENT' && !draft.payload.event_id) ||
    (draft.action === 'COMMAND' && !String(draft.payload.command ?? '').trim()) ||
    (rule.trigger === 'WEEKLY' && (rule.days ?? []).length === 0)

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={initial ? `Modifier « ${initial.name} »` : 'Nouvelle tâche programmée'}
      description="La tâche s'exécutera avec vos droits, réévalués à chaque déclenchement."
      size="md"
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Annuler
          </Button>
          <Button
            variant="primary"
            loading={saving}
            disabled={incomplete}
            onClick={() => onSave(draft)}
          >
            Enregistrer
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Nom">
            <Input
              value={draft.name}
              onChange={(event) => setDraft({ ...draft, name: event.target.value })}
              placeholder="Sauvegarde nocturne"
              autoFocus
            />
          </Field>
          <Field label="Action">
            <Select
              value={draft.action}
              disabled={locked}
              onChange={(event) =>
                setDraft({
                  ...draft,
                  action: event.target.value as ScheduleAction,
                  payload: {},
                })
              }
            >
              {(Object.keys(ACTION_LABELS) as ScheduleAction[]).map((action) => (
                <option key={action} value={action}>
                  {ACTION_LABELS[action]}
                </option>
              ))}
            </Select>
          </Field>
        </div>

        {draft.action === 'EVENT' ? (
          <Field
            label="Événement"
            hint={events.length === 0 ? "Aucun événement enregistré sur ce serveur." : undefined}
          >
            <Select
              value={String(draft.payload.event_id ?? '')}
              onChange={(event) =>
                setDraft({ ...draft, payload: { event_id: Number(event.target.value) } })
              }
            >
              <option value="">— Choisir —</option>
              {events.map((event) => (
                <option key={event.id} value={event.id}>
                  {event.name}
                </option>
              ))}
            </Select>
          </Field>
        ) : null}

        {draft.action === 'COMMAND' ? (
          <Field label="Commande" hint="Sans le « / » initial.">
            <Input
              value={String(draft.payload.command ?? '')}
              onChange={(event) =>
                setDraft({ ...draft, payload: { command: event.target.value } })
              }
              placeholder="say Redémarrage dans 5 minutes"
            />
          </Field>
        ) : null}

        <Field label="Quand">
          <Select
            value={rule.trigger}
            onChange={(event) => {
              const trigger = event.target.value as TriggerKind
              setRule({
                trigger,
                ...(trigger === 'INTERVAL' ? { interval_minutes: rule.interval_minutes ?? 360 } : {}),
                ...(trigger !== 'INTERVAL' ? { hour: rule.hour ?? 4, minute: rule.minute ?? 0 } : {}),
                ...(trigger === 'WEEKLY' ? { days: rule.days ?? [0] } : {}),
              })
            }}
          >
            {(Object.keys(TRIGGER_LABELS) as TriggerKind[]).map((trigger) => (
              <option key={trigger} value={trigger}>
                {TRIGGER_LABELS[trigger]}
              </option>
            ))}
          </Select>
        </Field>

        {rule.trigger === 'INTERVAL' ? (
          <Field label="Intervalle (minutes)" hint="5 minutes au minimum.">
            <Input
              type="number"
              min={5}
              value={rule.interval_minutes ?? 360}
              onChange={(event) => setRule({ interval_minutes: Number(event.target.value) })}
            />
          </Field>
        ) : (
          <div className="grid gap-3 sm:grid-cols-3">
            <Field label="Heure">
              <Input
                type="number"
                min={0}
                max={23}
                value={rule.hour ?? 4}
                onChange={(event) => setRule({ hour: Number(event.target.value) })}
              />
            </Field>
            <Field label="Minute">
              <Input
                type="number"
                min={0}
                max={59}
                value={rule.minute ?? 0}
                onChange={(event) => setRule({ minute: Number(event.target.value) })}
              />
            </Field>
            <Field label="Fuseau">
              <Input
                value={rule.timezone}
                onChange={(event) => setRule({ timezone: event.target.value })}
              />
            </Field>
          </div>
        )}

        {rule.trigger === 'WEEKLY' ? (
          <div>
            <p className="mb-1.5 text-xs font-medium text-slate-400">Jours</p>
            <div className="flex flex-wrap gap-1.5">
              {DAYS.map((label, index) => (
                <button
                  key={label}
                  onClick={() => toggleDay(index)}
                  className={cn(
                    'rounded-lg px-2.5 py-1 text-xs capitalize transition-colors',
                    (rule.days ?? []).includes(index)
                      ? 'bg-sky-600 text-white'
                      : 'bg-slate-800 text-slate-400 hover:text-slate-200',
                  )}
                >
                  {label}
                </button>
              ))}
            </div>
          </div>
        ) : null}

        <ErrorPanel error={error} />
      </div>
    </Dialog>
  )
}

export { ACTION_LABELS }
