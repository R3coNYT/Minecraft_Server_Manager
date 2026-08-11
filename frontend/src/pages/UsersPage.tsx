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

const ROLE_LABELS: Record<Role, string> = {
  ADMIN: 'Administrateur',
  MODERATOR: 'Modérateur',
  VIEWER: 'Lecture seule',
}

const ROLE_HINTS: Record<Role, string> = {
  ADMIN: 'Tous les droits, y compris les commandes sensibles et la gestion des comptes.',
  MODERATOR: 'Démarrage, arrêt, console, kick et bannissement. Pas de configuration.',
  VIEWER: 'Consultation seule : états, console et joueurs.',
}

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
      push({ kind: 'success', title: `Compte « ${user.username} » créé` })
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
      push({ kind: 'success', title: 'Compte modifié' })
    },
    onError: (mutationError) => pushError(mutationError),
  })

  const remove = useMutation({
    mutationFn: (id: number) => api.users.remove(id),
    onSuccess: () => {
      invalidate()
      push({ kind: 'success', title: 'Compte supprimé' })
      setToDelete(null)
    },
    onError: (mutationError) => pushError(mutationError),
  })

  if (isLoading) return <LoadingBlock />

  return (
    <div className="mx-auto max-w-5xl space-y-5 p-4 sm:p-6">
      <div className="flex items-center justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold text-slate-100">Utilisateurs</h1>
          <p className="text-sm text-slate-500">
            Un changement de rôle ferme immédiatement les sessions ouvertes du compte.
          </p>
        </div>
        <Button variant="primary" icon={<Plus className="size-4" />} onClick={() => setCreateOpen(true)}>
          Nouveau compte
        </Button>
      </div>

      <ErrorPanel error={error} />

      <Card>
        <CardHeader title={`${users?.length ?? 0} compte(s)`} />
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-slate-800 text-left text-xs text-slate-500">
              <th className="px-5 py-2.5 font-medium">Utilisateur</th>
              <th className="px-5 py-2.5 font-medium">Rôle</th>
              <th className="px-5 py-2.5 font-medium">Dernière connexion</th>
              <th className="px-5 py-2.5 font-medium">Actif</th>
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
                    {isSelf ? <span className="ml-2 text-xs text-slate-600">(vous)</span> : null}
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
                      {(Object.keys(ROLE_LABELS) as Role[]).map((value) => (
                        <option key={value} value={value}>
                          {ROLE_LABELS[value]}
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
                      <span className="sr-only">Supprimer</span>
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
        title="Nouveau compte"
        size="sm"
        footer={
          <>
            <Button variant="ghost" onClick={() => setCreateOpen(false)}>
              Annuler
            </Button>
            <Button
              variant="primary"
              loading={create.isPending}
              disabled={!username.trim() || password.length < 10}
              onClick={() => create.mutate()}
            >
              Créer
            </Button>
          </>
        }
      >
        <div className="space-y-4">
          <Field label="Nom d'utilisateur">
            <Input value={username} onChange={(event) => setUsername(event.target.value)} autoFocus />
          </Field>
          <Field label="Mot de passe" hint="10 caractères minimum. Une phrase de passe est idéale.">
            <Input
              type="password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
            />
          </Field>
          <Field label="Rôle" hint={ROLE_HINTS[role]}>
            <Select value={role} onChange={(event) => setRole(event.target.value as Role)}>
              {(Object.keys(ROLE_LABELS) as Role[]).map((value) => (
                <option key={value} value={value}>
                  {ROLE_LABELS[value]}
                </option>
              ))}
            </Select>
          </Field>
          <ErrorPanel error={create.error} />
        </div>
      </Dialog>

      <ConfirmDialog
        open={toDelete !== null}
        title={`Supprimer « ${toDelete?.username} » ?`}
        consequence="Le compte et ses sessions seront supprimés. Ses entrées d'audit sont conservées."
        confirmLabel="Supprimer"
        danger
        loading={remove.isPending}
        onConfirm={() => toDelete && remove.mutate(toDelete.id)}
        onClose={() => setToDelete(null)}
      />
    </div>
  )
}
