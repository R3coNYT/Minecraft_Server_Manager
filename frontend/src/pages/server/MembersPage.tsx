/**
 * Partage du serveur, réservé à son propriétaire : qui y a accès, et avec quel
 * rôle — admin du serveur (il le fait vivre) ou membre (vue d'ensemble seule).
 */

import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Trash2, UserPlus } from 'lucide-react'
import { api } from '@/lib/api'
import { useToasts } from '@/stores/toasts'
import { formatDateTime } from '@/lib/format'
import type { ServerMember, ServerRole, UserLookup } from '@/lib/types'
import { Avatar } from '@/components/common/Avatar'
import { Button } from '@/components/ui/Button'
import { Card, CardHeader, EmptyState, Field, Input, LoadingBlock, Select } from '@/components/ui/primitives'
import { ErrorPanel } from '@/components/common/ErrorPanel'
import { ConfirmDialog } from '@/components/common/ConfirmDialog'
import { useServerContext } from './context'
import { t, type MessageKey } from '@/i18n'

type ShareRole = Exclude<ServerRole, 'OWNER'>
const ROLES: ShareRole[] = ['VIEWER', 'ADMIN']

/** Suggestions de pseudos au fil de la frappe (à partir de deux caractères). */
function useLookup(query: string) {
  const [debounced, setDebounced] = useState(query)
  useEffect(() => {
    const timer = window.setTimeout(() => setDebounced(query.trim()), 250)
    return () => window.clearTimeout(timer)
  }, [query])
  return useQuery<UserLookup[]>({
    queryKey: ['user-lookup', debounced],
    queryFn: () => api.users.lookup(debounced),
    enabled: debounced.length >= 2,
    staleTime: 30_000,
  })
}

function AddMember({ serverId, taken }: { serverId: number; taken: Set<string> }) {
  const queryClient = useQueryClient()
  const push = useToasts((state) => state.push)
  const [username, setUsername] = useState('')
  const [role, setRole] = useState<ShareRole>('VIEWER')
  const [focused, setFocused] = useState(false)
  const suggestions = useLookup(username)

  const share = useMutation({
    mutationFn: () => api.servers.share(serverId, username.trim(), role),
    onSuccess: (member) => {
      setUsername('')
      void queryClient.invalidateQueries({ queryKey: ['members', serverId] })
      push({ kind: 'success', title: t('members.added', { name: member.username }) })
    },
  })

  const options = (suggestions.data ?? []).filter(
    (item) => !taken.has(item.username.toLowerCase()),
  )

  return (
    <form
      className="grid gap-3 px-5 py-4 sm:grid-cols-[1fr_15rem_auto] sm:items-start"
      onSubmit={(event) => {
        event.preventDefault()
        if (username.trim()) share.mutate()
      }}
    >
      <div className="relative">
        <Field label={t('members.username')} hint={t('members.usernameHint')}>
          <Input
            value={username}
            autoComplete="off"
            onChange={(event) => setUsername(event.target.value)}
            onFocus={() => setFocused(true)}
            onBlur={() => window.setTimeout(() => setFocused(false), 150)}
          />
        </Field>
        {focused && options.length > 0 ? (
          <ul className="absolute z-20 mt-1 max-h-56 w-full overflow-y-auto rounded-lg border border-slate-700 bg-slate-900 py-1 shadow-lg">
            {options.map((item) => (
              <li key={item.id}>
                <button
                  type="button"
                  className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-sm text-slate-200 hover:bg-slate-800"
                  onMouseDown={(event) => event.preventDefault()}
                  onClick={() => {
                    setUsername(item.username)
                    setFocused(false)
                  }}
                >
                  <Avatar url={item.avatar_url} name={item.username} size="xs" />
                  {item.username}
                </button>
              </li>
            ))}
          </ul>
        ) : null}
      </div>
      <Field label={t('members.role')}>
        <Select value={role} onChange={(event) => setRole(event.target.value as ShareRole)}>
          {ROLES.map((value) => (
            <option key={value} value={value}>
              {t(`members.role.${value}` as MessageKey)}
            </option>
          ))}
        </Select>
      </Field>
      <Button
        type="submit"
        variant="primary"
        className="sm:mt-6"
        icon={<UserPlus className="size-4" />}
        loading={share.isPending}
        disabled={!username.trim()}
      >
        {t('members.add')}
      </Button>
      <div className="sm:col-span-3">
        <ErrorPanel error={share.error} />
      </div>
    </form>
  )
}

