/**
 * Client WebSocket : connexion unique, reconnexion automatique, reprise sans trou.
 *
 * Trois comportements que l'on ne veut pas réécrire dans chaque page :
 *
 * * **une seule connexion** pour toute l'application. Ouvrir un socket par page
 *   multiplierait les authentifications et les files côté serveur ;
 * * **reconnexion à délai croissant**, avec reprise des abonnements. Une coupure
 *   réseau de trente secondes ne doit pas laisser une console figée sans que
 *   personne ne s'en aperçoive ;
 * * **reprise par numéro de séquence** : à la reconnexion, le client annonce la
 *   dernière ligne reçue et le serveur renvoie exactement la suite.
 */

import { useRealtime } from '@/stores/realtime'
import type { EventProgress, LogLine, ServerStatus, SystemStats } from '@/lib/types'

export type Channel = 'status' | 'logs' | 'stats' | 'players' | 'events'

interface Envelope {
  t: string
  sid: number | null
  seq: number
  ts: string
  d: unknown
}

const RECONNECT_DELAYS_MS = [500, 1000, 2000, 5000, 10000, 15000]
const PING_INTERVAL_MS = 25000

export class RealtimeClient {
  private socket: WebSocket | null = null
  private subscriptions = new Map<number, Channel[]>()
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null
  private pingTimer: ReturnType<typeof setInterval> | null = null
  private attempts = 0
  private stopped = false

  connect(): void {
    this.stopped = false
    if (this.socket && this.socket.readyState <= WebSocket.OPEN) return

    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const url = `${protocol}//${window.location.host}/ws`

    useRealtime.getState().setConnection('connecting', this.attempts)
    const socket = new WebSocket(url)
    this.socket = socket

    socket.onopen = () => {
      this.attempts = 0
      useRealtime.getState().setConnection('open', 0)
      // Les abonnements sont rejoués : après une coupure, l'utilisateur n'a
      // rien à faire pour retrouver son flux.
      for (const [serverId, channels] of this.subscriptions) {
        this.sendSubscribe(serverId, channels)
      }
      this.startPing()
    }

    socket.onmessage = (event) => this.handleMessage(event.data)

    socket.onclose = () => {
      this.stopPing()
      useRealtime.getState().setConnection('closed', this.attempts)
      if (!this.stopped) this.scheduleReconnect()
    }

    socket.onerror = () => {
      // `onclose` suit systématiquement : la reconnexion est traitée là-bas.
    }
  }

  /**
   * Ferme la connexion sans oublier les abonnements.
   *
   * Les pages gèrent leur propre cycle (`subscribe`/`unsubscribe`) ; vider la
   * liste ici ferait perdre le flux de toute page restée montée après une simple
   * reconnexion, sans que rien ne vienne le réclamer à nouveau.
   */
  close(): void {
    this.stopped = true
    this.clearReconnect()
    this.stopPing()
    this.socket?.close()
    this.socket = null
    useRealtime.getState().setConnection('idle', 0)
  }

  /** Suit un serveur. Idempotent : réappeler met à jour les canaux. */
  subscribe(serverId: number, channels: Channel[]): void {
    this.subscriptions.set(serverId, channels)
    this.sendSubscribe(serverId, channels)
  }

  unsubscribe(serverId: number): void {
    this.subscriptions.delete(serverId)
    this.send({ t: 'unsubscribe', d: { server_id: serverId } })
  }

  // ------------------------------------------------------------------ //
  private sendSubscribe(serverId: number, channels: Channel[]): void {
    const resumeFrom = useRealtime.getState().lastSeq[serverId]
    this.send({
      t: 'subscribe',
      d: {
        server_id: serverId,
        channels,
        ...(resumeFrom !== undefined ? { resume_from: resumeFrom } : {}),
      },
    })
  }

  private send(message: unknown): void {
    if (this.socket?.readyState === WebSocket.OPEN) {
      this.socket.send(JSON.stringify(message))
    }
  }

  private handleMessage(raw: unknown): void {
    if (typeof raw !== 'string') return

    let envelope: Envelope
    try {
      envelope = JSON.parse(raw) as Envelope
    } catch {
      return
    }

    const store = useRealtime.getState()

    switch (envelope.t) {
      case 'server.status':
        store.applyStatus(envelope.d as ServerStatus)
        break

      case 'server.log': {
        const payload = envelope.d as { lines: LogLine[] }
        if (envelope.sid !== null) store.appendLogs(envelope.sid, payload.lines ?? [])
        break
      }

      case 'server.stats': {
        if (envelope.sid === null) break
        const stats = envelope.d as ServerStatus['stats']
        const current = store.statuses[envelope.sid]
        // La durée de fonctionnement voyage avec les statistiques : sans la
        // reprendre ici, l'en-tête resterait figé sur la valeur du dernier
        // changement d'état, c'est-à-dire zéro juste après un démarrage.
        if (current) store.applyStatus({ ...current, stats, uptime_s: stats.uptime_s })
        break
      }

      case 'server.players': {
        const payload = envelope.d as { players: { username: string; uuid: string | null }[] }
        if (envelope.sid !== null) store.setPlayers(envelope.sid, payload.players ?? [])
        break
      }

      case 'event.run':
        // Une séquence peut durer une heure : sa progression arrive ici plutôt
        // que d'être redemandée en boucle par la page.
        store.applyEventProgress(envelope.d as EventProgress)
        break

      case 'server.crash': {
        // L'état complet arrive par `server.status` ; on ne garde ici que le
        // décompte des lignes de contexte déjà présentes dans la console.
        break
      }

      case 'log.truncated': {
        const payload = envelope.d as { missed: number }
        store.noteMissed(envelope.sid, payload.missed ?? 0)
        break
      }

      case 'system.stats':
        store.setSystem(envelope.d as SystemStats)
        break

      default:
        break
    }
  }

  private scheduleReconnect(): void {
    this.clearReconnect()
    const delay = RECONNECT_DELAYS_MS[Math.min(this.attempts, RECONNECT_DELAYS_MS.length - 1)]!
    this.attempts += 1
    this.reconnectTimer = setTimeout(() => this.connect(), delay)
  }

  private clearReconnect(): void {
    if (this.reconnectTimer !== null) {
      clearTimeout(this.reconnectTimer)
      this.reconnectTimer = null
    }
  }

  private startPing(): void {
    this.stopPing()
    this.pingTimer = setInterval(() => this.send({ t: 'ping' }), PING_INTERVAL_MS)
  }

  private stopPing(): void {
    if (this.pingTimer !== null) {
      clearInterval(this.pingTimer)
      this.pingTimer = null
    }
  }
}

/** Instance partagée par toute l'application. */
export const realtime = new RealtimeClient()
