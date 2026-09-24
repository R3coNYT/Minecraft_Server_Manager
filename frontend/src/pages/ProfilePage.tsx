/**
 * Profil du compte connecté : avatar, pseudo, langue, adresse, mot de passe, et
 * limites d'hébergement. On y arrive en cliquant sur son pseudo, en haut à droite.
 */

import { useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ImageUp, Trash2 } from 'lucide-react'
import { api } from '@/lib/api'
import { queryKeys, useMe } from '@/hooks/useApi'
import { useToasts } from '@/stores/toasts'
import { formatMemory } from '@/lib/format'
import type { Me } from '@/lib/types'
import { Avatar } from '@/components/common/Avatar'
import { Button } from '@/components/ui/Button'
import { Card, CardHeader, Field, Input, LoadingBlock, Select } from '@/components/ui/primitives'
import { ErrorPanel } from '@/components/common/ErrorPanel'
import { t, useLanguage, type MessageKey } from '@/i18n'

const LANGUAGES = [
  { value: '', label: 'profile.languageDefault' },
  { value: 'en', label: 'English' },
  { value: 'fr', label: 'Français' },
] as const

function useSaveMe() {
  const queryClient = useQueryClient()
  return (me: Me) => queryClient.setQueryData(queryKeys.me, me)
}

function IdentityCard({ me }: { me: Me }) {
  const saveMe = useSaveMe()
  const push = useToasts((state) => state.push)
  const pushError = useToasts((state) => state.pushError)
  const fileInput = useRef<HTMLInputElement>(null)
  const [username, setUsername] = useState(me.username)

  const upload = useMutation({
    mutationFn: (file: File) => api.auth.uploadAvatar(file),
    onSuccess: (updated) => {
      saveMe(updated)
      push({ kind: 'success', title: t('profile.avatarSaved') })
    },
    onError: (error) => pushError(error),
  })
  const remove = useMutation({
    mutationFn: () => api.auth.removeAvatar(),
    onSuccess: saveMe,
    onError: (error) => pushError(error),
  })
  const rename = useMutation({
    mutationFn: () => api.auth.updateProfile({ username: username.trim() }),
    onSuccess: (updated) => {
      saveMe(updated)
      push({ kind: 'success', title: t('profile.renamed', { name: updated.username }) })
    },
  })

  return (
    <Card>
      <CardHeader title={t('profile.identityTitle')} subtitle={t('profile.identitySubtitle')} />
      <div className="flex flex-col gap-5 px-5 py-4 sm:flex-row sm:items-start">
        <div className="flex flex-col items-center gap-2">
          <Avatar url={me.avatar_url} name={me.username} size="lg" />
          <input
            ref={fileInput}
            type="file"
            accept="image/png,image/jpeg,image/webp"
            className="hidden"
            onChange={(event) => {
              const file = event.target.files?.[0]
              if (file) upload.mutate(file)
              event.target.value = ''
            }}
          />
          <div className="flex gap-1">
            <Button
              size="sm"
              icon={<ImageUp className="size-3.5" />}
              loading={upload.isPending}
              onClick={() => fileInput.current?.click()}
            >
              {t('profile.avatarChange')}
            </Button>
            {me.avatar_url ? (
              <Button
                size="sm"
                variant="ghost"
                icon={<Trash2 className="size-3.5" />}
                loading={remove.isPending}
                onClick={() => remove.mutate()}
                title={t('profile.avatarRemove')}
              >
                <span className="sr-only">{t('profile.avatarRemove')}</span>
              </Button>
            ) : null}
          </div>
          <p className="max-w-40 text-center text-[11px] text-slate-600">{t('profile.avatarHint')}</p>
        </div>

        <form
          className="min-w-0 flex-1 space-y-3"
          onSubmit={(event) => {
            event.preventDefault()
            if (username.trim() && username.trim() !== me.username) rename.mutate()
          }}
        >
          <Field label={t('profile.username')} hint={t('profile.usernameHint')}>
            <Input value={username} maxLength={24} onChange={(event) => setUsername(event.target.value)} />
          </Field>
          <ErrorPanel error={rename.error} />
          <Button
            type="submit"
            variant="primary"
            size="sm"
            loading={rename.isPending}
            disabled={!username.trim() || username.trim() === me.username}
          >
            {t('profile.rename')}
          </Button>
          <p className="text-xs text-slate-600">
            {t('profile.storage', { id: me.storage_id })}
          </p>
        </form>
      </div>
    </Card>
  )
}

export function LanguageCard({ me }: { me: Me }) {
  const saveMe = useSaveMe()
  const queryClient = useQueryClient()
  const pushError = useToasts((state) => state.pushError)
  const change = useMutation({
    mutationFn: (value: string) => api.auth.updateProfile({ language: value || null }),
    onSuccess: (updated) => {
      saveMe(updated)
      // Les textes venus du serveur changent aussi de langue.
      void queryClient.invalidateQueries({ predicate: (query) => query.queryKey[0] !== 'me' })
      if (!updated.language) {
        const panel = queryClient.getQueryData<{ language: 'en' | 'fr' }>(['ui-settings'])
        if (panel) useLanguage.getState().setLanguage(panel.language)
      }
    },
    onError: (error) => pushError(error),
  })

  return (
    <Card>
      <CardHeader title={t('profile.languageTitle')} subtitle={t('profile.languageSubtitle')} />
      <div className="px-5 py-4">
        <Field label={t('profile.language')}>
          <Select
            value={me.language ?? ''}
            disabled={change.isPending}
            onChange={(event) => change.mutate(event.target.value)}
          >
            {LANGUAGES.map((item) => (
              <option key={item.value} value={item.value}>
                {item.value ? item.label : t(item.label as MessageKey)}
              </option>
            ))}
          </Select>
        </Field>
      </div>
    </Card>
  )
}