export function MembersPage() {
  const { server } = useServerContext()
  const queryClient = useQueryClient()
  const pushError = useToasts((state) => state.pushError)
  const [toRemove, setToRemove] = useState<ServerMember | null>(null)

  const members = useQuery({
    queryKey: ['members', server.id],
    queryFn: () => api.servers.members(server.id),
  })
  const invalidate = () => void queryClient.invalidateQueries({ queryKey: ['members', server.id] })

  const changeRole = useMutation({
    mutationFn: ({ member, role }: { member: ServerMember; role: ShareRole }) =>
      api.servers.share(server.id, member.username, role),
    onSuccess: invalidate,
    onError: (error) => pushError(error),
  })
  const remove = useMutation({
    mutationFn: (member: ServerMember) => api.servers.unshare(server.id, member.user_id),
    onSuccess: () => {
      invalidate()
      setToRemove(null)
    },
  })

  const taken = new Set((members.data ?? []).map((member) => member.username.toLowerCase()))
  taken.add(server.owner_username.toLowerCase())

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-4xl space-y-5 p-4 sm:p-6">
        <Card>
          <CardHeader title={t('members.addTitle')} subtitle={t('members.addSubtitle')} />
          <AddMember serverId={server.id} taken={taken} />
        </Card>

        <Card>
          <CardHeader title={t('members.listTitle')} subtitle={t('members.listSubtitle')} />
          {members.isLoading ? <LoadingBlock /> : null}
          <ErrorPanel error={members.error} className="m-5" />
          {members.data && members.data.length === 0 ? (
            <EmptyState title={t('members.empty')} description={t('members.emptyHint')} />
          ) : null}
          {members.data && members.data.length > 0 ? (
            <ul className="divide-y divide-slate-800/60">
              {members.data.map((member) => (
                <li key={member.user_id} className="flex items-center gap-3 px-5 py-3">
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm text-slate-200">{member.username}</p>
                    <p className="text-xs text-slate-500">
                      {t('members.since', { date: formatDateTime(member.added_at) })}
                    </p>
                  </div>
                  <div className="w-56 shrink-0">
                    <Select
                      className="h-8 py-0 text-xs"
                      value={member.role}
                      disabled={changeRole.isPending}
                      onChange={(event) =>
                        changeRole.mutate({ member, role: event.target.value as ShareRole })
                      }
                    >
                      {ROLES.map((value) => (
                        <option key={value} value={value}>
                          {t(`members.role.${value}` as MessageKey)}
                        </option>
                      ))}
                    </Select>
                  </div>
                  <Button
                    size="sm"
                    variant="ghost"
                    icon={<Trash2 className="size-3.5" />}
                    onClick={() => setToRemove(member)}
                    title={t('members.remove')}
                  >
                    <span className="sr-only">{t('members.remove')}</span>
                  </Button>
                </li>
              ))}
            </ul>
          ) : null}
        </Card>

        <div className="rounded-lg border border-slate-800 bg-slate-900/40 px-4 py-3 text-xs text-slate-500">
          <p>{t('members.roleHint.VIEWER')}</p>
          <p className="mt-1">{t('members.roleHint.ADMIN')}</p>
        </div>
      </div>

      <ConfirmDialog
        open={toRemove !== null}
        title={t('members.removeTitle', { name: toRemove?.username ?? '' })}
        consequence={t('members.removeConsequence')}
        confirmLabel={t('members.remove')}
        danger
        loading={remove.isPending}
        error={remove.error}
        onConfirm={() => toRemove && remove.mutate(toRemove)}
        onClose={() => setToRemove(null)}
      />
    </div>
  )
}
