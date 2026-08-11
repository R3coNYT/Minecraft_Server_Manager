/**
 * État temps réel alimenté par le WebSocket.
 *
 * Séparé du cache REST (TanStack Query) : ce sont deux natures de données. Le
 * REST détient ce qui est configuré et se recharge à la demande ; ce store
 * détient ce qui change en continu et n'a de sens qu'à l'instant présent.
 *
 * L'historique de console est **borné côté client** exactement comme il l'est
 * côté serveur : une console ouverte plusieurs heures sur un serveur bavard
 * finirait sinon par saturer l'onglet du navigateur.
 */

import { create } from 'zustand'
import type { EventProgress, LogLine, ServerStatus, SystemStats } from '@/lib/types'

/** Nombre maximal de lignes conservées par serveur dans le navigateur. */
export const MAX_CLIENT_LINES = 5000

export type ConnectionState = 'idle' | 'connecting' | 'open' | 'closed'

export interface PlayerEntry {
  username: string
  uuid: string | null
}

interface RealtimeState {
  connection: ConnectionState
  /** Nombre de tentatives de reconnexion consécutives, pour l'affichage. */
  reconnectAttempts: number

  statuses: Record<number, ServerStatus>
  logs: Record<number, LogLine[]>
  lastSeq: Record<number, number>
  players: Record<number, PlayerEntry[]>
  /** Lignes perdues (tampon dépassé ou client trop lent), par serveur. */
  missedLines: Record<number, number>
  /** Dernière progression connue d'un événement, par serveur. */
  eventProgress: Record<number, EventProgress>
  system: SystemStats | null

  setConnection: (state: ConnectionState, attempts?: number) => void
  applyStatus: (status: ServerStatus) => void
  appendLogs: (serverId: number, lines: LogLine[]) => void
  clearLogs: (serverId: number) => void
  setPlayers: (serverId: number, players: PlayerEntry[]) => void
  noteMissed: (serverId: number | null, count: number) => void
  applyEventProgress: (progress: EventProgress) => void
  setSystem: (stats: SystemStats) => void
  forget: (serverId: number) => void
}

export const useRealtime = create<RealtimeState>((set) => ({
  connection: 'idle',
  reconnectAttempts: 0,
  statuses: {},
  logs: {},
  lastSeq: {},
  players: {},
  missedLines: {},
  eventProgress: {},
  system: null,

  setConnection: (connection, attempts) =>
    set((state) => ({
      connection,
      reconnectAttempts: attempts ?? state.reconnectAttempts,
    })),

  applyStatus: (status) =>
    set((state) => ({
      statuses: { ...state.statuses, [status.id]: status },
      players: {
        ...state.players,
        [status.id]: (status.players ?? []).map((username) => ({ username, uuid: null })),
      },
    })),

  appendLogs: (serverId, lines) =>
    set((state) => {
      if (lines.length === 0) return state

      const cursor = state.lastSeq[serverId] ?? 0
      // Filet anti-doublon : une reprise après coupure peut recouvrir des
      // lignes déjà reçues si la connexion est retombée pendant l'envoi.
      const fresh = lines.filter((line) => line.seq > cursor)
      if (fresh.length === 0) return state

      const previous = state.logs[serverId] ?? []
      const merged = [...previous, ...fresh]
      const trimmed =
        merged.length > MAX_CLIENT_LINES ? merged.slice(merged.length - MAX_CLIENT_LINES) : merged

      return {
        logs: { ...state.logs, [serverId]: trimmed },
        lastSeq: { ...state.lastSeq, [serverId]: fresh[fresh.length - 1]!.seq },
      }
    }),

  clearLogs: (serverId) =>
    set((state) => ({
      // `lastSeq` est conservé : vider l'affichage ne doit pas redemander au
      // serveur un historique que l'utilisateur vient justement d'effacer.
      logs: { ...state.logs, [serverId]: [] },
    })),

  setPlayers: (serverId, players) =>
    set((state) => ({ players: { ...state.players, [serverId]: players } })),

  noteMissed: (serverId, count) =>
    set((state) => {
      if (serverId === null || count <= 0) return state
      return {
        missedLines: {
          ...state.missedLines,
          [serverId]: (state.missedLines[serverId] ?? 0) + count,
        },
      }
    }),

  applyEventProgress: (progress) =>
    set((state) => ({
      eventProgress: { ...state.eventProgress, [progress.server_id]: progress },
    })),

  setSystem: (system) => set({ system }),

  forget: (serverId) =>
    set((state) => {
      const logs = { ...state.logs }
      const lastSeq = { ...state.lastSeq }
      const missedLines = { ...state.missedLines }
      const eventProgress = { ...state.eventProgress }
      delete logs[serverId]
      delete lastSeq[serverId]
      delete missedLines[serverId]
      delete eventProgress[serverId]
      return { logs, lastSeq, missedLines, eventProgress }
    }),
}))