function EmailCard({ me }: { me: Me }) {
  const saveMe = useSaveMe()
  const push = useToasts((state) => state.push)
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const change = useMutation({
    mutationFn: () => api.auth.changeEmail(email.trim(), password),
    onSuccess: (updated) => {
      saveMe(updated)
      setEmail('')
      setPassword('')
      push({ kind: 'success', title: t('profile.emailSaved') })
    },
  })

  return (
    <Card>
      <CardHeader
        title={t('profile.emailTitle')}
        subtitle={me.email ? t('profile.emailCurrent', { email: me.email }) : t('profile.emailNone')}
      />
      <form
        className="grid gap-3 px-5 py-4 sm:grid-cols-2"
        onSubmit={(event) => {
          event.preventDefault()
          if (email.trim() && password) change.mutate()
        }}
      >
        <Field label={t('profile.emailNew')}>
          <Input type="email" value={email} onChange={(event) => setEmail(event.target.value)} />
        </Field>
        <Field label={t('profile.currentPassword')}>
          <Input
            type="password"
            value={password}
            autoComplete="current-password"
            onChange={(event) => setPassword(event.target.value)}
          />
        </Field>
        <div className="sm:col-span-2">
          <ErrorPanel error={change.error} />
          <Button
            type="submit"
            size="sm"
            variant="primary"
            className="mt-2"
            loading={change.isPending}
            disabled={!email.trim() || !password}
          >
            {t('profile.emailSave')}
          </Button>
        </div>
      </form>
    </Card>
  )
}

function PasswordCard() {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const [confirm, setConfirm] = useState('')
  const change = useMutation({
    mutationFn: () => api.auth.changePassword(current, next),
    onSuccess: () => {
      // Toutes les sessions sont fermées, celle-ci comprise : retour à la connexion.
      queryClient.setQueryData(queryKeys.me, null)
      queryClient.clear()
      navigate('/login', { replace: true })
    },
  })
  const mismatch = confirm.length > 0 && confirm !== next

  return (
    <Card>
      <CardHeader title={t('profile.passwordTitle')} subtitle={t('profile.passwordSubtitle')} />
      <form
        className="grid gap-3 px-5 py-4 sm:grid-cols-3"
        onSubmit={(event) => {
          event.preventDefault()
          if (current && next && next === confirm) change.mutate()
        }}
      >
        <Field label={t('profile.currentPassword')}>
          <Input
            type="password"
            value={current}
            autoComplete="current-password"
            onChange={(event) => setCurrent(event.target.value)}
          />
        </Field>
        <Field label={t('profile.newPassword')} hint={t('register.passwordHint')}>
          <Input
            type="password"
            value={next}
            autoComplete="new-password"
            onChange={(event) => setNext(event.target.value)}
          />
        </Field>
        <Field label={t('register.confirm')} error={mismatch ? t('register.mismatch') : undefined}>
          <Input
            type="password"
            value={confirm}
            autoComplete="new-password"
            onChange={(event) => setConfirm(event.target.value)}
          />
        </Field>
        <div className="sm:col-span-3">
          <ErrorPanel error={change.error} />
          <Button
            type="submit"
            size="sm"
            variant="primary"
            className="mt-2"
            loading={change.isPending}
            disabled={!current || !next || next !== confirm}
          >
            {t('profile.passwordSave')}
          </Button>
        </div>
      </form>
    </Card>
  )
}

/** `used` à `null` : une limite sans consommation (la mémoire par serveur). */
function LimitRow({ label, used, limit }: { label: string; used: string | null; limit: string | null }) {
  const text =
    used === null ? (limit ?? t('people.unlimited')) : limit === null ? used : t('profile.limitOf', { used, limit })
  return (
    <div className="flex items-center justify-between gap-3 px-5 py-2.5 text-sm">
      <span className="text-slate-400">{label}</span>
      <span className="tabular-nums text-slate-200">{text}</span>
    </div>
  )
}

function LimitsCard() {
  const { data } = useQuery({ queryKey: ['my-quota'], queryFn: () => api.auth.quota() })
  if (!data?.quota) return null
  const { quota, usage } = data
  const mb = (value: number | null) => (value === null ? null : formatMemory(value))

  return (
    <Card>
      <CardHeader title={t('profile.limitsTitle')} subtitle={t('profile.limitsSubtitle')} />
      <div className="divide-y divide-slate-800/60">
        <LimitRow
          label={t('profile.limitServers')}
          used={String(usage.servers)}
          limit={quota.max_servers === null ? null : String(quota.max_servers)}
        />
        <LimitRow
          label={t('profile.limitMemory')}
          used={formatMemory(usage.memory_online_mb)}
          limit={mb(quota.max_memory_total_mb)}
        />
        <LimitRow
          label={t('profile.limitMemoryServer')}
          used={null}
          limit={mb(quota.max_memory_per_server_mb)}
        />
        <LimitRow
          label={t('profile.limitDisk')}
          used={formatMemory(usage.disk_mb)}
          limit={mb(quota.max_disk_mb)}
        />
      </div>
    </Card>
  )
}

export function ProfilePage() {
  const { data: me } = useMe()
  if (!me) return <LoadingBlock />
  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-3xl space-y-5 p-4 sm:p-6">
        <div>
          <h1 className="text-lg font-semibold text-slate-100">{t('profile.title')}</h1>
          <p className="text-sm text-slate-500">{t(`role.${me.role}` as MessageKey)}</p>
        </div>
        <IdentityCard me={me} />
        <LimitsCard />
        <LanguageCard me={me} />
        <EmailCard me={me} />
        <PasswordCard />
      </div>
    </div>
  )
}
