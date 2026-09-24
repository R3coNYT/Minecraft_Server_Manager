/**
 * Salon Discord propre à ce serveur : ses plantages, démarrages, sauvegardes…
 * Chaque serveur peut annoncer dans un salon différent.
 */

import { api } from '@/lib/api'
import { useServerContext } from './context'
import { NotificationSettingsCard } from '@/components/notifications/NotificationSettingsCard'
import { t } from '@/i18n'

export function NotificationsPage() {
  const { server } = useServerContext()

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-3xl space-y-5 p-4 sm:p-6">
        <NotificationSettingsCard
          // La clé change avec le serveur : ses réglages ne se mélangent pas à
          // ceux du serveur consulté juste avant.
          key={server.id}
          title={t('notifications.serverTitle')}
          subtitle={t('notifications.serverSubtitle')}
          queryKey={['server-notifications', server.id]}
          load={() => api.serverNotifications.get(server.id)}
          save={(payload) => api.serverNotifications.update(server.id, payload)}
          sendTest={() => api.serverNotifications.test(server.id)}
        />
      </div>
    </div>
  )
}
