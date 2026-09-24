/**
 * Réglages.
 *
 * Chacun y choisit sa langue. L'admin y trouve en plus ce qui vaut pour tout le
 * panneau : langue par défaut, inscriptions et invitations, hébergement des
 * comptes (dossiers, ports, quotas), et le salon Discord global — qui n'annonce
 * que la création et la suppression de serveurs.
 */

import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Copy, Link2, Trash2 } from 'lucide-react'
import { api } from '@/lib/api'
import { hasPermission, useMe } from '@/hooks/useApi'
import { useToasts } from '@/stores/toasts'
import { copyText } from '@/lib/clipboard'
import { formatDateTime } from '@/lib/format'
import type { HostingSettings, Quota, RegistrationMode } from '@/lib/types'
import { Badge, Card, CardHeader, Field, Input, LoadingBlock, Select } from '@/components/ui/primitives'
import { Button } from '@/components/ui/Button'
import { ErrorPanel } from '@/components/common/ErrorPanel'
import { NotificationSettingsCard } from '@/components/notifications/NotificationSettingsCard'
import { UI_SETTINGS_KEY } from '@/components/common/LanguageBoundary'
import { LanguageCard } from './ProfilePage'
import { t, useLanguage, type Language, type MessageKey } from '@/i18n'

const LANGUAGE_NAMES: Record<Language, string> = { en: 'English', fr: 'Français' }
const MODES: RegistrationMode[] = ['closed', 'invite', 'open']

/** Langue par défaut du panneau : celle des comptes qui n'en ont pas choisi. */
function DefaultLanguageCard() {
  const queryClient = useQueryClient()
  const pushError = useToasts((state) => state.pushError)
  const { data: me } = useMe()
  const { data: ui } = useQuery({ queryKey: UI_SETTINGS_KEY, queryFn: () => api.ui.get() })

  const change = useMutation({
    mutationFn: (value: Language) => api.ui.setLanguage(value),
    onSuccess: (result) => {
      queryClient.setQueryData(UI_SETTINGS_KEY, result)
      void queryClient.invalidateQueries()
      // La langue de l'admin suit, sauf s'il en a choisi une pour lui-même.
      if (!me?.language) useLanguage.getState().setLanguage(result.language)
    },
    onError: (error) => pushError(error),
  })

  return (
    <Card>
      <CardHeader title={t('settings.languageTitle')} subtitle={t('settings.languageSubtitle')} />
      <div className="px-5 py-4">
        <Field label={t('settings.language')}>
          <Select
            value={ui?.language ?? 'en'}
            disabled={change.isPending}
            onChange={(event) => change.mutate(event.target.value as Language)}
          >
            {(Object.keys(LANGUAGE_NAMES) as Language[]).map((value) => (
              <option key={value} value={value}>
                {LANGUAGE_NAMES[value]}
              </option>
            ))}
          </Select>
        </Field>
      </div>
    </Card>
  )
}

