/**
 * Création d'un compte, quand l'inscription est ouverte ou qu'on arrive avec
 * un lien d'invitation (`/register?invite=…`). Le compte créé est aussitôt
 * connecté, comme après une connexion.
 */

import { useState, type FormEvent } from 'react'
import { Link, Navigate, useNavigate, useSearchParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { queryKeys, useMe } from '@/hooks/useApi'
import { MsmLogo } from '@/components/brand/MsmLogo'
import { Button } from '@/components/ui/Button'
import { Card, Checkbox, Field, Input, LoadingBlock } from '@/components/ui/primitives'
import { ErrorPanel } from '@/components/common/ErrorPanel'
import { GoogleButton, OrSeparator } from '@/components/common/GoogleButton'
import { t } from '@/i18n'

const MIN_PASSWORD = 10

export function RegisterPage() {
  const { data: me, isLoading: meLoading } = useMe()
  const [params] = useSearchParams()
  const invitation = params.get('invite')
  const navigate = useNavigate()
  const queryClient = useQueryClient()

  const [email, setEmail] = useState('')
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [accept, setAccept] = useState(false)

  const info = useQuery({ queryKey: ['registration'], queryFn: () => api.auth.registration() })

  const register = useMutation({
    mutationFn: () =>
      api.auth.register({
        email,
        username,
        password,
        accept_terms: accept,
        invitation,
      }),
    onSuccess: (created) => {
      queryClient.setQueryData(queryKeys.me, created)
      void queryClient.invalidateQueries()
      navigate('/', { replace: true })
    },
  })

  if (meLoading || info.isLoading) return <LoadingBlock />
  if (me) return <Navigate to="/" replace />

  const mode = info.data?.mode ?? 'closed'
  const possible = mode === 'open' || (mode === 'invite' && Boolean(invitation))
  const mismatch = confirm.length > 0 && confirm !== password
  const ready =
    email.trim() && username.trim() && password.length >= MIN_PASSWORD && confirm === password && accept

  const onSubmit = (event: FormEvent) => {
    event.preventDefault()
    if (ready) register.mutate()
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-slate-950 px-4 py-10">
      <div className="w-full max-w-sm">
        <div className="mb-6 flex flex-col items-center gap-2 text-center">
          <MsmLogo className="size-14 text-emerald-500" />
          <h1 className="text-lg font-semibold text-slate-100">{t('register.title')}</h1>
          <p className="text-sm text-slate-500">
            {invitation ? t('register.invited') : t('register.subtitle')}
          </p>
        </div>

        {!possible ? (
          <Card className="space-y-2 p-5 text-sm">
            <p className="font-medium text-slate-200">
              {mode === 'invite' ? t('register.inviteOnly') : t('register.closed')}
            </p>
            <p className="text-slate-500">{t('register.closedHint')}</p>
          </Card>
        ) : (
          <Card className="p-5">
            <form onSubmit={onSubmit} className="space-y-4">
              <Field label={t('register.email')}>
                <Input
                  type="email"
                  value={email}
                  onChange={(event) => setEmail(event.target.value)}
                  autoComplete="email"
                  autoFocus
                  required
                />
              </Field>
              <Field label={t('register.username')} hint={t('register.usernameHint')}>
                <Input
                  value={username}
                  onChange={(event) => setUsername(event.target.value)}
                  autoComplete="username"
                  maxLength={24}
                  required
                />
              </Field>
              <Field label={t('register.password')} hint={t('register.passwordHint')}>
                <Input
                  type="password"
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                  autoComplete="new-password"
                  required
                />
              </Field>
              <Field
                label={t('register.confirm')}
                error={mismatch ? t('register.mismatch') : undefined}
              >
                <Input
                  type="password"
                  value={confirm}
                  onChange={(event) => setConfirm(event.target.value)}
                  autoComplete="new-password"
                  required
                />
              </Field>
              <Checkbox
                label={t('register.accept')}
                hint={t('register.acceptHint')}
                checked={accept}
                onChange={(event) => setAccept(event.target.checked)}
              />

              <ErrorPanel error={register.error} />

              <Button
                type="submit"
                variant="primary"
                size="lg"
                className="w-full"
                loading={register.isPending}
                disabled={!ready}
              >
                {t('register.submit')}
              </Button>

              {info.data?.google ? (
                <>
                  <OrSeparator />
                  <GoogleButton invitation={invitation} />
                </>
              ) : null}
            </form>
          </Card>
        )}

        <p className="mt-4 text-center text-sm text-slate-500">
          {t('register.haveAccount')}{' '}
          <Link to="/login" className="text-emerald-400 hover:text-emerald-300">
            {t('register.signIn')}
          </Link>
        </p>
      </div>
    </div>
  )
}
