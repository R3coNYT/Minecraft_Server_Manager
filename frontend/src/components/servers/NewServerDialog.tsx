/**
 * Création d'un serveur de zéro : un formulaire, puis le suivi de la tâche.
 *
 * Le serveur valide la demande tout de suite (nom, dossier, mémoire…) ; ce qui
 * prend du temps — téléchargement, installeur NeoForge — tourne en tâche de
 * fond, suivie ici étape par étape. Fermer la fenêtre ne l'interrompt pas.
 */

import { useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import {
  CheckCircle2,
  Circle,
  ExternalLink,
  Info,
  Loader2,
  MinusCircle,
  XCircle,
} from 'lucide-react'
import { api } from '@/lib/api'
import { queryKeys } from '@/hooks/useApi'
import { useToasts } from '@/stores/toasts'
import { formatBytes } from '@/lib/format'
import type { ProvisioningJob, ProvisioningStepStatus } from '@/lib/types'
import { Dialog } from '@/components/ui/Dialog'
import { Button } from '@/components/ui/Button'
import { Checkbox, Field, Input, Select } from '@/components/ui/primitives'
import { ErrorPanel } from '@/components/common/ErrorPanel'
import { cn } from '@/lib/cn'
import { t } from '@/i18n'

const EULA_URL = 'https://aka.ms/MinecraftEULA'

interface NewServerDialogProps {
  open: boolean
  onClose: () => void
}

/** Attend que la saisie se calme avant d'interroger le serveur. */
function useDebounced<T>(value: T, delay = 300): T {
  const [debounced, setDebounced] = useState(value)
  useEffect(() => {
    const timer = window.setTimeout(() => setDebounced(value), delay)
    return () => window.clearTimeout(timer)
  }, [value, delay])
  return debounced
}

export function NewServerDialog({ open, onClose }: NewServerDialogProps) {
  const [jobId, setJobId] = useState<string | null>(null)
  // Le formulaire reste monté pendant le suivi : après un échec, « Revenir au
  // formulaire » retrouve les valeurs saisies. Fermer la fenêtre, elle, repart
  // d'un formulaire vierge.
  const [formKey, setFormKey] = useState(0)

  const close = () => {
    setJobId(null)
    setFormKey((key) => key + 1)
    onClose()
  }

  return (
    <>
      <FormDialog key={formKey} open={open && !jobId} onStarted={setJobId} onClose={close} />
      {jobId ? (
        <ProgressDialog open={open} jobId={jobId} onBack={() => setJobId(null)} onClose={close} />
      ) : null}
    </>
  )
}

// --------------------------------------------------------------------------- //
//  Formulaire
// --------------------------------------------------------------------------- //
function FormDialog({
  open,
  onStarted,
  onClose,
}: {
  open: boolean
  onStarted: (jobId: string) => void
  onClose: () => void
}) {
  const [name, setName] = useState('')
  const [directory, setDirectory] = useState('')
  const [directoryEdited, setDirectoryEdited] = useState(false)
  const [distribution, setDistribution] = useState('paper')
  const [version, setVersion] = useState('')
  const [showSnapshots, setShowSnapshots] = useState(false)
  const [build, setBuild] = useState('')
  const [memoryMin, setMemoryMin] = useState('1024')
  const [memoryMax, setMemoryMax] = useState('4096')
  const [port, setPort] = useState('')
  const [portEdited, setPortEdited] = useState(false)
  const [acceptEula, setAcceptEula] = useState(false)
  const [startAfter, setStartAfter] = useState(false)

  const distributions = useQuery({
    queryKey: ['provisioning', 'distributions'],
    queryFn: () => api.provisioning.distributions(),
    enabled: open,
    staleTime: Infinity,
  })
  const current = distributions.data?.find((item) => item.key === distribution)

  const versions = useQuery({
    queryKey: ['provisioning', 'versions', distribution],
    queryFn: () => api.provisioning.versions(distribution),
    enabled: open,
    staleTime: 5 * 60_000,
  })
  const shownVersions = useMemo(
    () =>
      (versions.data ?? []).filter((item) => showSnapshots || item.channel === 'release'),
    [versions.data, showSnapshots],
  )

  const builds = useQuery({
    queryKey: ['provisioning', 'builds', distribution, version],
    queryFn: () => api.provisioning.builds(distribution, version),
    enabled: open && Boolean(current?.has_builds) && Boolean(version),
    staleTime: 5 * 60_000,
  })

  const debouncedName = useDebounced(name.trim())
  const defaults = useQuery({
    queryKey: ['provisioning', 'defaults', debouncedName],
    queryFn: () => api.provisioning.defaults(debouncedName),
    enabled: open,
  })

  // Le dossier et le port suivent les valeurs proposées tant qu'on n'y a pas touché.
  useEffect(() => {
    if (!directoryEdited && defaults.data) setDirectory(defaults.data.directory)
  }, [defaults.data, directoryEdited])
  useEffect(() => {
    if (!portEdited && defaults.data) setPort(String(defaults.data.port))
  }, [defaults.data, portEdited])

  // La version la plus récente de la liste, puis son build stable le plus récent.
  useEffect(() => {
    if (shownVersions.length && !shownVersions.some((item) => item.id === version)) {
      setVersion(shownVersions[0]!.id)
    }
  }, [shownVersions, version])
  useEffect(() => {
    const list = builds.data ?? []
    if (list.length && !list.some((item) => item.id === build)) {
      setBuild((list.find((item) => item.channel === 'release') ?? list[0]!).id)
    }
  }, [builds.data, build])

  const queryClient = useQueryClient()
  const create = useMutation({
    mutationFn: () =>
      api.provisioning.create({
        name: name.trim(),
        directory: directory.trim(),
        distribution,
        version,
        build: current?.has_builds ? build : null,
        memory_min_mb: Number(memoryMin),
        memory_max_mb: Number(memoryMax),
        port: Number(port),
        accept_eula: acceptEula,
        start_after: acceptEula && startAfter,
      }),
    onSuccess: (job) => {
      queryClient.setQueryData(['provisioning', 'job', job.id], job)
      onStarted(job.id)
    },
  })

  const reset = () => {
    setName('')
    setDirectory('')
    setDirectoryEdited(false)
    setDistribution('paper')
    setVersion('')
    setBuild('')
    setPort('')
    setPortEdited(false)
    setAcceptEula(false)
    setStartAfter(false)
    create.reset()
  }

  const ready =
    Boolean(name.trim() && directory.trim() && version && port) &&
    (!current?.has_builds || Boolean(build))

  return (
    <Dialog
      open={open}
      onClose={() => {
        reset()
        onClose()
      }}
      title={t('provision.title')}
      description={t('provision.description')}
      size="lg"
      footer={
        <>
          <Button
            variant="ghost"
            onClick={() => {
              reset()
              onClose()
            }}
          >
            {t('common.cancel')}
          </Button>
          <Button
            variant="primary"
            loading={create.isPending}
            disabled={!ready}
            onClick={() => create.mutate()}
          >
            {t('provision.submit')}
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label={t('provision.name')}>
            <Input
              value={name}
              onChange={(event) => setName(event.target.value)}
              placeholder={t('provision.namePlaceholder')}
              autoFocus
            />
          </Field>
          <Field label={t('provision.type')}>
            <Select
              value={distribution}
              onChange={(event) => {
                setDistribution(event.target.value)
                setVersion('')
                setBuild('')
              }}
            >
              {(distributions.data ?? []).map((item) => (
                <option key={item.key} value={item.key}>
                  {item.label}
                </option>
              ))}
            </Select>
          </Field>
        </div>

        <Field
          label={t('provision.directory')}
          hint={
            defaults.data?.roots.length
              ? t('provision.directoryRoots', { roots: defaults.data.roots.join(', ') })
              : t('provision.directoryHint')
          }
        >
          <Input
            value={directory}
            onChange={(event) => {
              setDirectoryEdited(true)
              setDirectory(event.target.value)
            }}
            placeholder="/data/minecraft/survie"
            spellCheck={false}
          />
        </Field>

        <div className="grid gap-4 sm:grid-cols-2">
          <Field label={t('provision.version')}>
            <Select
              value={version}
              disabled={versions.isLoading}
              onChange={(event) => {
                setVersion(event.target.value)
                setBuild('')
              }}
            >
              {versions.isLoading ? <option>{t('provision.loadingVersions')}</option> : null}
              {shownVersions.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.id}
                </option>
              ))}
            </Select>
          </Field>

          {current?.has_builds ? (
            <Field label={t('provision.build')}>
              <Select
                value={build}
                disabled={builds.isLoading || !version}
                onChange={(event) => setBuild(event.target.value)}
              >
                {builds.isLoading ? <option>{t('provision.loadingVersions')}</option> : null}
                {(builds.data ?? []).map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.channel === 'release'
                      ? item.label
                      : t('provision.buildBeta', { label: item.label })}
                  </option>
                ))}
              </Select>
            </Field>
          ) : (
            <div className="flex items-end pb-2">
              <Checkbox
                label={t('provision.showSnapshots')}
                checked={showSnapshots}
                onChange={(event) => setShowSnapshots(event.target.checked)}
              />
            </div>
          )}
        </div>

        {current?.has_builds ? (
          <Checkbox
            label={t('provision.showSnapshots')}
            checked={showSnapshots}
            onChange={(event) => setShowSnapshots(event.target.checked)}
          />
        ) : null}

        <ErrorPanel error={versions.error ?? builds.error} compact />

        {current?.kind === 'installer' ? (
          <Note>{t('provision.installerNote')}</Note>
        ) : distribution === 'fabric' ? (
          <Note>{t('provision.noChecksumNote')}</Note>
        ) : null}

        <div className="grid gap-4 sm:grid-cols-3">
          <Field label={t('provision.memoryMin')}>
            <Input
              type="number"
              min={512}
              step={512}
              value={memoryMin}
              onChange={(event) => setMemoryMin(event.target.value)}
            />
          </Field>
          <Field label={t('provision.memoryMax')}>
            <Input
              type="number"
              min={512}
              step={512}
              value={memoryMax}
              onChange={(event) => setMemoryMax(event.target.value)}
            />
          </Field>
          <Field label={t('provision.port')}>
            <Input
              type="number"
              min={1}
              max={65535}
              value={port}
              onChange={(event) => {
                setPortEdited(true)
                setPort(event.target.value)
              }}
            />
          </Field>
        </div>

        <div className="space-y-2.5 rounded-lg border border-slate-800 bg-slate-900/40 px-4 py-3">
          <Checkbox
            label={t('provision.eula')}
            hint={t('provision.eulaHint')}
            checked={acceptEula}
            onChange={(event) => setAcceptEula(event.target.checked)}
          />
          <a
            href={EULA_URL}
            target="_blank"
            rel="noreferrer"
            className="ml-6.5 inline-flex items-center gap-1 text-xs text-sky-400 hover:text-sky-300"
          >
            {t('provision.eulaLink')}
            <ExternalLink className="size-3" />
          </a>
          <Checkbox
            label={t('provision.startAfter')}
            hint={t('provision.startAfterHint')}
            checked={acceptEula && startAfter}
            disabled={!acceptEula}
            onChange={(event) => setStartAfter(event.target.checked)}
          />
        </div>

        <ErrorPanel error={create.error} />
      </div>
    </Dialog>
  )
}