function InvitationsList() {
  const queryClient = useQueryClient()
  const push = useToasts((state) => state.push)
  const pushError = useToasts((state) => state.pushError)
  const [note, setNote] = useState('')
  const [days, setDays] = useState(7)
  const [link, setLink] = useState<string | null>(null)

  const invitations = useQuery({ queryKey: ['invitations'], queryFn: () => api.users.invitations() })
  const invite = useMutation({
    mutationFn: () => api.users.invite(note.trim(), days),
    onSuccess: (created) => {
      setNote('')
      setLink(`${window.location.origin}/register?invite=${encodeURIComponent(created.token)}`)
      void queryClient.invalidateQueries({ queryKey: ['invitations'] })
    },
  })
  const revoke = useMutation({
    mutationFn: (id: number) => api.users.revokeInvitation(id),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['invitations'] }),
    onError: (error) => pushError(error),
  })

  const now = Date.now()
  return (
    <div className="space-y-3 border-t border-slate-800 px-5 py-4">
      <p className="text-xs font-medium text-slate-300">{t('platform.invitations')}</p>
      <form
        className="grid gap-3 sm:grid-cols-[1fr_8rem_auto] sm:items-end"
        onSubmit={(event) => {
          event.preventDefault()
          invite.mutate()
        }}
      >
        <Field label={t('platform.inviteNote')}>
          <Input value={note} maxLength={128} onChange={(event) => setNote(event.target.value)} />
        </Field>
        <Field label={t('platform.inviteDays')}>
          <Input
            type="number"
            min={1}
            max={30}
            value={days}
            onChange={(event) => setDays(Number(event.target.value) || 7)}
          />
        </Field>
        <Button type="submit" icon={<Link2 className="size-4" />} loading={invite.isPending}>
          {t('platform.invite')}
        </Button>
      </form>
      <ErrorPanel error={invite.error} />

      {link ? (
        <div className="rounded-lg border border-emerald-900/60 bg-emerald-950/30 p-3">
          <p className="text-xs text-emerald-200">{t('platform.inviteOnce')}</p>
          <div className="mt-2 flex gap-2">
            <Input readOnly value={link} className="font-mono text-xs" onFocus={(event) => event.target.select()} />
            <Button
              icon={<Copy className="size-4" />}
              onClick={() =>
                void copyText(link).then((copied) =>
                  push(copied ? { kind: 'success', title: t('platform.copied') } : { kind: 'error', title: t('platform.copyFailed') }),
                )
              }
            >
              {t('platform.copy')}
            </Button>
          </div>
        </div>
      ) : null}

      {invitations.data && invitations.data.length > 0 ? (
        <ul className="divide-y divide-slate-800/60 rounded-lg border border-slate-800">
          {invitations.data.map((item) => {
            const expired = new Date(item.expires_at).getTime() < now
            return (
              <li key={item.id} className="flex items-center gap-3 px-3 py-2 text-sm">
                <div className="min-w-0 flex-1">
                  <p className="truncate text-slate-200">{item.note || t('platform.inviteNoNote')}</p>
                  <p className="text-xs text-slate-500">
                    {t('platform.inviteExpires', { date: formatDateTime(item.expires_at) })}
                  </p>
                </div>
                {item.used_at ? (
                  <Badge className="bg-emerald-950/60 text-emerald-300 ring-emerald-800">{t('platform.inviteUsed')}</Badge>
                ) : expired ? (
                  <Badge>{t('platform.inviteExpired')}</Badge>
                ) : (
                  <Badge className="bg-sky-950/60 text-sky-300 ring-sky-800">{t('platform.invitePending')}</Badge>
                )}
                <Button
                  size="sm"
                  variant="ghost"
                  icon={<Trash2 className="size-3.5" />}
                  onClick={() => revoke.mutate(item.id)}
                  title={t('platform.inviteRevoke')}
                >
                  <span className="sr-only">{t('platform.inviteRevoke')}</span>
                </Button>
              </li>
            )
          })}
        </ul>
      ) : null}
    </div>
  )
}

function RegistrationCard() {
  const queryClient = useQueryClient()
  const pushError = useToasts((state) => state.pushError)
  const { data } = useQuery({ queryKey: ['registration-settings'], queryFn: () => api.platform.registration() })
  const change = useMutation({
    mutationFn: (mode: RegistrationMode) => api.platform.setRegistration(mode),
    onSuccess: (result) => {
      queryClient.setQueryData(['registration-settings'], result)
      void queryClient.invalidateQueries({ queryKey: ['registration'] })
    },
    onError: (error) => pushError(error),
  })

  return (
    <Card>
      <CardHeader title={t('platform.registrationTitle')} subtitle={t('platform.registrationSubtitle')} />
      <div className="px-5 py-4">
        <Field label={t('platform.registrationMode')} hint={data ? t(`platform.mode.${data.mode}.hint` as MessageKey) : undefined}>
          <Select
            value={data?.mode ?? 'closed'}
            disabled={!data || change.isPending}
            onChange={(event) => change.mutate(event.target.value as RegistrationMode)}
          >
            {MODES.map((mode) => (
              <option key={mode} value={mode}>
                {t(`platform.mode.${mode}` as MessageKey)}
              </option>
            ))}
          </Select>
        </Field>
      </div>
      <InvitationsList />
    </Card>
  )
}

