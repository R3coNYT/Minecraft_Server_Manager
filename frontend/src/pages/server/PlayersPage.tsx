/**
 * Joueurs connectés.
 *
 * Les données proviennent des logs du serveur : pseudo et UUID quand celui-ci
 * l'a annoncé. Le **ping n'existe pas** en Minecraft vanilla — aucune commande
 * console ne le fournit — la colonne affiche donc « — » plutôt qu'une valeur
 * inventée. Un fournisseur RCON ou un plugin pourra la remplir plus tard.
 */

import { Users } from 'lucide-react'
import { useRealtime } from '@/stores/realtime'
import { useServerContext } from './context'
import { Card, CardHeader, EmptyState } from '@/components/ui/primitives'

export function PlayersPage() {
  const { server, status } = useServerContext()
  const players = useRealtime((state) => state.players[server.id]) ?? []
  const running = status?.state === 'ONLINE'

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-4xl p-4 sm:p-6">
        <Card>
          <CardHeader
            title={`Joueurs connectés (${players.length})`}
            subtitle="Détectés en direct à partir de la console du serveur."
          />

          {players.length === 0 ? (
            <EmptyState
              icon={<Users className="size-8" />}
              title={running ? 'Aucun joueur connecté' : 'Serveur arrêté'}
              description={
                running
                  ? 'Les arrivées et départs apparaîtront ici en temps réel.'
                  : 'Démarrer le serveur pour suivre les joueurs.'
              }
            />
          ) : (
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-slate-800 text-left text-xs text-slate-500">
                  <th className="px-5 py-2.5 font-medium">Joueur</th>
                  <th className="px-5 py-2.5 font-medium">UUID</th>
                  <th className="px-5 py-2.5 font-medium">Ping</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-800/60">
                {players.map((player) => (
                  <tr key={player.username}>
                    <td className="px-5 py-2.5">
                      <div className="flex items-center gap-2.5">
                        <span
                          className="flex size-6 items-center justify-center rounded bg-slate-800 text-[11px] font-semibold text-slate-300"
                          aria-hidden
                        >
                          {player.username.slice(0, 2).toUpperCase()}
                        </span>
                        <span className="text-slate-200">{player.username}</span>
                      </div>
                    </td>
                    <td className="px-5 py-2.5 font-mono text-xs text-slate-500">
                      {player.uuid ?? '—'}
                    </td>
                    <td className="px-5 py-2.5 text-slate-600" title="Non exposé par Minecraft">
                      —
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Card>
      </div>
    </div>
  )
}
