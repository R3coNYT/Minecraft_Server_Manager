/**
 * Fiche d'un compte, pour l'équipe de MSM : identité, pseudos successifs,
 * serveurs possédés et partagés, limites, et sanctions.
 *
 * Un modérateur consulte et bannit les users ; un admin gère aussi le rôle,
 * l'activation, les limites et la suppression.
 */

import { useEffect, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft, Ban, ShieldCheck, Trash2 } from 'lucide-react'
import { api } from '@/lib/api'
import { hasPermission, queryKeys, useMe } from '@/hooks/useApi'
import { useToasts } from '@/stores/toasts'
import { formatDateTime, formatMemory } from '@/lib/format'
import type { Quota, Role, UserDetail } from '@/lib/types'
import { Avatar } from '@/components/common/Avatar'
import { Badge, Card, CardHeader, Field, Input, LoadingBlock, Select } from '@/components/ui/primitives'
import { Button } from '@/components/ui/Button'
import { Dialog } from '@/components/ui/Dialog'
import { ErrorPanel } from '@/components/common/ErrorPanel'
import { ConfirmDialog } from '@/components/common/ConfirmDialog'
import { AccountState, RoleBadge, ROLES } from './UsersPage'
import { t, type MessageKey } from '@/i18n'

const QUOTA_FIELDS: { key: keyof Quota; label: MessageKey; unit: 'count' | 'mb' }[] = [
  { key: 'max_servers', label: 'profile.limitServers', unit: 'count' },
  { key: 'max_memory_per_server_mb', label: 'profile.limitMemoryServer', unit: 'mb' },
  { key: 'max_memory_total_mb', label: 'people.limitMemoryOnline', unit: 'mb' },
  { key: 'max_disk_mb', label: 'profile.limitDisk', unit: 'mb' },
]

function BanDialog({ user, open, onClose }: { user: UserDetail; open: boolean; onClose: () => void }) {
  const queryClient = useQueryClient()
  const push = useToasts((state) => state.push)
  const [reason, setReason] = useState('')
  const ban = useMutation({
    mutationFn: () => api.users.ban(user.id, reason.trim()),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['user', user.id] })
      void queryClient.invalidateQueries({ queryKey: queryKeys.users })
      push({ kind: 'success', title: t('people.bannedToast', { name: user.username }) })
      setReason('')
      onClose()
    },
  })
  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={t('people.banTitle', { name: user.username })}
      description={t('people.banDescription')}
      size="sm"
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            {t('common.cancel')}
          </Button>
          <Button variant="danger" loading={ban.isPending} disabled={!reason.trim()} onClick={() => ban.mutate()}>
            {t('people.ban')}
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        <Field label={t('people.banReason')} hint={t('people.banReasonHint')}>
          <Input value={reason} maxLength={1000} autoFocus onChange={(event) => setReason(event.target.value)} />
        </Field>
        <ErrorPanel error={ban.error} />
      </div>
    </Dialog>
  )
}

