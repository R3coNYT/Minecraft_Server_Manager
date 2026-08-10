/** Accès aux données de l'API via TanStack Query. */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ApiError, api } from '@/lib/api'
import { useRealtime } from '@/stores/realtime'
import type { Me, Server, ServerStatus } from '@/lib/types'

export const queryKeys = {
  me: ['me'] as const,
  servers: ['servers'] as const,
  server: (id: number) => ['servers', id] as const,
  dashboard: ['dashboard'] as const,
  launchers: ['launchers'] as const,
  audit: (params: Record<string, unknown>) => ['audit', params] as const,
  users: ['users'] as const,
  health: ['health'] as const,
}

/**
 * Session courante.
 *
 * Un 401 n'est **pas** réessayé : c'est une réponse valide signifiant « non
 * connecté », pas une panne. Le réessayer ferait clignoter l'écran de connexion.
 */
export function useMe() {
  return useQuery<Me | null>({
    queryKey: queryKeys.me,
    queryFn: async () => {
      try {
        return await api.auth.me()
      } catch (error) {
        if (error instanceof ApiError && error.isUnauthenticated) return null
        throw error
      }
    },
    retry: false,
    staleTime: 60_000,
  })
}

export function useLogin() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ username, password }: { username: string; password: string }) =>
      api.auth.login(username, password),
    onSuccess: (me) => {
      queryClient.setQueryData(queryKeys.me, me)
      void queryClient.invalidateQueries()
    },
  })
}

export function useLogout() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: () => api.auth.logout(),
    onSettled: () => {
      queryClient.setQueryData(queryKeys.me, null)
      queryClient.clear()
    },
  })
}

/**
 * Liste des serveurs.
 *
 * Aucun rafraîchissement périodique : l'état vivant arrive par WebSocket. Cette
 * requête ne sert qu'à la configuration, qui ne change que sur action explicite.
 */
export function useServers() {
  return useQuery<Server[]>({
    queryKey: queryKeys.servers,
    queryFn: () => api.servers.list(),
    staleTime: 30_000,
  })
}

export function useServer(id: number) {
  return useQuery<Server>({
    queryKey: queryKeys.server(id),
    queryFn: () => api.servers.get(id),
    enabled: Number.isFinite(id) && id > 0,
  })
}

export function useDashboard() {
  return useQuery({
    queryKey: queryKeys.dashboard,
    queryFn: () => api.servers.dashboard(),
    staleTime: 15_000,
  })
}

export function useLaunchers() {
  return useQuery({
    queryKey: queryKeys.launchers,
    queryFn: () => api.system.launchers(),
    staleTime: Infinity,
  })
}

/**
 * État d'un serveur : priorité au flux temps réel, repli sur la réponse REST.
 *
 * Sans ce repli, l'affichage resterait vide entre le chargement de la page et
 * l'arrivée du premier message WebSocket.
 */
export function useServerStatus(serverId: number, fallback: ServerStatus | null | undefined) {
  const live = useRealtime((state) => state.statuses[serverId])
  return live ?? fallback ?? null
}

export function useLifecycleActions(serverId: number) {
  const queryClient = useQueryClient()
  const applyStatus = useRealtime((state) => state.applyStatus)

  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: queryKeys.servers })
    void queryClient.invalidateQueries({ queryKey: queryKeys.dashboard })
  }

  return {
    start: useMutation({
      mutationFn: () => api.servers.start(serverId),
      onSuccess: (status) => {
        if (status) applyStatus(status)
        invalidate()
      },
    }),
    stop: useMutation({
      mutationFn: () => api.servers.stop(serverId),
      onSuccess: (result) => {
        applyStatus(result.status)
        invalidate()
      },
    }),
    restart: useMutation({
      mutationFn: () => api.servers.restart(serverId),
      onSuccess: (status) => {
        if (status) applyStatus(status)
        invalidate()
      },
    }),
    kill: useMutation({
      mutationFn: () => api.servers.kill(serverId),
      onSuccess: (status) => {
        if (status) applyStatus(status)
        invalidate()
      },
    }),
  }
}

/** L'utilisateur possède-t-il cette permission ? */
export function hasPermission(me: Me | null | undefined, permission: string): boolean {
  return me?.permissions.includes(permission) ?? false
}
