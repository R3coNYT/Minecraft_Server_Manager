/**
 * Réglages d'un salon Discord : le salon global comme celui d'un serveur.
 *
 * L'adresse du webhook n'est jamais réaffichée : elle permet à qui la détient
 * d'écrire dans le salon. L'interface montre qu'elle est enregistrée et ses
 * derniers caractères, de quoi la reconnaître sans pouvoir la réutiliser.
 *
 * Les événements proposés viennent du serveur avec les réglages : chaque salon
 * n'offre que ceux qui le concernent.
 */

import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, Bell, Send, Trash2 } from 'lucide-react'
import type { NotificationSettings } from '@/lib/types'
import { useToasts } from '@/stores/toasts'
import { Card, CardHeader, Checkbox, Field, Input, LoadingBlock } from '@/components/ui/primitives'
import { Button } from '@/components/ui/Button'
import { ErrorPanel } from '@/components/common/ErrorPanel'
import { t } from '@/i18n'

interface NotificationSettingsCardProps {
  title: string
  subtitle: string
  queryKey: readonly unknown[]
  load: () => Promise<NotificationSettings>
  save: (payload: Record<string, unknown>) => Promise<NotificationSettings>
  sendTest: () => Promise<{ sent: boolean }>
}

export function NotificationSettingsCard({
  title,
  subtitle,
  queryKey,
  load,
  save,
  sendTest,
}: NotificationSettingsCardProps) {
  const queryClient = useQueryClient()
  const push = useToasts((state) => state.push)
  const pushError = useToasts((state) => state.pushError)

  const [webhook, setWebhook] = useState('')
  const [selected, setSelected] = useState<string[] | null>(null)

  const settings = useQuery({ queryKey, queryFn: load })

  // Les cases suivent le serveur tant que l'utilisateur n'y a pas touché.
  useEffect(() => {
    if (settings.data && selected === null) setSelected(settings.data.events)
  }, [settings.data, selected])

  const update = useMutation({
    mutationFn: save,
    onSuccess: (result) => {
      setWebhook('')
      setSelected(result.events)
      queryClient.setQueryData(queryKey, result)
    },
    onError: (error) => pushError(error),
  })

  const test = useMutation({
    mutationFn: sendTest,
    onSuccess: (result) =>
      push(
        result.sent
          ? {
              kind: 'success',
              title: t('notifications.testSent'),
              detail: t('notifications.testSentDetail'),
            }
          : {
              kind: 'error',
              title: t('notifications.testRefused'),
              detail: t('notifications.testRefusedDetail'),
            },
      ),
    onError: (error) => pushError(error),
  })

  const current = settings.data
  const checked = selected ?? current?.events ?? []

  return (
    <Card>
      <CardHeader title={title} subtitle={subtitle} />

      {settings.isLoading ? (
        <LoadingBlock />
      ) : (
        <div className="space-y-5 px-5 py-4">
          <ErrorPanel error={settings.error} />

          {current?.webhook_unreadable ? (
            <div className="flex items-start gap-2.5 rounded-lg border border-amber-900/60 bg-amber-950/30 px-3.5 py-3">
              <AlertTriangle className="mt-0.5 size-4 shrink-0 text-amber-400" />
              <p className="text-sm text-amber-100/90">{t('notifications.unreadable')}</p>
            </div>
          ) : null}

          <Field
            label={t('notifications.webhook')}
            hint={
              current?.webhook_configured
                ? t('notifications.webhookSaved', { hint: current.webhook_hint ?? '' })
                : t('notifications.webhookHelp')
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
                {t('common.save')}
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
              {current?.enabled ? t('notifications.disable') : t('notifications.enable')}
            </Button>
            <Button
              variant="ghost"
              icon={<Send className="size-4" />}
              disabled={!current?.webhook_configured}
              loading={test.isPending}
              onClick={() => test.mutate()}
            >
              {t('notifications.sendTest')}
            </Button>
            {current?.webhook_configured ? (
              <Button
                variant="ghost"
                icon={<Trash2 className="size-4" />}
                onClick={() => update.mutate({ clear_webhook: true })}
              >
                {t('notifications.removeAddress')}
              </Button>
            ) : null}
          </div>

          <div>
            <p className="mb-2 text-xs font-medium text-slate-300">{t('notifications.events')}</p>
            <div className="space-y-2">
              {(current?.available_events ?? []).map((event) => (
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
              {t('notifications.saveSelection')}
            </Button>
          </div>

          <ErrorPanel error={update.error} />
        </div>
      )}
    </Card>
  )
}
