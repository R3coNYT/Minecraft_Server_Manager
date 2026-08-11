/**
 * Réglages globaux du panneau — aujourd'hui les notifications Discord.
 *
 * L'adresse du webhook n'est jamais réaffichée : elle permet à qui la détient
 * d'écrire dans le salon. L'interface montre qu'elle est enregistrée et ses
 * derniers caractères, de quoi la reconnaître sans pouvoir la réutiliser.
 */

import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, Bell, Send, Trash2 } from 'lucide-react'
import { api } from '@/lib/api'
import { useToasts } from '@/stores/toasts'
import { Card, CardHeader, Checkbox, Field, Input, LoadingBlock } from '@/components/ui/primitives'
import { Button } from '@/components/ui/Button'
import { ErrorPanel } from '@/components/common/ErrorPanel'

export function SettingsPage() {
  const queryClient = useQueryClient()
  const push = useToasts((state) => state.push)
  const pushError = useToasts((state) => state.pushError)

  const [webhook, setWebhook] = useState('')
  const [selected, setSelected] = useState<string[] | null>(null)

  const settings = useQuery({ queryKey: ['notifications'], queryFn: () => api.notifications.get() })
  const catalogue = useQuery({
    queryKey: ['notification-events'],
    queryFn: () => api.notifications.events(),
    staleTime: Infinity,
  })

  // Les cases suivent le serveur tant que l'utilisateur n'y a pas touché.
  useEffect(() => {
    if (settings.data && selected === null) setSelected(settings.data.events)
  }, [settings.data, selected])

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ['notifications'] })

  const update = useMutation({
    mutationFn: (payload: Record<string, unknown>) => api.notifications.update(payload),
    onSuccess: (result) => {
      setWebhook('')
      setSelected(result.events)
      void invalidate()
    },
    onError: (error) => pushError(error),
  })

  const test = useMutation({
    mutationFn: () => api.notifications.test(),
    onSuccess: (result) =>
      push(
        result.sent
          ? { kind: 'success', title: 'Message envoyé', detail: 'Vérifier le salon Discord.' }
          : {
              kind: 'error',
              title: "Discord n'a pas accepté le message",
              detail: "Vérifier que le webhook existe toujours dans les réglages du salon.",
            },
      ),
    onError: (error) => pushError(error),
  })

  if (settings.isLoading) return <LoadingBlock />

  const current = settings.data
  const events = catalogue.data ?? []
  const checked = selected ?? current?.events ?? []

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-3xl space-y-5 p-4 sm:p-6">
        <ErrorPanel error={settings.error} />

        <Card>
          <CardHeader
            title="Notifications Discord"
            subtitle="Être prévenu quand quelque chose se passe mal, sans regarder le panneau."
          />

          <div className="space-y-5 px-5 py-4">
            {current?.webhook_unreadable ? (
              <div className="flex items-start gap-2.5 rounded-lg border border-amber-900/60 bg-amber-950/30 px-3.5 py-3">
                <AlertTriangle className="mt-0.5 size-4 shrink-0 text-amber-400" />
                <p className="text-sm text-amber-100/90">
                  L'adresse enregistrée n'est plus déchiffrable : la clé secrète de MSM a changé.
                  Les notifications sont inactives tant qu'elle n'est pas ressaisie.
                </p>
              </div>
            ) : null}

            <Field
              label="Adresse du webhook"
              hint={
                current?.webhook_configured
                  ? `Enregistrée (${current.webhook_hint}). Saisir une nouvelle adresse pour la remplacer.`
                  : 'Discord → Paramètres du salon → Intégrations → Webhooks → Copier l’URL.'
              }
            >
              <div className="flex gap-2">
                <Input
                  value={webhook}
                  onChange={(event) => setWebhook(event.target.value)}
                  placeholder="https://discord.com/api/webhooks/…"
                  type="password"
                  autoComplete="off"
                />
                <Button
                  variant="secondary"
                  disabled={!webhook.trim()}
                  loading={update.isPending}
                  onClick={() => update.mutate({ webhook_url: webhook })}
                >
                  Enregistrer
                </Button>
              </div>
            </Field>

            <div className="flex flex-wrap items-center gap-2">
              <Button
                variant={current?.enabled ? 'secondary' : 'primary'}
                icon={<Bell className="size-4" />}
                disabled={!current?.webhook_configured}
                loading={update.isPending}
                onClick={() => update.mutate({ enabled: !current?.enabled })}
              >
                {current?.enabled ? 'Désactiver les notifications' : 'Activer les notifications'}
              </Button>
              <Button
                variant="ghost"
                icon={<Send className="size-4" />}
                disabled={!current?.webhook_configured}
                loading={test.isPending}
                onClick={() => test.mutate()}
              >
                Envoyer un test
              </Button>
              {current?.webhook_configured ? (
                <Button
                  variant="ghost"
                  icon={<Trash2 className="size-4" />}
                  onClick={() => update.mutate({ clear_webhook: true })}
                >
                  Retirer l'adresse
                </Button>
              ) : null}
            </div>

            <div>
              <p className="mb-2 text-xs font-medium text-slate-300">Événements notifiés</p>
              <div className="space-y-2">
                {events.map((event) => (
                  <Checkbox
                    key={event.key}
                    label={event.label}
                    checked={checked.includes(event.key)}
                    onChange={(change) =>
                      setSelected(
                        change.target.checked
                          ? [...checked, event.key]
                          : checked.filter((item) => item !== event.key),
                      )
                    }
                  />
                ))}
              </div>
              <Button
                className="mt-3"
                size="sm"
                variant="secondary"
                loading={update.isPending}
                onClick={() => update.mutate({ events: checked })}
              >
                Enregistrer la sélection
              </Button>
            </div>

            <ErrorPanel error={update.error} />
          </div>
        </Card>
      </div>
    </div>
  )
}
