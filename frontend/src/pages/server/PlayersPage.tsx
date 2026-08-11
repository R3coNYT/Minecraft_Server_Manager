/**
 * Joueurs d'un serveur.
 *
 * Trois sources fusionnées côté API : le runtime pour les présences, les
 * fichiers du serveur pour les statuts (opérateur, banni, liste blanche), la
 * base pour l'historique.
 *
 * La liste se rafraîchit à l'arrivée ou au départ d'un joueur — un événement
 * poussé par le WebSocket — et non à intervalle fixe.
 */

import { useEffect, useMemo, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Ban, Search, Shield, Users } from 'lucide-react'
import { api } from '@/lib/api'
import { useRealtime } from '@/stores/realtime'
import { formatRelative } from '@/lib/format'
import { useServerContext } from './context'
import { Badge, Card, CardHeader, EmptyState, Input, LoadingBlock } from '@/components/ui/primitives'
import { ErrorPanel } from '@/components/common/ErrorPanel'
import { PlayerAvatar } from '@/components/players/PlayerAvatar'
import { PlayerActionsMenu } from '@/components/players/PlayerActionsMenu'

export function PlayersPage() {
  const { server, status } = useServerContext()
  const queryClient = useQueryClient()
  const [query, setQuery] = useState('')

  const livePlayers = useRealtime((state) => state.players[server.id])
  const running = status?.state === 'ONLINE' || status?.state === 'STARTING'

  const { data, isLoading, error } = useQuery({
    queryKey: ['players', server.id],
    queryFn: () => api.players.list(server.id),
    staleTime: 10_000,
  })

  // Le WebSocket signale les arrivées et départs ; on recharge alors la liste
  // complète, qui porte aussi les statuts lus dans les fichiers du serveur.
  const onlineSignature = (livePlayers ?? []).map((player) => player.username).join(',')
  useEffect(() => {
    void queryClient.invalidateQueries({ queryKey: ['players', server.id] })
  }, [onlineSignature, server.id, queryClient])

  const players = useMemo(() => {
    const list = data ?? []
    if (!query.trim()) return list
    const needle = query.toLowerCase()
    return list.filter((player) => player.username.toLowerCase().includes(needle))
  }, [data, query])

  const onlineCount = (data ?? []).filter((player) => player.online).length

  if (isLoading) return <LoadingBlock />

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-5xl space-y-4 p-4 sm:p-6">
        <ErrorPanel error={error} />

        <Card>
          <CardHeader
            title={`${onlineCount} joueur${onlineCount > 1 ? 's' : ''} connecté${onlineCount > 1 ? 's' : ''}`}
            subtitle={
              data && data.length > onlineCount
                ? `${data.length - onlineCount} autre(s) déjà venu(s) sur ce serveur`
                : undefined
            }
            action={
              data && data.length > 8 ? (
                <div className="flex w-56 items-center gap-2 rounded-lg border border-slate-700 bg-slate-900 px-2.5">
                  <Search className="size-3.5 shrink-0 text-slate-500" />
                  <Input
                    value={query}
                    onChange={(event) => setQuery(event.target.value)}
                    placeholder="Rechercher…"
                    className="border-0 bg-transparent px-0 py-1.5 text-xs focus:border-0"
                  />
                </div>
              ) : undefined
            }
          />

          {players.length === 0 ? (
            <EmptyState
              icon={<Users className="size-8" />}
              title={
                query
                  ? 'Aucun joueur ne correspond'
                  : running
                    ? 'Aucun joueur connecté'
                    : 'Serveur arrêté'
              }
              description={
                query
                  ? 'Modifier la recherche.'
                  : running
                    ? 'Les arrivées et départs apparaîtront ici en temps réel.'
                    : "Démarrer le serveur pour suivre les joueurs. L'historique reste consultable."
              }
            />
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[640px] text-sm">
                <thead>
                  <tr className="border-b border-slate-800 text-left text-xs text-slate-500">
                    <th className="px-5 py-2.5 font-medium">Joueur</th>
                    <th className="px-5 py-2.5 font-medium">Statut</th>
                    <th className="px-5 py-2.5 font-medium">Dernière présence</th>
                    <th className="px-5 py-2.5 font-medium" title="Non exposé par Minecraft">
                      Ping
                    </th>
                    <th className="px-5 py-2.5" />
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-800/60">
                  {players.map((player) => (
                    <tr key={player.username} className={player.online ? '' : 'opacity-60'}>
                      <td className="px-5 py-2.5">
                        <div className="flex items-center gap-3">
                          <PlayerAvatar
                            serverId={server.id}
                            username={player.username}
                            hasUuid={Boolean(player.uuid)}
                          />
                          <div className="min-w-0">
                            <p className="truncate text-slate-100">{player.username}</p>
                            <p className="truncate font-mono text-[11px] text-slate-600">
                              {player.uuid ?? 'UUID inconnu'}
                            </p>
                          </div>
                        </div>
                      </td>

                      <td className="px-5 py-2.5">
                        <div className="flex flex-wrap items-center gap-1.5">
                          {player.online ? (
                            <Badge className="bg-emerald-500/10 text-emerald-300 ring-emerald-500/30">
                              En ligne
                            </Badge>
                          ) : (
                            <Badge>Hors ligne</Badge>
                          )}
                          {player.is_op ? (
                            <Badge className="bg-amber-500/10 text-amber-300 ring-amber-500/30">
                              <Shield className="size-3" />
                              Opérateur
                              {player.op_level ? ` ${player.op_level}` : ''}
                            </Badge>
                          ) : null}
                          {player.is_banned ? (
                            <Badge
                              className="bg-red-500/10 text-red-300 ring-red-500/30"
                              {...(player.ban_reason ? { title: player.ban_reason } : {})}
                            >
                              <Ban className="size-3" />
                              Banni
                            </Badge>
                          ) : null}
                        </div>
                      </td>

                      <td className="px-5 py-2.5 text-xs text-slate-500">
                        {player.online ? 'Maintenant' : formatRelative(player.last_seen)}
                        {player.total_sessions > 0 ? (
                          <span className="ml-1.5 text-slate-600">
                            · {player.total_sessions} session
                            {player.total_sessions > 1 ? 's' : ''}
                          </span>
                        ) : null}
                      </td>

                      <td
                        className="px-5 py-2.5 text-slate-600"
                        title="Minecraft n'expose pas le ping par joueur"
                      >
                        {player.ping_ms ?? '—'}
                      </td>

                      <td className="px-5 py-2.5 text-right">
                        <PlayerActionsMenu
                          serverId={server.id}
                          player={player}
                          serverRunning={Boolean(running)}
                        />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>

        <p className="px-1 text-xs text-slate-600">
          Le ping par joueur n'est pas exposé par Minecraft : aucune commande console ne le
          fournit. La colonne se remplira si un fournisseur RCON ou un plugin est ajouté.
        </p>
      </div>
    </div>
  )
}
