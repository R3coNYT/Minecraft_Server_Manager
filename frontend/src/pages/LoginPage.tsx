import { useState, type FormEvent } from 'react'
import { Navigate } from 'react-router-dom'
import { Boxes } from 'lucide-react'
import { useLogin, useMe } from '@/hooks/useApi'
import { Button } from '@/components/ui/Button'
import { Card, Field, Input, LoadingBlock } from '@/components/ui/primitives'
import { ErrorPanel } from '@/components/common/ErrorPanel'

export function LoginPage() {
  const { data: me, isLoading } = useMe()
  const login = useLogin()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')

  if (isLoading) return <LoadingBlock />
  if (me) return <Navigate to="/" replace />

  const onSubmit = (event: FormEvent) => {
    event.preventDefault()
    login.mutate({ username, password })
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-slate-950 px-4">
      <div className="w-full max-w-sm">
        <div className="mb-6 flex flex-col items-center gap-2 text-center">
          <Boxes className="size-8 text-emerald-500" />
          <h1 className="text-lg font-semibold text-slate-100">Minecraft Server Manager</h1>
          <p className="text-sm text-slate-500">Connexion au panneau d'administration</p>
        </div>

        <Card className="p-5">
          <form onSubmit={onSubmit} className="space-y-4">
            <Field label="Nom d'utilisateur">
              <Input
                value={username}
                onChange={(event) => setUsername(event.target.value)}
                autoComplete="username"
                autoFocus
                required
              />
            </Field>

            <Field label="Mot de passe">
              <Input
                type="password"
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                autoComplete="current-password"
                required
              />
            </Field>

            <ErrorPanel error={login.error} />

            <Button
              type="submit"
              variant="primary"
              size="lg"
              className="w-full"
              loading={login.isPending}
              disabled={!username || !password}
            >
              Se connecter
            </Button>
          </form>
        </Card>

        <p className="mt-4 text-center text-xs text-slate-600">
          Premier démarrage ? Créer un compte avec{' '}
          <code className="rounded bg-slate-900 px-1.5 py-0.5 font-mono">
            msm createadmin
          </code>
        </p>
      </div>
    </div>
  )
}
