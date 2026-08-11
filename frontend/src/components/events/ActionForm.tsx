/**
 * Formulaire d'une action, construit à partir du catalogue du serveur.
 *
 * Aucun champ n'est codé en dur ici : le backend décrit ses actions et leurs
 * champs, l'interface les rend. Ajouter une action côté serveur la rend donc
 * disponible sans toucher au frontend.
 */

import type { ActionField, ActionType } from '@/lib/types'
import { Field, Input, Select } from '@/components/ui/primitives'

interface ActionFormProps {
  action: ActionType
  values: Record<string, unknown>
  onChange: (values: Record<string, unknown>) => void
  disabled?: boolean
}

/** Valeurs initiales d'une action, d'après les défauts qu'elle déclare. */
export function defaultParams(action: ActionType): Record<string, unknown> {
  const params: Record<string, unknown> = {}
  for (const field of action.fields) {
    if (field.default !== null && field.default !== undefined) params[field.name] = field.default
  }
  return params
}

function FieldInput({
  field,
  value,
  disabled,
  onChange,
}: {
  field: ActionField
  value: unknown
  disabled: boolean
  onChange: (value: unknown) => void
}) {
  // Le type « target » est une saisie libre : un sélecteur Minecraft peut être
  // un pseudo comme une expression complexe (`@a[distance=..5]`).
  if (field.type === 'number') {
    return (
      <Input
        type="number"
        value={value === undefined || value === null ? '' : String(value)}
        disabled={disabled}
        {...(field.minimum !== null ? { min: field.minimum } : {})}
        {...(field.maximum !== null ? { max: field.maximum } : {})}
        placeholder={field.placeholder}
        onChange={(event) =>
          onChange(event.target.value === '' ? '' : Number(event.target.value))
        }
      />
    )
  }

  return (
    <Input
      value={value === undefined || value === null ? '' : String(value)}
      disabled={disabled}
      placeholder={field.placeholder}
      spellCheck={field.type !== 'target'}
      onChange={(event) => onChange(event.target.value)}
    />
  )
}

export function ActionForm({ action, values, onChange, disabled = false }: ActionFormProps) {
  return (
    <div className="grid gap-3 sm:grid-cols-2">
      {action.fields.map((field) => (
        <div key={field.name} className={field.type === 'text' ? 'sm:col-span-2' : ''}>
          <Field
            label={field.label + (field.required ? '' : ' (facultatif)')}
            hint={field.help}
          >
            <FieldInput
              field={field}
              value={values[field.name]}
              disabled={disabled}
              onChange={(value) => onChange({ ...values, [field.name]: value })}
            />
          </Field>
        </div>
      ))}
    </div>
  )
}

export function ActionSelect({
  actions,
  value,
  onChange,
  disabled = false,
}: {
  actions: ActionType[]
  value: string
  onChange: (key: string) => void
  disabled?: boolean
}) {
  return (
    <Select value={value} disabled={disabled} onChange={(event) => onChange(event.target.value)}>
      {actions.map((action) => (
        <option key={action.key} value={action.key}>
          {action.label}
          {action.danger !== 'SAFE' ? ' — irréversible' : ''}
        </option>
      ))}
    </Select>
  )
}
