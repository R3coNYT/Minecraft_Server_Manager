/**
 * Interface dédiée à `server.properties`.
 *
 * Les clés connues deviennent des champs typés — case à cocher, liste, nombre
 * borné — plutôt qu'une saisie libre où `max-players=beaucoup` passerait sans
 * broncher. Les clés inconnues, y compris celles d'une version future de
 * Minecraft, restent modifiables en texte : le panneau ne doit pas empêcher de
 * régler ce qu'il ne connaît pas encore.
 *
 * Seules les valeurs réellement changées sont envoyées, et le fichier conserve
 * ses commentaires et son ordre d'origine.
 */

import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { RotateCw, Save, Settings2 } from 'lucide-react'
import { api } from '@/lib/api'
import { hasPermission, useMe } from '@/hooks/useApi'
import { useToasts } from '@/stores/toasts'
import type { ServerProperty } from '@/lib/types'
import { useServerContext } from './context'
import { Card, CardHeader, EmptyState, Input, LoadingBlock, Select } from '@/components/ui/primitives'
import { Button } from '@/components/ui/Button'
import { ErrorPanel } from '@/components/common/ErrorPanel'

function PropertyField({
  property,
  value,
  disabled,
  onChange,
}: {
  property: ServerProperty
  value: string
  disabled: boolean
  onChange: (value: string) => void
}) {
  if (property.type === 'boolean') {
    return (
      <input
        type="checkbox"
        className="size-4 rounded border-slate-600 bg-slate-900 text-emerald-600"
        checked={value === 'true'}
        disabled={disabled}
        onChange={(event) => onChange(event.target.checked ? 'true' : 'false')}
      />
    )
  }

  if (property.type === 'enum') {
    return (
      <Select
        className="h-8 w-48 py-0 text-xs"
        value={value}
        disabled={disabled}
        onChange={(event) => onChange(event.target.value)}
      >
        {property.choices.map((choice) => (
          <option key={choice} value={choice}>
            {choice}
          </option>
        ))}
      </Select>
    )
  }

  return (
    <Input
      className="h-8 w-48 py-0 text-xs"
      type={property.type === 'integer' ? 'number' : 'text'}
      value={value}
      disabled={disabled}
      {...(property.minimum !== null ? { min: property.minimum } : {})}
      {...(property.maximum !== null ? { max: property.maximum } : {})}
      onChange={(event) => onChange(event.target.value)}
    />
  )
}

export function PropertiesPage() {
  const { server, status } = useServerContext()
  const queryClient = useQueryClient()
  const { data: me } = useMe()
  const push = useToasts((state) => state.push)

  const [changes, setChanges] = useState<Record<string, string>>({})
  const canWrite = hasPermission(me, 'properties:write')

  const { data, isLoading, error } = useQuery({
    queryKey: ['properties', server.id],
    queryFn: () => api.properties.read(server.id),
  })

  const save = useMutation({
    mutationFn: () => api.properties.update(server.id, changes),
    onSuccess: (result) => {
      push({
        kind: result.requires_restart ? 'warning' : 'success',
        title: `${result.updated.length} réglage(s) enregistré(s)`,
        detail: result.requires_restart
          ? 'Un redémarrage du serveur est nécessaire pour appliquer ces changements.'
          : undefined,
      })
      setChanges({})
      void queryClient.invalidateQueries({ queryKey: ['properties', server.id] })
    },
  })

  const { known, unknown } = useMemo(() => {
    const entries = data?.entries ?? []
    return {
      known: entries.filter((entry) => entry.known),
      unknown: entries.filter((entry) => !entry.known),
    }
  }, [data])

  const pendingRestart = useMemo(
    () =>
      (data?.entries ?? []).some(
        (entry) => entry.key in changes && entry.requires_restart,
      ),
    [data, changes],
  )

  if (isLoading) return <LoadingBlock />

  if (!data?.exists) {
    return (
      <div className="h-full overflow-y-auto p-4 sm:p-6">
        <Card>
          <EmptyState
            icon={<Settings2 className="size-8" />}
            title="server.properties absent"
            description="Le serveur ne l'a pas encore généré. Le démarrer une première fois créera ses fichiers de configuration."
          />
        </Card>
      </div>
    )
  }

  const rows = (entries: ServerProperty[]) =>
    entries.map((entry) => {
      const current = changes[entry.key] ?? entry.value
      const modified = entry.key in changes && changes[entry.key] !== entry.value
      return (
        <tr key={entry.key} className={modified ? 'bg-emerald-950/20' : ''}>
          <td className="px-5 py-2.5">
            <p className="text-slate-200">{entry.label}</p>
            <p className="font-mono text-[11px] text-slate-600">{entry.key}</p>
            {entry.help ? (
              <p className="mt-0.5 max-w-md text-[11px] text-slate-500">{entry.help}</p>
            ) : null}
          </td>
          <td className="px-5 py-2.5">
            <PropertyField
              property={entry}
              value={current}
              disabled={!canWrite}
              onChange={(value) => setChanges({ ...changes, [entry.key]: value })}
            />
          </td>
          <td className="px-5 py-2.5 text-[11px] text-slate-600">
            {entry.requires_restart ? (
              <span className="inline-flex items-center gap-1">
                <RotateCw className="size-3" />
                au redémarrage
              </span>
            ) : (
              'immédiat'
            )}
          </td>
        </tr>
      )
    })

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-4xl space-y-4 p-4 sm:p-6">
        <ErrorPanel error={error} />
        <ErrorPanel error={save.error} />

        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h2 className="text-sm font-semibold text-slate-100">Réglages du serveur</h2>
            <p className="text-xs text-slate-500">
              Les commentaires et l'ordre du fichier sont préservés à l'enregistrement.
            </p>
          </div>
          <Button
            variant="primary"
            icon={<Save className="size-4" />}
            disabled={!canWrite || Object.keys(changes).length === 0}
            loading={save.isPending}
            onClick={() => save.mutate()}
          >
            Enregistrer {Object.keys(changes).length > 0 ? `(${Object.keys(changes).length})` : ''}
          </Button>
        </div>

        {pendingRestart && status?.state === 'ONLINE' ? (
          <div className="rounded-lg border border-amber-900/60 bg-amber-950/30 px-4 py-3 text-sm text-amber-100">
            Certains réglages modifiés ne prendront effet qu'après un redémarrage du serveur.
          </div>
        ) : null}

        <Card>
          <CardHeader title="Réglages courants" />
          <table className="w-full text-sm">
            <tbody className="divide-y divide-slate-800/60">{rows(known)}</tbody>
          </table>
        </Card>

        {unknown.length > 0 ? (
          <Card>
            <CardHeader
              title={`Autres réglages (${unknown.length})`}
              subtitle="Clés non répertoriées par MSM : modifiables en texte libre."
            />
            <table className="w-full text-sm">
              <tbody className="divide-y divide-slate-800/60">{rows(unknown)}</tbody>
            </table>
          </Card>
        ) : null}
      </div>
    </div>
  )
}
