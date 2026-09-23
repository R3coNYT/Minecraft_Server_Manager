/** Gestion des comptes du panneau (administrateurs uniquement). */

import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Plus, Trash2 } from 'lucide-react'
import { api } from '@/lib/api'
import { queryKeys, useMe } from '@/hooks/useApi'
import { useToasts } from '@/stores/toasts'
import { formatDateTime } from '@/lib/format'
import type { Role, User } from '@/lib/types'
import { Card, CardHeader, Field, Input, LoadingBlock, Select } from '@/components/ui/primitives'
import { Button } from '@/components/ui/Button'
import { Dialog } from '@/components/ui/Dialog'
import { ErrorPanel } from '@/components/common/ErrorPanel'
import { ConfirmDialog } from '@/components/common/ConfirmDialog'
import { t, tn } from '@/i18n'

const ROLES: Role[] = ['ADMIN', 'MODERATOR', 'VIEWER']

export function UsersPage() {
  const queryClient = useQueryClient()
  const { data: me } = useMe()
  const push = useToasts((state) => state.push)
  const pushError = useToasts((state) => state.pushError)

  const [createOpen, setCreateOpen] = useState(false)
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [role, setRole] = useState<Role>('VIEWER')
  const [toDelete, setToDelete] = useState<User | null>(null)

  const { data: users, isLoading, error } = useQuery({
    queryKey: queryKeys.users,
    queryFn: () => api.users.list(),
  })

  const invalidate = () => void queryClient.invalidateQueries({ queryKey: queryKeys.users })

  const create = useMutation({
    mutationFn: () => api.users.create({ username, password, role }),
    onSuccess: (user) => {
      invalidate()
      push({ kind: 'success', title: t('users.created', { name: user.username }) })
      setCreateOpen(false)
      setUsername('')
      setPassword('')
      setRole('VIEWER')
    },
  })

  const update = useMutation({
    mutationFn: ({ id, payload }: { id: number; payload: Record<string, unknown> }) =>
      api.users.update(id, payload),
    onSuccess: () => {
      invalidate()
      push({ kind: 'success', title: t('users.updated') })
    },
    onError: (mutationError) => pushError(mutationError),
  })

  const remove = useMutation({
    mutationFn: (id: number) => api.users.remove(id),
    onSuccess: () => {
      invalidate()
      push({ kind: 'success', title: t('users.deleted') })
      setToDelete(null)
    },
    onError: (mutationError) => pushError(mutationError),
  })

  if (isLoading) return <LoadingBlock />

  return (
    <div className="mx-auto max-w-5xl space-y-5 p-4 sm:p-6">
      <div className="flex items-center justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold text-slate-100">{t('users.title')}</h1>
          <p className="text-sm text-slate-500">
            {t('users.subtitle')}
          </p>
        </div>
        <Button variant="primary" icon={<Plus className="size-4" />} onClick={() => setCreateOpen(true)}>
          {t('users.new')}
        </Button>
      </div>

      <ErrorPanel error={error} />

      <Card>
        <CardHeader title={tn('users.count', users?.length ?? 0)} />
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-slate-800 text-left text-xs text-slate-500">
              <th className="px-5 py-2.5 font-medium">{t('users.user')}</th>
              <th className="px-5 py-2.5 font-medium">{t('users.role')}</th>
              <th className="px-5 py-2.5 font-medium">{t('users.lastLogin')}</th>
              <th className="px-5 py-2.5 font-medium">{t('users.active')}</th>
              <th className="px-5 py-2.5" />
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-800/60">
            {(users ?? []).map((user) => {
              const isSelf = user.id === me?.id
              return (
                <tr key={user.id}>
                  <td className="px-5 py-2.5">
                    <span className="text-slate-200">{user.username}</span>
                    {isSelf ? <span className="ml-2 text-xs text-slate-600">{t('users.you')}</span> : null}
                  </td>
                  <td className="px-5 py-2.5">
                    <Select
                      className="h-8 w-44 py-0 text-xs"
                      value={user.role}
                      disabled={isSelf || update.isPending}
                      onChange={(event) =>
                        update.mutate({ id: user.id, payload: { role: event.target.value } })
                      }
                    >
                      {ROLES.map((value) => (
                        <option key={value} value={value}>
                          {t(`role.${value}`)}
                        </option>
                      ))}
                    </Select>
                  </td>
                  <td className="px-5 py-2.5 text-xs text-slate-500">
                    {formatDateTime(user.last_login_at)}
                  </td>
                  <td className="px-5 py-2.5">
                    <input
                      type="checkbox"
                      className="size-4 rounded border-slate-600 bg-slate-900 text-emerald-600"
                      checked={user.is_active}
                      disabled={isSelf || update.isPending}
                      onChange={(event) =>
                        update.mutate({
                          id: user.id,
                          payload: { is_active: event.target.checked },
                        })
                      }
                    />
                  </td>
                  <td className="px-5 py-2.5 text-right">
                    <Button
                      size="sm"
                      variant="ghost"
                      icon={<Trash2 className="size-3.5" />}
                      disabled={isSelf}
                      onClick={() => setToDelete(user)}
                    >
                      <span className="sr-only">{t('common.delete')}</span>
                    </Button>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </Card>

      <Dialog
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        title={t('users.new')}
        size="sm"
        footer={
          <>
            <Button variant="ghost" onClick={() => setCreateOpen(false)}>
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
            <Input
              type="password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
            />
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

      <ConfirmDialog
        open={toDelete !== null}
        title={t('users.deleteTitle', { name: toDelete?.username ?? '' })}
        consequence={t('users.deleteConsequence')}
        confirmLabel={t('common.delete')}
        danger
        loading={remove.isPending}
        onConfirm={() => toDelete && remove.mutate(toDelete.id)}
        onClose={() => setToDelete(null)}
      />
    </div>
  )
}
