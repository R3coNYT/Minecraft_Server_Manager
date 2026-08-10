/**
 * Types miroir de l'API.
 *
 * Ils sont écrits à la main plutôt que générés : le contrat est encore en cours
 * de stabilisation, et une divergence se voit immédiatement à la compilation.
 * Le jour où l'API se fige, `openapi-typescript` prendra le relais.
 */

export type ServerState =
  | 'OFFLINE'
  | 'STARTING'
  | 'ONLINE'
  | 'STOPPING'
  | 'CRASHED'
  | 'UNKNOWN'

export type Role = 'ADMIN' | 'MODERATOR' | 'VIEWER'

export type LogLevel = 'TRACE' | 'DEBUG' | 'INFO' | 'WARN' | 'ERROR' | 'FATAL' | 'RAW'

export type DangerLevel = 'SAFE' | 'SENSITIVE' | 'DESTRUCTIVE'

export interface ApiErrorBody {
  code: string
  message: string
  cause?: string
  remediation?: string
  trace_id?: string
}

export interface User {
  id: number
  username: string
  display_name: string | null
  email: string | null
  role: Role
  is_active: boolean
  last_login_at: string | null
  created_at: string
}

export interface Me extends User {
  permissions: string[]
}

export interface ProcessStats {
  cpu_percent: number
  memory_mb: number
  process_count: number
  java_pid: number | null
  uptime_s: number
}

export interface ServerStatus {
  id: number
  name: string
  state: ServerState
  state_since: string
  state_reason: string | null
  pid: number | null
  uptime_s: number
  players_online: number
  players: string[]
  consecutive_crashes: number
  console_writable: boolean
  last_error: ApiErrorBody | null
  stats: ProcessStats
  log_seq: number
  log_dropped: number
}

export interface ServerSettings {
  java_path: string | null
  jar_path: string | null
  script_path: string | null
  custom_argv: string[]
  jvm_args: string[]
  extra_args: string[]
  env: Record<string, string>
  memory_min_mb: number | null
  memory_max_mb: number | null
  port: number | null
  stop_command: string
  stop_timeout_s: number
  kill_timeout_s: number
  start_timeout_s: number
  auto_restart: 'NEVER' | 'ON_CRASH' | 'ALWAYS'
  restart_delay_s: number
  max_consecutive_crashes: number
  autostart_on_boot: boolean
  auto_accept_eula: boolean
  log_history_lines: number
  use_pty: boolean
  rcon_enabled: boolean
}

export interface Server {
  id: number
  name: string
  slug: string
  description: string | null
  directory: string
  server_type: string
  minecraft_version: string | null
  launcher_key: string
  enabled: boolean
  sort_order: number
  color: string | null
  settings: ServerSettings | null
  capabilities: string[]
  status: ServerStatus | null
}

export interface Player {
  username: string
  uuid: string | null
  online: boolean
  is_op: boolean
  is_banned: boolean
  is_whitelisted: boolean
  op_level: number | null
  ban_reason: string | null
  first_seen: string | null
  last_seen: string | null
  total_sessions: number
  /** Toujours nul : Minecraft n'expose pas le ping par joueur. */
  ping_ms: number | null
}

export interface PlayerActionResult {
  username: string
  command: string
}

export interface SystemStats {
  cpu_percent: number
  cpu_count: number
  memory_total_mb: number
  memory_used_mb: number
  memory_percent: number
  disk_total_gb?: number
  disk_used_gb?: number
  disk_percent?: number
}

export interface DashboardSummary {
  servers_total: number
  servers_online: number
  servers_offline: number
  players_online: number
  cpu_percent: number
  memory_mb: number
}

export interface Dashboard {
  summary: DashboardSummary
  servers: Server[]
  system: SystemStats
}

export interface LogLine {
  seq: number
  ts: string
  text: string
  level: LogLevel
  thread: string | null
  category: string | null
  source: 'stdout' | 'stderr' | 'msm' | 'command'
  server_time: string | null
}

export interface LogsPage {
  lines: LogLine[]
  first_seq: number | null
  last_seq: number | null
  dropped: number
}

export interface CommandResult {
  command: string
  danger: DangerLevel
}

export interface CommandInspection {
  command: string
  danger: DangerLevel
  requires_confirmation: boolean
  requires_strong_confirmation: boolean
  explanation: string | null
}

export interface StopResult {
  stage: 'command' | 'signal' | 'kill' | 'already_stopped'
  forced: boolean
  exit_code: number | null
  duration_s: number
  status: ServerStatus
}

export interface JarCandidate {
  name: string
  size_bytes: number
  server_type: string
  minecraft_version: string | null
  score: number
}

export interface Detection {
  directory: string
  exists: boolean
  server_type: string
  minecraft_version: string | null
  launcher_key: string | null
  jar_path: string | null
  script_path: string | null
  jars: JarCandidate[]
  scripts: string[]
  capabilities: string[]
  eula_accepted: boolean | null
  port: number | null
  notes: string[]
}

export interface LauncherInfo {
  key: string
  label: string
  description: string
  unavailable_reason: string | null
}

export interface AuditEntry {
  id: number
  ts: string
  actor_username: string
  actor_role: string | null
  ip_address: string | null
  action: string
  result: 'SUCCESS' | 'DENIED' | 'ERROR'
  server_id: number | null
  target_type: string | null
  target_id: string | null
  summary: string
  payload: Record<string, unknown> | null
}

export interface AuditPage {
  entries: AuditEntry[]
  total: number
  limit: number
  offset: number
}

export interface Health {
  status: string
  version: string
  python: string
  platform: string
  process_backend: string
  servers_registered: number
}
