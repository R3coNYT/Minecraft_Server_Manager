/**
 * Regroupement des serveurs d'une liste : les miens d'abord, puis ceux qu'on me
 * partage, puis — pour l'équipe de MSM, qui voit tout — ceux des autres,
 * propriétaire par propriétaire. L'API les renvoie déjà dans cet ordre ; on n'en
 * tire ici que les titres de groupe.
 */

import type { Server } from './types'
import { t } from '@/i18n'

export interface ServerGroup {
  key: string
  title: string
  servers: Server[]
}

export function groupServers(servers: Server[], myId: number | undefined): ServerGroup[] {
  const mine: Server[] = []
  const shared: Server[] = []
  const others = new Map<string, Server[]>()

  for (const server of servers) {
    if (server.owner_id === myId) mine.push(server)
    else if (server.access) shared.push(server)
    else {
      const list = others.get(server.owner_username) ?? []
      list.push(server)
      others.set(server.owner_username, list)
    }
  }

  const groups: ServerGroup[] = []
  if (mine.length) groups.push({ key: 'mine', title: t('access.mine'), servers: mine })
  if (shared.length) groups.push({ key: 'shared', title: t('access.sharedWithMe'), servers: shared })
  for (const [owner, list] of others) {
    groups.push({ key: `owner:${owner}`, title: t('access.of', { name: owner }), servers: list })
  }
  return groups
}
