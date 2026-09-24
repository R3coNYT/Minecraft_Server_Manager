/** À qui est ce serveur, vu du compte courant : « Partagé », ou son propriétaire. */

import { useMe } from '@/hooks/useApi'
import type { Server } from '@/lib/types'
import { Badge } from '@/components/ui/primitives'
import { t } from '@/i18n'

export function AccessBadge({ server, compact = false }: { server: Server; compact?: boolean }) {
  const { data: me } = useMe()
  if (!me || server.owner_id === me.id) return null
  if (server.shared) {
    return (
      <Badge className="bg-sky-950/60 text-sky-300 ring-sky-800">
        {t('access.shared')}
      </Badge>
    )
  }
  // Un admin ou un modérateur voit le serveur d'un autre sans en être membre.
  return compact ? null : <Badge>{t('access.owner', { name: server.owner_username })}</Badge>
}
