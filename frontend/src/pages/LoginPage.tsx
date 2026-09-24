import { useState, type FormEvent } from 'react'
import { Link, Navigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { MsmLogo } from '@/components/brand/MsmLogo'
import { useLogin, useMe } from '@/hooks/useApi'
import { Button } from '@/components/ui/Button'
import { Card, Field, Input, LoadingBlock } from '@/components/ui/primitives'
import { ErrorPanel } from '@/components/common/ErrorPanel'
import { t } from '@/i18n'

export function LoginPage() {
  const { data: me, isLoading } = useMe()
  const login = useLogin()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const registration = useQuery({
    queryKey: ['registration'],
    queryFn: () => api.auth.registration(),
  })

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
          <MsmLogo className="size-14 text-emerald-500" />
          <h1 className="text-lg font-semibold text-slate-100">Minecraft Server Manager</h1>
          <p className="text-sm text-slate-500">{t('login.subtitle')}</p>
        </div>

        <Card className="p-5">
          <form onSubmit={onSubmit} className="space-y-4">
            <Field label={t('login.username')}>
              <Input
                value={username}
                onChange={(event) => setUsername(event.target.value)}
                autoComplete="username"
                autoFocus
                required
              />
            </Field>

            <Field label={t('login.password')}>
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
              {t('login.submit')}
            </Button>
          </form>
        </Card>

        {registration.data?.mode === 'open' ? (
          <p className="mt-4 text-center text-sm text-slate-500">
            {t('login.noAccount')}{' '}
            <Link to="/register" className="text-emerald-400 hover:text-emerald-300">
              {t('login.createAccount')}
            </Link>
          </p>
        ) : null}

        {/* Indice pour l'administrateur qui installe MSM, inutile à un visiteur. */}
        {registration.data && registration.data.mode !== 'open' ? (
          <p className="mt-4 text-center text-xs text-slate-600">
            {t('login.firstRun')}{' '}
            <code className="rounded bg-slate-900 px-1.5 py-0.5 font-mono">
              msm createadmin
            </code>
          </p>
        ) : null}
      </div>
    </div>
  )
}
