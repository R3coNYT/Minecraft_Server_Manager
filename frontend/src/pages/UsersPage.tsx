/**
 * Comptes du panneau : consultés par l'équipe de MSM (admins et modérateurs),
 * créés par les admins. Un clic sur un compte ouvre sa fiche : pseudos
 * successifs, serveurs, limites, bannissement.
 */

import { useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Plus, Search } from 'lucide-react'
import { api } from '@/lib/api'
import { hasPermission, queryKeys, useMe } from '@/hooks/useApi'
import { useToasts } from '@/stores/toasts'
import { formatDateTime } from '@/lib/format'
import type { Role, User } from '@/lib/types'
import { Avatar } from '@/components/common/Avatar'
import { Badge, Card, CardHeader, Field, Input, LoadingBlock, Select } from '@/components/ui/primitives'
import { Button } from '@/components/ui/Button'
import { Dialog } from '@/components/ui/Dialog'
import { ErrorPanel } from '@/components/common/ErrorPanel'
import { t, tn } from '@/i18n'

export const ROLES: Role[] = ['ADMIN', 'MODERATOR', 'USER']

export function RoleBadge({ role }: { role: Role }) {
  const tone: Record<Role, string> = {
    ADMIN: 'bg-emerald-950/60 text-emerald-300 ring-emerald-800',
    MODERATOR: 'bg-sky-950/60 text-sky-300 ring-sky-800',
    USER: 'bg-slate-800 text-slate-300 ring-slate-700',
  }
  return <Badge className={tone[role]}>{t(`role.${role}`)}</Badge>
}

export function AccountState({ user }: { user: User }) {
  if (user.banned_at) {
    return <Badge className="bg-red-950/60 text-red-300 ring-red-900">{t('people.banned')}</Badge>
  }
  if (!user.is_active) return <Badge>{t('people.disabled')}</Badge>
  return <span className="text-xs text-slate-500">{t('people.active')}</span>
}

function CreateAccountDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const queryClient = useQueryClient()
  const push = useToasts((state) => state.push)
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [role, setRole] = useState<Role>('USER')

  const create = useMutation({
    mutationFn: () => api.users.create({ username, password, role }),
    onSuccess: (user) => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.users })
      push({ kind: 'success', title: t('users.created', { name: user.username }) })
      setUsername('')
      setPassword('')
      setRole('USER')
      onClose()
    },
  })

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={t('users.new')}
      size="sm"
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            {t('common.cancel')}
          </Button>
          <Button
            variant="primary"
            loading={create.isPending}
            disabled={!username.trim() || password.length < 10}
            onClick={() => create.mutate()}
          >
            {t('users.create')}
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <Field label={t('users.username')}>
          <Input value={username} onChange={(event) => setUsername(event.target.value)} autoFocus />
        </Field>
        <Field label={t('users.password')} hint={t('users.passwordHint')}>
          <Input type="password" value={password} onChange={(event) => setPassword(event.target.value)} />
        </Field>
        <Field label={t('users.role')} hint={t(`users.hint.${role}`)}>
          <Select value={role} onChange={(event) => setRole(event.target.value as Role)}>
            {ROLES.map((value) => (
              <option key={value} value={value}>
                {t(`role.${value}`)}
              </option>
            ))}
          </Select>
        </Field>
        <ErrorPanel error={create.error} />
      </div>
    </Dialog>
  )
}

export function UsersPage() {
  const { data: me } = useMe()
  const navigate = useNavigate()
  const [createOpen, setCreateOpen] = useState(false)
  const [query, setQuery] = useState('')

  const { data: users, isLoading, error } = useQuery({
    queryKey: queryKeys.users,
    queryFn: () => api.users.list(),
  })

  const shown = useMemo(() => {
    const needle = query.trim().toLowerCase()
    if (!needle) return users ?? []
    return (users ?? []).filter(
      (user) =>
        user.username.toLowerCase().includes(needle) ||
        (user.email ?? '').toLowerCase().includes(needle),
    )
  }, [users, query])

  if (isLoading) return <LoadingBlock />

  return (
    <div className="mx-auto max-w-5xl space-y-5 p-4 sm:p-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold text-slate-100">{t('users.title')}</h1>
          <p className="text-sm text-slate-500">{t('people.subtitle')}</p>
        </div>
        {hasPermission(me, 'user:manage') ? (
          <Button variant="primary" icon={<Plus className="size-4" />} onClick={() => setCreateOpen(true)}>
            {t('users.new')}
          </Button>
        ) : null}
      </div>

      <ErrorPanel error={error} />

      <Card>
        <CardHeader
          title={tn('users.count', users?.length ?? 0)}
          action={
            <div className="relative w-56">
              <Search className="pointer-events-none absolute left-2.5 top-2.5 size-3.5 text-slate-500" />
              <Input
                className="h-8 pl-8 text-xs"
                placeholder={t('people.search')}
                value={query}
                onChange={(event) => setQuery(event.target.value)}
              />
            </div>
          }
        />
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-slate-800 text-left text-xs text-slate-500">
              <th className="px-5 py-2.5 font-medium">{t('users.user')}</th>
              <th className="px-5 py-2.5 font-medium">{t('users.role')}</th>
              <th className="px-5 py-2.5 font-medium">{t('people.state')}</th>
              <th className="hidden px-5 py-2.5 font-medium sm:table-cell">{t('users.lastLogin')}</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-800/60">
            {shown.map((user) => (
              <tr
                key={user.id}
                className="cursor-pointer hover:bg-slate-800/40"
                onClick={() => navigate(`/users/${user.id}`)}
              >
                <td className="px-5 py-2.5">
                  <div className="flex items-center gap-2.5">
                    <Avatar url={user.avatar_url} name={user.username} />
                    <div className="min-w-0">
                      <p className="truncate text-slate-200">
                        {user.username}
                        {user.id === me?.id ? (
                          <span className="ml-2 text-xs text-slate-600">{t('users.you')}</span>
                        ) : null}
                      </p>
                      {user.email ? <p className="truncate text-xs text-slate-500">{user.email}</p> : null}
                    </div>
                  </div>
                </td>
                <td className="px-5 py-2.5">
                  <RoleBadge role={user.role} />
                </td>
                <td className="px-5 py-2.5">
                  <AccountState user={user} />
                </td>
                <td className="hidden px-5 py-2.5 text-xs text-slate-500 sm:table-cell">
                  {formatDateTime(user.last_login_at)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>

      <CreateAccountDialog open={createOpen} onClose={() => setCreateOpen(false)} />
    </div>
  )
}