const QUOTA_FIELDS: { key: keyof Quota; label: MessageKey }[] = [
  { key: 'max_servers', label: 'profile.limitServers' },
  { key: 'max_memory_per_server_mb', label: 'platform.quotaMemoryServer' },
  { key: 'max_memory_total_mb', label: 'platform.quotaMemoryTotal' },
  { key: 'max_disk_mb', label: 'platform.quotaDisk' },
]

function HostingCard() {
  const queryClient = useQueryClient()
  const push = useToasts((state) => state.push)
  const { data, isLoading } = useQuery({ queryKey: ['hosting'], queryFn: () => api.platform.hosting() })
  const [form, setForm] = useState<HostingSettings | null>(null)

  useEffect(() => {
    if (data) setForm(data)
  }, [data])

  const save = useMutation({
    mutationFn: (payload: HostingSettings) => api.platform.setHosting(payload),
    onSuccess: (result) => {
      queryClient.setQueryData(['hosting'], result)
      push({ kind: 'success', title: t('platform.hostingSaved') })
    },
  })

  if (isLoading || !form) return <Card><LoadingBlock /></Card>

  const setQuota = (key: keyof Quota, raw: string) =>
    setForm({ ...form, quota: { ...form.quota, [key]: raw.trim() === '' ? null : Number(raw) } })

  return (
    <Card>
      <CardHeader title={t('platform.hostingTitle')} subtitle={t('platform.hostingSubtitle')} />
      <form
        className="space-y-4 px-5 py-4"
        onSubmit={(event) => {
          event.preventDefault()
          const { users_root_effective: _ignored, ...payload } = form
          save.mutate({ ...payload, users_root: payload.users_root?.trim() || null })
        }}
      >
        <Field
          label={t('platform.usersRoot')}
          hint={t('platform.usersRootHint', { path: data?.users_root_effective ?? '' })}
        >
          <Input
            value={form.users_root ?? ''}
            placeholder={data?.users_root_effective ?? ''}
            onChange={(event) => setForm({ ...form, users_root: event.target.value })}
          />
        </Field>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label={t('platform.portMin')}>
            <Input
              type="number"
              min={1024}
              max={65535}
              value={form.port_min}
              onChange={(event) => setForm({ ...form, port_min: Number(event.target.value) })}
            />
          </Field>
          <Field label={t('platform.portMax')} hint={t('platform.portHint')}>
            <Input
              type="number"
              min={1024}
              max={65535}
              value={form.port_max}
              onChange={(event) => setForm({ ...form, port_max: Number(event.target.value) })}
            />
          </Field>
        </div>
        <div>
          <p className="mb-2 text-xs font-medium text-slate-300">{t('platform.quotaTitle')}</p>
          <div className="grid gap-3 sm:grid-cols-4">
            {QUOTA_FIELDS.map(({ key, label }) => (
              <Field key={key} label={t(label)}>
                <Input
                  type="number"
                  min={0}
                  placeholder={t('people.unlimited')}
                  value={form.quota[key] ?? ''}
                  onChange={(event) => setQuota(key, event.target.value)}
                />
              </Field>
            ))}
          </div>
          <p className="mt-2 text-xs text-slate-500">{t('platform.quotaHint')}</p>
        </div>
        <ErrorPanel error={save.error} />
        <Button type="submit" variant="primary" loading={save.isPending}>
          {t('common.save')}
        </Button>
      </form>
    </Card>
  )
}

export function SettingsPage() {
  const { data: me } = useMe()
  const admin = hasPermission(me, 'settings:manage')
  if (!me) return <LoadingBlock />

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-3xl space-y-5 p-4 sm:p-6">
        <LanguageCard me={me} />
        {admin ? (
          <>
            <DefaultLanguageCard />
            <RegistrationCard />
            <HostingCard />
            <NotificationSettingsCard
              title={t('settings.discordTitle')}
              subtitle={t('settings.discordSubtitle')}
              queryKey={['notifications']}
              load={() => api.notifications.get()}
              save={(payload) => api.notifications.update(payload)}
              sendTest={() => api.notifications.test()}
            />
          </>
        ) : null}
      </div>
    </div>
  )
}
