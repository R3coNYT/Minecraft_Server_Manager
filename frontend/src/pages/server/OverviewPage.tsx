/** Aperçu d'un serveur : diagnostic, configuration de démarrage, capacités. */

import type { ReactNode } from 'react'
import { AlertTriangle, Wrench } from 'lucide-react'
import { useServerContext } from './context'
import { autoRestartLabel, capabilityLabel, formatMemory, formatRelative } from '@/lib/format'
import { Badge, Card, CardHeader } from '@/components/ui/primitives'
import { ResourcePanel } from '@/components/metrics/ResourcePanel'
import { VersionInstaller } from '@/components/servers/VersionInstaller'
import { hasPermission, useMe } from '@/hooks/useApi'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { useToasts } from '@/stores/toasts'
import { t, tn } from '@/i18n'

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
  const queryClient = useQueryClient()
  const { data: me } = useMe()
  const settings = server.settings
  const running = status?.state === 'ONLINE' || status?.state === 'STARTING'
  const canEdit = hasPermission(me, 'server:edit')
  const push = useToasts((state) => state.push)
  const pushError = useToasts((state) => state.pushError)

  // Le seul réglage modifiable depuis cet écran : il conditionne ce qui se passe
  // après une coupure de courant, et n'a pas à se chercher dans un formulaire.
  const autostart = useMutation({
    mutationFn: (value: boolean) =>
      api.servers.update(server.id, { settings: { autostart_on_boot: value } }),
    onSuccess: (updated) => {
      push({
        kind: 'success',
        title: updated.settings?.autostart_on_boot
          ? t('overview.autostartOn')
          : t('overview.autostartOff'),
      })
      void queryClient.invalidateQueries({ queryKey: ['server', server.id] })
      void queryClient.invalidateQueries({ queryKey: ['servers'] })
    },
    onError: (error) => pushError(error),
  })

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
                    <span className="font-medium">{t('common.cause')}</span>
                    {status.last_error.cause}
                  </p>
                ) : null}
                {status.last_error.remediation ? (
                  <p className="mt-1.5 flex items-start gap-1.5 text-xs text-amber-200/90">
                    <Wrench className="mt-0.5 size-3 shrink-0" />
                    <span>
                      <span className="font-medium">{t('common.fix')}</span>
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
            {tn('overview.crashes', status.consecutive_crashes)}
          </div>
        ) : null}

        <div className="grid gap-5 lg:grid-cols-2">
          <Card>
            <CardHeader title={t('overview.state')} />
            <dl className="divide-y divide-slate-800/60">
              <DefinitionRow label={t('overview.since')} value={formatRelative(status?.state_since)} />
              <DefinitionRow label={t('overview.reason')} value={status?.state_reason ?? '—'} />
              <DefinitionRow label={t('overview.pid')} value={status?.pid ?? '—'} />
              <DefinitionRow
                label={t('overview.javaProcess')}
                value={status?.stats.java_pid ?? '—'}
              />
              <DefinitionRow
                label={t('overview.watchedProcesses')}
                value={status?.stats.process_count ?? 0}
              />
              <DefinitionRow
                label={t('overview.console')}
                value={
                  status?.console_writable ? (
                    <span className="text-emerald-300">{t('overview.writable')}</span>
                  ) : (
                    <span className="text-amber-300">{t('overview.readOnly')}</span>
                  )
                }
              />
            </dl>
          </Card>

          <Card>
            <CardHeader title={t('overview.startup')} />
            <dl className="divide-y divide-slate-800/60">
              <DefinitionRow label={t('overview.mode')} value={server.launcher_key} />
              <DefinitionRow label={t('overview.jarFile')} value={settings?.jar_path ?? '—'} />
              <DefinitionRow label={t('overview.script')} value={settings?.script_path ?? '—'} />
              <DefinitionRow
                label={t('overview.memory')}
                value={
                  settings?.memory_max_mb
                    ? `${settings.memory_min_mb ? `${formatMemory(settings.memory_min_mb)} – ` : ''}${formatMemory(settings.memory_max_mb)}`
                    : '—'
                }
              />
              <DefinitionRow label={t('overview.port')} value={settings?.port ?? '—'} />
              <DefinitionRow
                label={t('overview.autoRestart')}
                value={
                  settings ? autoRestartLabel(settings.auto_restart) : '—'
                }
              />
              <DefinitionRow
                label={t('overview.autoEula')}
                value={settings?.auto_accept_eula ? t('common.yes') : t('common.no')}
              />
              <div className="flex items-start justify-between gap-4 px-5 py-2.5">
                <div className="min-w-0">
                  <p className="text-sm text-slate-500">{t('overview.startWithMsm')}</p>
                  <p className="mt-0.5 text-xs text-slate-600">
                    {t('overview.startWithMsmHint')}
                  </p>
                </div>
                <label className="flex shrink-0 cursor-pointer items-center gap-2 text-sm text-slate-200">
                  <input
                    type="checkbox"
                    className="size-4 rounded border-slate-600 bg-slate-900 text-emerald-600 focus:ring-emerald-600"
                    checked={settings?.autostart_on_boot ?? false}
                    disabled={!canEdit || autostart.isPending}
                    onChange={(event) => autostart.mutate(event.target.checked)}
                  />
                  {settings?.autostart_on_boot ? t('common.yes') : t('common.no')}
                </label>
              </div>
            </dl>
          </Card>
        </div>

        <ResourcePanel serverId={server.id} />

        {hasPermission(me, 'server:edit') ? (
          <VersionInstaller
            serverId={server.id}
            serverName={server.name}
            running={running}
            currentJar={settings?.jar_path ?? null}
            onInstalled={() => {
              void queryClient.invalidateQueries({ queryKey: ['server', server.id] })
              void queryClient.invalidateQueries({ queryKey: ['servers'] })
            }}
          />
        ) : null}

        <Card>
          <CardHeader
            title={t('overview.capabilities')}
            subtitle={t('overview.capabilitiesHint')}
          />
          <div className="flex flex-wrap gap-2 px-5 py-4">
            {server.capabilities.map((capability) => (
              <Badge key={capability}>{capabilityLabel(capability)}</Badge>
            ))}
          </div>
        </Card>

        <Card>
          <CardHeader title={t('overview.shutdown')} />
          <dl className="divide-y divide-slate-800/60">
            <DefinitionRow label={t('overview.stopCommand')} value={settings?.stop_command ?? 'stop'} />
            <DefinitionRow
              label={t('overview.stopTimeout')}
              value={`${settings?.stop_timeout_s ?? 60} s`}
            />
            <DefinitionRow
              label={t('overview.killTimeout')}
              value={`${settings?.kill_timeout_s ?? 15} s`}
            />
            <DefinitionRow
              label={t('overview.history')}
              value={t('overview.historyLines', { count: settings?.log_history_lines ?? 5000 })}
            />
          </dl>
        </Card>
      </div>
    </div>
  )
}
