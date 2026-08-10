/** Aperçu d'un serveur : diagnostic, configuration de démarrage, capacités. */

import type { ReactNode } from 'react'
import { AlertTriangle, Wrench } from 'lucide-react'
import { useServerContext } from './context'
import {
  AUTO_RESTART_LABELS,
  CAPABILITY_LABELS,
  formatMemory,
  formatRelative,
} from '@/lib/format'
import { Badge, Card, CardHeader } from '@/components/ui/primitives'

function DefinitionRow({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-4 px-5 py-2.5 text-sm">
      <dt className="shrink-0 text-slate-500">{label}</dt>
      <dd className="min-w-0 truncate text-right text-slate-200">{value}</dd>
    </div>
  )
}

export function OverviewPage() {
  const { server, status } = useServerContext()
  const settings = server.settings

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-5xl space-y-5 p-4 sm:p-6">
        {status?.last_error ? (
          <div className="rounded-lg border border-red-900/60 bg-red-950/40 px-4 py-3.5">
            <div className="flex items-start gap-2.5">
              <AlertTriangle className="mt-0.5 size-4 shrink-0 text-red-400" />
              <div>
                <p className="text-sm font-medium text-red-200">{status.last_error.message}</p>
                {status.last_error.cause ? (
                  <p className="mt-1 text-xs text-red-300/80">
                    <span className="font-medium">Cause : </span>
                    {status.last_error.cause}
                  </p>
                ) : null}
                {status.last_error.remediation ? (
                  <p className="mt-1.5 flex items-start gap-1.5 text-xs text-amber-200/90">
                    <Wrench className="mt-0.5 size-3 shrink-0" />
                    <span>
                      <span className="font-medium">Action : </span>
                      {status.last_error.remediation}
                    </span>
                  </p>
                ) : null}
              </div>
            </div>
          </div>
        ) : null}

        {status && status.consecutive_crashes > 0 ? (
          <div className="rounded-lg border border-amber-900/60 bg-amber-950/30 px-4 py-3 text-sm text-amber-100">
            {status.consecutive_crashes} plantage
            {status.consecutive_crashes > 1 ? 's' : ''} consécutif
            {status.consecutive_crashes > 1 ? 's' : ''}. Le compteur se remet à zéro dès que le
            serveur tient assez longtemps en ligne.
          </div>
        ) : null}

        <div className="grid gap-5 lg:grid-cols-2">
          <Card>
            <CardHeader title="État" />
            <dl className="divide-y divide-slate-800/60">
              <DefinitionRow label="Depuis" value={formatRelative(status?.state_since)} />
              <DefinitionRow label="Raison" value={status?.state_reason ?? '—'} />
              <DefinitionRow label="PID" value={status?.pid ?? '—'} />
              <DefinitionRow
                label="Processus Java"
                value={status?.stats.java_pid ?? '—'}
              />
              <DefinitionRow
                label="Processus surveillés"
                value={status?.stats.process_count ?? 0}
              />
              <DefinitionRow
                label="Console"
                value={
                  status?.console_writable ? (
                    <span className="text-emerald-300">Accessible en écriture</span>
                  ) : (
                    <span className="text-amber-300">Lecture seule</span>
                  )
                }
              />
            </dl>
          </Card>

          <Card>
            <CardHeader title="Démarrage" />
            <dl className="divide-y divide-slate-800/60">
              <DefinitionRow label="Mode" value={server.launcher_key} />
              <DefinitionRow label="Fichier JAR" value={settings?.jar_path ?? '—'} />
              <DefinitionRow label="Script" value={settings?.script_path ?? '—'} />
              <DefinitionRow
                label="Mémoire"
                value={
                  settings?.memory_max_mb
                    ? `${settings.memory_min_mb ? `${formatMemory(settings.memory_min_mb)} – ` : ''}${formatMemory(settings.memory_max_mb)}`
                    : '—'
                }
              />
              <DefinitionRow label="Port" value={settings?.port ?? '—'} />
              <DefinitionRow
                label="Redémarrage auto"
                value={
                  settings ? (AUTO_RESTART_LABELS[settings.auto_restart] ?? settings.auto_restart) : '—'
                }
              />
              <DefinitionRow
                label="CLUF automatique"
                value={settings?.auto_accept_eula ? 'Oui' : 'Non'}
              />
            </dl>
          </Card>
        </div>

        <Card>
          <CardHeader
            title="Fonctionnalités détectées"
            subtitle="Déduites du contenu réel du dossier, pas du type déclaré."
          />
          <div className="flex flex-wrap gap-2 px-5 py-4">
            {server.capabilities.map((capability) => (
              <Badge key={capability}>{CAPABILITY_LABELS[capability] ?? capability}</Badge>
            ))}
          </div>
        </Card>

        <Card>
          <CardHeader title="Arrêt" />
          <dl className="divide-y divide-slate-800/60">
            <DefinitionRow label="Commande" value={settings?.stop_command ?? 'stop'} />
            <DefinitionRow
              label="Délai d'arrêt propre"
              value={`${settings?.stop_timeout_s ?? 60} s`}
            />
            <DefinitionRow
              label="Délai avant terminaison"
              value={`${settings?.kill_timeout_s ?? 15} s`}
            />
            <DefinitionRow
              label="Historique de console"
              value={`${settings?.log_history_lines ?? 5000} lignes`}
            />
          </dl>
        </Card>
      </div>
    </div>
  )
}
