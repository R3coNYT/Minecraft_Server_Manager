/**
 * Abonne la page courante au flux temps réel d'un serveur.
 *
 * L'abonnement est **relâché au démontage** : sans cela, quitter une page de
 * console laisserait le serveur pousser des lignes que plus personne n'affiche,
 * pour toutes les pages jamais visitées.
 */

import { useEffect } from 'react'
import { realtime, type Channel } from '@/ws/client'

// `events` est inclus d'office : il ne pousse quelque chose que pendant une
// séquence en cours, et une progression manquée est une progression perdue —
// l'utilisateur peut avoir quitté la page des événements entre-temps.
const DEFAULT_CHANNELS: Channel[] = ['status', 'logs', 'stats', 'players', 'events', 'backups']

export function useServerSubscription(
  serverId: number,
  channels: Channel[] = DEFAULT_CHANNELS,
): void {
  // Les canaux sont sérialisés pour ne pas relancer l'effet à chaque rendu,
  // un littéral de tableau étant une nouvelle référence à chaque fois.
  const key = channels.join(',')

  useEffect(() => {
    if (!Number.isFinite(serverId) || serverId <= 0) return
    const list = key.split(',') as Channel[]
    realtime.subscribe(serverId, list)
    return () => realtime.unsubscribe(serverId)
  }, [serverId, key])
}