function QuotaCard({ user, editable }: { user: UserDetail; editable: boolean }) {
  const queryClient = useQueryClient()
  const push = useToasts((state) => state.push)
  const [values, setValues] = useState<Record<string, string>>({})

  useEffect(() => {
    const overrides = user.quota_overrides ?? {}
    setValues(
      Object.fromEntries(
        QUOTA_FIELDS.map(({ key }) => [key, overrides[key] == null ? '' : String(overrides[key])]),
      ),
    )
  }, [user.quota_overrides])

  const save = useMutation({
    mutationFn: (overrides: Partial<Quota>) => api.users.setQuota(user.id, overrides),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['user', user.id] })
      push({ kind: 'success', title: t('people.limitsSaved') })
    },
  })

  const limits = user.limits
  const effective = limits.quota
  const show = (key: keyof Quota, unit: 'count' | 'mb') => {
    const value = effective?.[key]
    if (value == null) return t('people.unlimited')
    return unit === 'mb' ? formatMemory(value) : String(value)
  }

  return (
    <Card>
      <CardHeader
        title={t('people.limitsTitle')}
        subtitle={effective ? t('people.limitsSubtitle') : t('people.noLimits')}
      />
      <dl className="grid gap-x-6 gap-y-1.5 px-5 py-4 text-sm sm:grid-cols-2">
        {[
          [t('profile.limitServers'), `${limits.usage.servers} / ${show('max_servers', 'count')}`],
          [
            t('people.limitMemoryOnline'),
            `${formatMemory(limits.usage.memory_online_mb)} / ${show('max_memory_total_mb', 'mb')}`,
          ],
          [t('profile.limitMemoryServer'), show('max_memory_per_server_mb', 'mb')],
          [t('profile.limitDisk'), `${formatMemory(limits.usage.disk_mb)} / ${show('max_disk_mb', 'mb')}`],
        ].map(([label, value]) => (
          <div key={label} className="flex justify-between gap-3">
            <dt className="text-slate-400">{label}</dt>
            <dd className="tabular-nums text-slate-200">{value}</dd>
          </div>
        ))}
      </dl>
      {editable && effective ? (
        <form
          className="border-t border-slate-800 px-5 py-4"
          onSubmit={(event) => {
            event.preventDefault()
            const overrides: Partial<Quota> = {}
            for (const { key } of QUOTA_FIELDS) {
              const raw = values[key]?.trim()
              if (raw) overrides[key] = Number(raw)
            }
            save.mutate(overrides)
          }}
        >
          <p className="mb-3 text-xs text-slate-500">{t('people.overridesHint')}</p>
          <div className="grid gap-3 sm:grid-cols-4">
            {QUOTA_FIELDS.map(({ key, label, unit }) => (
              <Field key={key} label={`${t(label)}${unit === 'mb' ? ' (MB)' : ''}`}>
                <Input
                  type="number"
                  min={0}
                  placeholder={t('people.default')}
                  value={values[key] ?? ''}
                  onChange={(event) => setValues((current) => ({ ...current, [key]: event.target.value }))}
                />
              </Field>
            ))}
          </div>
          <ErrorPanel error={save.error} className="mt-3" />
          <div className="mt-3 flex gap-2">
            <Button type="submit" size="sm" variant="primary" loading={save.isPending}>
              {t('common.save')}
            </Button>
            <Button type="button" size="sm" variant="ghost" onClick={() => save.mutate({})}>
              {t('people.resetLimits')}
            </Button>
          </div>
        </form>
      ) : null}
    </Card>
  )
}

