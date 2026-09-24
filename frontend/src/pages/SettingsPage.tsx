/**
 * Réglages globaux du panneau : la langue, et le salon Discord global — qui
 * n'annonce que la création et la suppression de serveurs. Chaque serveur a
 * son propre salon, réglé depuis son onglet Notifications.
 */

import { useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { useToasts } from '@/stores/toasts'
import { Card, CardHeader, Field, Select } from '@/components/ui/primitives'
import { NotificationSettingsCard } from '@/components/notifications/NotificationSettingsCard'
import { t, useLanguage, type Language } from '@/i18n'
import { UI_SETTINGS_KEY } from '@/components/common/LanguageBoundary'

const LANGUAGE_NAMES: Record<Language, string> = { en: 'English', fr: 'Français' }

/** Langue du panneau : un réglage global, partagé par tous les utilisateurs. */
function LanguageCard() {
  const queryClient = useQueryClient()
  const pushError = useToasts((state) => state.pushError)
  const language = useLanguage((state) => state.language)

  const change = useMutation({
    mutationFn: (value: Language) => api.ui.setLanguage(value),
    onSuccess: (result) => {
      queryClient.setQueryData(UI_SETTINGS_KEY, result)
      // Les textes produits par le serveur (erreurs, résumés, libellés) changent
      // aussi de langue : tout recharger plutôt que de les trier.
      void queryClient.invalidateQueries()
      useLanguage.getState().setLanguage(result.language)
    },
    onError: (error) => pushError(error),
  })

  return (
    <Card>
      <CardHeader title={t('settings.languageTitle')} subtitle={t('settings.languageSubtitle')} />
      <div className="px-5 py-4">
        <Field label={t('settings.language')}>
          <Select
            value={language}
            disabled={change.isPending}
            onChange={(event) => change.mutate(event.target.value as Language)}
          >
            {(Object.keys(LANGUAGE_NAMES) as Language[]).map((value) => (
              <option key={value} value={value}>
                {LANGUAGE_NAMES[value]}
              </option>
            ))}
          </Select>
        </Field>
      </div>
    </Card>
  )
}

export function SettingsPage() {
  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-3xl space-y-5 p-4 sm:p-6">
        <LanguageCard />
        <NotificationSettingsCard
          title={t('settings.discordTitle')}
          subtitle={t('settings.discordSubtitle')}
          queryKey={['notifications']}
          load={() => api.notifications.get()}
          save={(payload) => api.notifications.update(payload)}
          sendTest={() => api.notifications.test()}
        />
      </div>
    </div>
  )
}
