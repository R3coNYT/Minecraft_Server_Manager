/**
 * Client HTTP de l'API.
 *
 * Deux responsabilités qui justifient un module dédié plutôt que des `fetch`
 * dispersés :
 *
 * 1. **le jeton anti-CSRF** est joint automatiquement à toute requête modifiante.
 *    L'oublier une seule fois produirait un 403 incompréhensible ;
 * 2. **les erreurs sont normalisées** en `ApiError`, qui porte `cause` et
 *    `remediation`. L'interface peut ainsi toujours afficher « Cause / Action »
 *    sans que chaque écran ait à connaître le format de l'API.
 */

import type {
  AuditPage,
  CommandInspection,
  CommandResult,
  Dashboard,
  Detection,
  Health,
  LauncherInfo,
  LogsPage,
  Me,
  Server,
  StopResult,
  SystemStats,
  User,
  ApiErrorBody,
} from './types'

const BASE = '/api/v1'

/** Erreur d'API, enrichie de la cause et de l'action corrective. */
export class ApiError extends Error {
  readonly status: number
  readonly code: string
  readonly cause?: string
  readonly remediation?: string
  readonly traceId?: string

  constructor(status: number, body: ApiErrorBody) {
    super(body.message || 'Erreur inattendue')
    this.name = 'ApiError'
    this.status = status
    this.code = body.code || 'UNKNOWN'
    this.cause = body.cause
    this.remediation = body.remediation
    this.traceId = body.trace_id
  }

  /** L'utilisateur doit-il confirmer pour que l'action aboutisse ? */
  get needsConfirmation(): boolean {
    return this.status === 428
  }

  get isUnauthenticated(): boolean {
    return this.status === 401
  }
}

/** Erreur réseau : le serveur n'a pas répondu du tout. */
export class NetworkError extends ApiError {
  constructor(detail: string) {
    super(0, {
      code: 'NETWORK_ERROR',
      message: 'Le panneau ne répond pas.',
      cause: detail,
      remediation: 'Vérifier que le service MSM est démarré, puis réessayer.',
    })
    this.name = 'NetworkError'
  }
}

function readCookie(name: string): string | null {
  const match = document.cookie.match(new RegExp(`(?:^|; )${name}=([^;]*)`))
  return match?.[1] ? decodeURIComponent(match[1]) : null
}

interface RequestOptions {
  method?: 'GET' | 'POST' | 'PUT' | 'DELETE'
  body?: unknown
  params?: Record<string, string | number | boolean | undefined | null>
  signal?: AbortSignal
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = 'GET', body, params, signal } = options

  const url = new URL(`${BASE}${path}`, window.location.origin)
  for (const [key, value] of Object.entries(params ?? {})) {
    if (value !== undefined && value !== null && value !== '') {
      url.searchParams.set(key, String(value))
    }
  }

  const headers: Record<string, string> = {}
  if (body !== undefined) headers['Content-Type'] = 'application/json'

  if (method !== 'GET') {
    // Double soumission : le cookie est lisible, un site tiers ne l'est pas.
    const csrf = readCookie('msm_csrf')
    if (csrf) headers['X-CSRF-Token'] = csrf
  }

  let response: Response
  try {
    response = await fetch(url.toString(), {
      method,
      headers,
      credentials: 'same-origin',
      body: body === undefined ? undefined : JSON.stringify(body),
      signal,
    })
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') throw error
    throw new NetworkError(error instanceof Error ? error.message : String(error))
  }

  if (response.status === 204) return undefined as T

  const text = await response.text()
  const payload = text ? safeParse(text) : null

  if (!response.ok) {
    throw new ApiError(response.status, (payload as ApiErrorBody) ?? {
      code: `HTTP_${response.status}`,
      message: 'Erreur inattendue',
    })
  }

  return payload as T
}

function safeParse(text: string): unknown {
  try {
    return JSON.parse(text)
  } catch {
    return { code: 'INVALID_RESPONSE', message: text.slice(0, 200) }
  }
}

/** Surface complète de l'API, groupée par domaine. */
export const api = {
  auth: {
    me: () => request<Me>('/auth/me'),
    login: (username: string, password: string) =>
      request<Me>('/auth/login', { method: 'POST', body: { username, password } }),
    logout: () => request<{ status: string }>('/auth/logout', { method: 'POST' }),
    changePassword: (currentPassword: string, newPassword: string) =>
      request<{ status: string; detail: string }>('/auth/password', {
        method: 'POST',
        body: { current_password: currentPassword, new_password: newPassword },
      }),
  },

  servers: {
    list: () => request<Server[]>('/servers'),
    dashboard: () => request<Dashboard>('/servers/dashboard'),
    get: (id: number) => request<Server>(`/servers/${id}`),
    status: (id: number) => request<Server['status']>(`/servers/${id}/status`),
    create: (payload: Record<string, unknown>) =>
      request<Server>('/servers', { method: 'POST', body: payload }),
    update: (id: number, payload: Record<string, unknown>) =>
      request<Server>(`/servers/${id}`, { method: 'PUT', body: payload }),
    remove: (id: number) =>
      request<{ status: string; detail: string }>(`/servers/${id}`, { method: 'DELETE' }),
    detect: (directory: string) =>
      request<Detection>('/servers/detect', { method: 'POST', body: { directory } }),

    start: (id: number) => request<Server['status']>(`/servers/${id}/start`, { method: 'POST' }),
    stop: (id: number) => request<StopResult>(`/servers/${id}/stop`, { method: 'POST' }),
    restart: (id: number) =>
      request<Server['status']>(`/servers/${id}/restart`, { method: 'POST' }),
    kill: (id: number) => request<Server['status']>(`/servers/${id}/kill`, { method: 'POST' }),
  },

  console: {
    logs: (
      id: number,
      params: { limit?: number; since?: number; before?: number; search?: string; regex?: boolean },
    ) => request<LogsPage>(`/servers/${id}/logs`, { params }),
    send: (id: number, command: string, confirm = false) =>
      request<CommandResult>(`/servers/${id}/command`, {
        method: 'POST',
        body: { command, confirm },
      }),
    inspect: (id: number, command: string) =>
      request<CommandInspection>(`/servers/${id}/command/inspect`, {
        method: 'POST',
        body: { command },
      }),
  },

  users: {
    list: () => request<User[]>('/users'),
    create: (payload: Record<string, unknown>) =>
      request<User>('/users', { method: 'POST', body: payload }),
    update: (id: number, payload: Record<string, unknown>) =>
      request<User>(`/users/${id}`, { method: 'PUT', body: payload }),
    remove: (id: number) => request<{ status: string }>(`/users/${id}`, { method: 'DELETE' }),
  },

  audit: {
    search: (params: {
      server_id?: number
      action?: string
      limit?: number
      offset?: number
    }) => request<AuditPage>('/audit', { params }),
    actions: () => request<string[]>('/audit/actions'),
  },

  system: {
    health: () => request<Health>('/health'),
    stats: () => request<SystemStats>('/system/stats'),
    launchers: () => request<LauncherInfo[]>('/system/launchers'),
  },
}