function Note({ children }: { children: string }) {
  return (
    <p className="flex items-start gap-2 rounded-lg border border-sky-900/50 bg-sky-950/30 px-3.5 py-2.5 text-xs text-sky-100/90">
      <Info className="mt-0.5 size-3.5 shrink-0 text-sky-300" />
      {children}
    </p>
  )
}

// --------------------------------------------------------------------------- //
//  Suivi
// --------------------------------------------------------------------------- //
const STEP_ICONS: Record<ProvisioningStepStatus, typeof Circle> = {
  pending: Circle,
  running: Loader2,
  done: CheckCircle2,
  failed: XCircle,
  skipped: MinusCircle,
}

const STEP_COLORS: Record<ProvisioningStepStatus, string> = {
  pending: 'text-slate-600',
  running: 'animate-spin text-sky-400',
  done: 'text-emerald-400',
  failed: 'text-red-400',
  skipped: 'text-slate-500',
}

function ProgressDialog({
  open,
  jobId,
  onBack,
  onClose,
}: {
  open: boolean
  jobId: string
  onBack: () => void
  onClose: () => void
}) {
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const push = useToasts((state) => state.push)

  const job = useQuery({
    queryKey: ['provisioning', 'job', jobId],
    queryFn: () => api.provisioning.job(jobId),
    refetchInterval: (query) => (query.state.data?.status === 'RUNNING' ? 1000 : false),
  })
  const data: ProvisioningJob | undefined = job.data
  const status = data?.status

  // Une fois créé, le serveur apparaît dans la liste et le tableau de bord.
  useEffect(() => {
    if (status !== 'COMPLETED' || !data) return
    void queryClient.invalidateQueries({ queryKey: queryKeys.servers })
    void queryClient.invalidateQueries({ queryKey: queryKeys.dashboard })
    push({ kind: 'success', title: t('provision.created', { name: data.name }) })
    // Une seule annonce par création : l'état terminé ne change plus.
  }, [status])

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={t('provision.progressTitle', { name: data?.name ?? '' })}
      description={status === 'RUNNING' ? t('provision.progressHint') : data?.directory}
      footer={
        <>
          {status === 'FAILED' ? (
            <Button variant="ghost" onClick={onBack}>
              {t('provision.back')}
            </Button>
          ) : null}
          <Button variant={status === 'COMPLETED' ? 'ghost' : 'secondary'} onClick={onClose}>
            {t('common.close')}
          </Button>
          {status === 'COMPLETED' && data?.server_id ? (
            <Button
              variant="primary"
              onClick={() => {
                onClose()
                navigate(`/servers/${data.server_id}`)
              }}
            >
              {t('provision.open')}
            </Button>
          ) : null}
        </>
      }
    >
      <div className="space-y-4">
        <ErrorPanel error={job.error} />

        <ol className="space-y-2.5">
          {(data?.steps ?? []).map((step) => {
            const Icon = STEP_ICONS[step.status]
            return (
              <li key={step.key} className="flex items-start gap-3">
                <Icon className={cn('mt-0.5 size-4 shrink-0', STEP_COLORS[step.status])} />
                <div className="min-w-0 flex-1">
                  <p
                    className={cn(
                      'text-sm',
                      step.status === 'pending' ? 'text-slate-500' : 'text-slate-200',
                    )}
                  >
                    {step.label}
                  </p>
                  {step.detail ? (
                    <p className="truncate text-xs text-slate-500">{step.detail}</p>
                  ) : null}
                  {step.key === 'download' && step.status === 'running' ? (
                    <DownloadProgress job={data!} />
                  ) : null}
                </div>
              </li>
            )
          })}
        </ol>

        {status === 'FAILED' && data?.error ? (
          <div className="rounded-lg border border-red-900/60 bg-red-950/40 px-4 py-3 text-sm">
            <p className="font-medium text-red-200">{data.error.message}</p>
            {data.error.cause ? (
              <p className="mt-1 text-xs text-red-200/80">
                {t('common.cause')}
                {data.error.cause}
              </p>
            ) : null}
            {data.error.remediation ? (
              <p className="mt-1 text-xs text-red-200/80">
                {t('common.fix')}
                {data.error.remediation}
              </p>
            ) : null}
            <p className="mt-2 text-xs text-slate-400">{t('provision.failed')}</p>
          </div>
        ) : null}
      </div>
    </Dialog>
  )
}

function DownloadProgress({ job }: { job: ProvisioningJob }) {
  return (
    <div className="mt-1.5">
      {job.progress !== null ? (
        <div className="h-1 overflow-hidden rounded-full bg-slate-800">
          <div
            className="h-full rounded-full bg-sky-500 transition-[width] duration-300"
            style={{ width: `${Math.round(job.progress * 100)}%` }}
          />
        </div>
      ) : null}
      <p className="mt-1 text-[11px] text-slate-500">
        {t('provision.downloaded', { size: formatBytes(job.downloaded_bytes) })}
      </p>
    </div>
  )
}