export function UserDetailPage() {
  const params = useParams<{ userId: string }>()
  const userId = Number(params.userId)
  const { data: me } = useMe()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const pushError = useToasts((state) => state.pushError)
  const [banOpen, setBanOpen] = useState(false)
  const [deleteOpen, setDeleteOpen] = useState(false)

  const { data: user, isLoading, error } = useQuery({
    queryKey: ['user', userId],
    queryFn: () => api.users.get(userId),
  })
  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: ['user', userId] })
    void queryClient.invalidateQueries({ queryKey: queryKeys.users })
  }

  const update = useMutation({
    mutationFn: (payload: Record<string, unknown>) => api.users.update(userId, payload),
    onSuccess: refresh,
    onError: (mutationError) => pushError(mutationError),
  })
  const unban = useMutation({
    mutationFn: () => api.users.unban(userId),
    onSuccess: refresh,
    onError: (mutationError) => pushError(mutationError),
  })
  const remove = useMutation({
    mutationFn: () => api.users.remove(userId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.users })
      navigate('/users', { replace: true })
    },
  })

  if (isLoading) return <LoadingBlock />
  if (error || !user) {
    return (
      <div className="p-6">
        <ErrorPanel error={error} />
      </div>
    )
  }

  const isSelf = user.id === me?.id
  const canManage = hasPermission(me, 'user:manage')
  // Un modérateur sanctionne les users ; un admin, tout le monde sauf lui-même.
  const canBan = !isSelf && hasPermission(me, 'user:ban') && (canManage || user.role === 'USER')

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-4xl space-y-5 p-4 sm:p-6">
        <Link to="/users" className="inline-flex items-center gap-1.5 text-sm text-slate-400 hover:text-slate-200">
          <ArrowLeft className="size-4" />
          {t('users.title')}
        </Link>

        <Card>
          <div className="flex flex-col gap-4 px-5 py-5 sm:flex-row sm:items-center">
            <Avatar url={user.avatar_url} name={user.username} size="lg" />
            <div className="min-w-0 flex-1 space-y-1">
              <div className="flex flex-wrap items-center gap-2">
                <h1 className="truncate text-lg font-semibold text-slate-100">{user.username}</h1>
                <RoleBadge role={user.role} />
                <AccountState user={user} />
              </div>
              <p className="text-sm text-slate-400">{user.email ?? t('people.noEmail')}</p>
              <p className="text-xs text-slate-500">
                {t('people.joined', { date: formatDateTime(user.created_at) })} ·{' '}
                {t('people.lastSeen', { date: formatDateTime(user.last_login_at) })} ·{' '}
                {t('profile.storage', { id: user.storage_id })}
              </p>
            </div>
          </div>
          {user.banned_at ? (
            <div className="border-t border-red-900/60 bg-red-950/30 px-5 py-3 text-sm text-red-200">
              {t('people.bannedSince', { date: formatDateTime(user.banned_at) })}
              {user.ban_reason ? ` — ${user.ban_reason}` : ''}
            </div>
          ) : null}
        </Card>

        {canBan || canManage ? (
          <Card>
            <CardHeader title={t('people.actionsTitle')} />
            <div className="flex flex-wrap items-end gap-3 px-5 py-4">
              {canManage && !isSelf ? (
                <Field label={t('users.role')}>
                  <Select
                    className="w-44"
                    value={user.role}
                    disabled={update.isPending}
                    onChange={(event) => update.mutate({ role: event.target.value as Role })}
                  >
                    {ROLES.map((value) => (
                      <option key={value} value={value}>
                        {t(`role.${value}`)}
                      </option>
                    ))}
                  </Select>
                </Field>
              ) : null}
              {canManage && !isSelf ? (
                <Button onClick={() => update.mutate({ is_active: !user.is_active })} loading={update.isPending}>
                  {user.is_active ? t('people.disable') : t('people.enable')}
                </Button>
              ) : null}
              {canBan && !user.banned_at ? (
                <Button variant="danger" icon={<Ban className="size-4" />} onClick={() => setBanOpen(true)}>
                  {t('people.ban')}
                </Button>
              ) : null}
              {canBan && user.banned_at ? (
                <Button icon={<ShieldCheck className="size-4" />} loading={unban.isPending} onClick={() => unban.mutate()}>
                  {t('people.unban')}
                </Button>
              ) : null}
              {canManage && !isSelf ? (
                <Button variant="ghost" icon={<Trash2 className="size-4" />} onClick={() => setDeleteOpen(true)}>
                  {t('common.delete')}
                </Button>
              ) : null}
            </div>
          </Card>
        ) : null}

        <div className="grid gap-5 md:grid-cols-2">
          <Card>
            <CardHeader title={t('people.serversOwned')} />
            {user.servers_owned.length === 0 ? (
              <p className="px-5 py-4 text-sm text-slate-500">{t('people.none')}</p>
            ) : (
              <ul className="divide-y divide-slate-800/60">
                {user.servers_owned.map((server) => (
                  <li key={server.id} className="px-5 py-2.5 text-sm">
                    <Link to={`/servers/${server.id}`} className="text-slate-200 hover:text-emerald-400">
                      {server.name}
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </Card>
          <Card>
            <CardHeader title={t('people.serversShared')} />
            {user.servers_shared.length === 0 ? (
              <p className="px-5 py-4 text-sm text-slate-500">{t('people.none')}</p>
            ) : (
              <ul className="divide-y divide-slate-800/60">
                {user.servers_shared.map((server) => (
                  <li key={server.id} className="flex items-center justify-between gap-2 px-5 py-2.5 text-sm">
                    <Link to={`/servers/${server.id}`} className="truncate text-slate-200 hover:text-emerald-400">
                      {server.name}
                    </Link>
                    {server.role ? <Badge>{t(`members.role.${server.role}` as MessageKey)}</Badge> : null}
                  </li>
                ))}
              </ul>
            )}
          </Card>
        </div>

        <QuotaCard user={user} editable={canManage} />

        <Card>
          <CardHeader title={t('people.historyTitle')} subtitle={t('people.historySubtitle')} />
          {user.username_history.length === 0 ? (
            <p className="px-5 py-4 text-sm text-slate-500">{t('people.noHistory')}</p>
          ) : (
            <ul className="divide-y divide-slate-800/60">
              {user.username_history.map((item) => (
                <li key={`${item.username}-${item.changed_at}`} className="flex justify-between gap-3 px-5 py-2.5 text-sm">
                  <span className="text-slate-200">{item.username}</span>
                  <span className="text-xs text-slate-500">
                    {t('people.renamedOn', { date: formatDateTime(item.changed_at) })}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>

      <BanDialog user={user} open={banOpen} onClose={() => setBanOpen(false)} />
      <ConfirmDialog
        open={deleteOpen}
        title={t('users.deleteTitle', { name: user.username })}
        consequence={t('users.deleteConsequence')}
        confirmLabel={t('common.delete')}
        danger
        loading={remove.isPending}
        error={remove.error}
        onConfirm={() => remove.mutate()}
        onClose={() => setDeleteOpen(false)}
      />
    </div>
  )
}
