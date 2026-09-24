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

export type Role = 'ADMIN' | 'MODERATOR' | 'USER'

/** Rôle d'un compte sur un serveur. */
export type ServerRole = 'OWNER' | 'ADMIN' | 'VIEWER'

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
  /** Nom du dossier du compte sur le disque ; ne change jamais. */
  storage_id: string
  last_login_at: string | null
  created_at: string
  /** Langue choisie par le compte ; `null` : celle du panneau. */
  language: string | null
  banned_at: string | null
  ban_reason: string | null
  /** Adresse de l'avatar, ou `null` sans avatar. */
  avatar_url: string | null
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
  owner_id: number
  owner_username: string
  /** Rôle du compte courant sur ce serveur ; `null` pour un admin qui le voit sans en être membre. */
  access: ServerRole | null
  /** Partagé avec le compte courant par quelqu'un d'autre. */
  shared: boolean
  /** Droits effectifs du compte courant sur ce serveur. */
  permissions: string[]
}

export interface ServerMember {
  user_id: number
  username: string
  role: ServerRole
  added_at: string
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

export interface ManagedFile {
  name: string
  size_bytes: number
  modified_at: string
  enabled: boolean
}

export interface ConfigEntry {
  name: string
  path: string
  is_directory: boolean
  size_bytes: number
  modified_at: string
  format: string
  editable: boolean
}

export interface ConfigFile {
  path: string
  name: string
  format: string
  content: string
  encoding: string
  size_bytes: number
  modified_at: string
}

export interface ServerProperty {
  key: string
  value: string
  known: boolean
  label: string
  type: 'boolean' | 'integer' | 'string' | 'enum'
  choices: string[]
  minimum: number | null
  maximum: number | null
  requires_restart: boolean
  help: string
}

export interface PropertiesPage {
  exists: boolean
  entries: ServerProperty[]
}

export interface ActionField {
  name: string
  label: string
  type: string
  required: boolean
  default: unknown
  placeholder: string
  help: string
  minimum: number | null
  maximum: number | null
}

export interface ActionType {
  key: string
  label: string
  description: string
  danger: DangerLevel
  fields: ActionField[]
}

export interface EventStep {
  action: string
  params: Record<string, unknown>
  summary: string
}

export interface GameEvent {
  id: number
  name: string
  description: string | null
  server_id: number | null
  steps: EventStep[]
  danger: DangerLevel
}

export interface EventRun {
  id: number
  event_id: number | null
  status: 'PENDING' | 'RUNNING' | 'COMPLETED' | 'FAILED' | 'CANCELLED'
  current_step: number
  total_steps: number
  started_at: string | null
  finished_at: string | null
  error: string | null
}

export interface Backup {
  id: number
  server_id: number
  kind: string
  status: 'PENDING' | 'RUNNING' | 'COMPLETED' | 'FAILED'
  size_bytes: number | null
  created_at: string
  created_by: number | null
  error: string | null
  available: boolean
}

export interface BackupInventoryItem {
  name: string
  size_bytes: number
  enabled: boolean
}

/** Ce que l'archive déclare contenir, lu sans rien extraire. */
export interface BackupManifest {
  created_at: string | null
  msm_version: string | null
  server: { name?: string; type?: string; minecraft_version?: string | null }
  content: { worlds?: string[]; file_count?: number; total_bytes?: number }
  mods: BackupInventoryItem[]
  plugins: BackupInventoryItem[]
}

/** Progression d'une sauvegarde, poussée par le WebSocket. */
export interface BackupProgress {
  backup_id: number
  server_id: number
  status: string
  phase: string
  done: number
  total: number
  percent: number
  error: string | null
}

export type TriggerKind = 'INTERVAL' | 'DAILY' | 'WEEKLY'

export type ScheduleAction = 'BACKUP' | 'RESTART' | 'START' | 'STOP' | 'EVENT' | 'COMMAND'

export interface ScheduleRule {
  trigger: TriggerKind
  interval_minutes?: number | null
  hour?: number | null
  minute?: number | null
  days?: number[] | null
  timezone: string
}

export interface Schedule {
  id: number
  server_id: number
  name: string
  action: ScheduleAction
  payload: Record<string, unknown>
  rule: ScheduleRule
  /** Résumé lisible calculé par le serveur (« Chaque jour à 04:00 »). */
  summary: string
  enabled: boolean
  next_run_at: string | null
  last_run_at: string | null
  last_status: 'NEVER' | 'SUCCESS' | 'FAILED' | 'MISSED' | 'SKIPPED'
  last_error: string | null
}

export interface NotificationEventType {
  key: string
  label: string
}

export interface NotificationSettings {
  enabled: boolean
  events: string[]
  /** Événements que ce salon peut annoncer : global ou propres au serveur. */
  available_events: NotificationEventType[]
  webhook_configured: boolean
  webhook_hint: string | null
  /** Le secret enregistré n'est plus déchiffrable : la clé applicative a changé. */
  webhook_unreadable: boolean
}

export interface DownloadSource {
  key: string
  label: string
}

export interface GameVersion {
  id: string
  channel: string
  minecraft_version: string
  /** Recommandée par l'éditeur comme la plus stable (Mohist) : proposée par défaut. */
  recommended?: boolean
}

export interface InstallResult {
  file: string
  path: string
  previous_jar: string | null
  size_bytes: number
  version: string
}

export type MetricRange = '1h' | '6h' | '24h' | '7d'

export interface MetricPoint {
  ts: string
  cpu_percent: number
  memory_mb: number
  players_online: number
}

export interface MetricsHistory {
  range: string
  bucket_s: number
  points: MetricPoint[]
  peak_cpu_percent: number
  peak_memory_mb: number
  peak_players: number
}

/** Progression poussée par le WebSocket pendant une exécution. */
export interface EventProgress {
  run_id: number
  server_id: number
  status: EventRun['status']
  current_step: number
  total_steps: number
  summary: string
  error: string | null
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
  /** Ressources de la machine : réservées aux admins de MSM. */
  system: SystemStats | null
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

// --- Intégration avec le serveur de fichiers d'un launcher -------------------

export type LauncherSyncStatus =
  | 'NEVER'
  | 'UP_TO_DATE'
  | 'APPLIED'
  | 'PENDING_RESTART'
  | 'BLOCKED'
  | 'FAILED'

export type ModSide = 'client' | 'server' | 'both'

export interface LauncherLinkMod {
  path: string
  sha256: string
  size: number
  side: ModSide
  /** D'où vient le côté retenu : forcé dans MSM, déclaré, détecté ou par défaut. */
  side_source: 'override' | 'manifest' | 'detected' | 'default'
  declared_side: ModSide | null
  override: ModSide | null
  disabled_upstream: boolean
  on_server: 'enabled' | 'disabled' | 'absent'
}

export interface LauncherLink {
  enabled: boolean
  file_server_url: string
  sync_paths: string[]
  interval_minutes: number
  push_configured: boolean
  push_token_hint: string | null
  push_token_unreadable: boolean
  next_sync_at: string | null
  last_sync_at: string | null
  last_sync_status: LauncherSyncStatus
  last_sync_error: string | null
  last_sync_summary: {
    installs?: number
    removes?: number
    adopted?: number
    unchanged?: number
    client_only?: number
    download_bytes?: number
    notes?: string[]
  }
  pack_version: string | null
  pending: {
    installs: number
    removes: number
    download_bytes: number
    server_running: boolean
  } | null
  publish: {
    state_revision: number
    pushed_revision: number
    up_to_date: boolean
    disabled_files: string[]
    last_push_at: string | null
    last_push_error: string | null
  }
  mods: LauncherLinkMod[]
}

export interface UiSettings {
  language: 'en' | 'fr'
  languages: string[]
}

// --------------------------------------------------------------------------- //
//  Création de serveurs de zéro
// --------------------------------------------------------------------------- //
export interface ProvisioningDistribution {
  key: string
  label: string
  server_type: string
  /** Une version de Minecraft se décline-t-elle en builds (loader, NeoForge…) ? */
  has_builds: boolean
  /** `installer` : un installeur s'exécute pendant la création (NeoForge). */
  kind: 'jar' | 'installer'
}

export interface ProvisioningBuild {
  id: string
  label: string
  channel: string
}

export interface ProvisioningDefaults {
  roots: string[]
  directory: string
  port: number
}

export type ProvisioningStepStatus = 'pending' | 'running' | 'done' | 'failed' | 'skipped'

export interface ProvisioningStep {
  key: string
  label: string
  status: ProvisioningStepStatus
  detail: string
}

export interface ProvisioningJob {
  id: string
  name: string
  directory: string
  distribution: string
  version: string
  build: string | null
  status: 'RUNNING' | 'COMPLETED' | 'FAILED'
  /** Avancement du téléchargement, de 0 à 1 ; `null` si la taille est inconnue. */
  progress: number | null
  downloaded_bytes: number
  steps: ProvisioningStep[]
  error: { message: string; cause: string | null; remediation: string | null } | null
  server_id: number | null
  created_at: string
  finished_at: string | null
}

