/**
 * Dernière étape d'une inscription avec Google : choisir son pseudo. Google a
 * fourni l'adresse (et la photo, reprise comme avatar) ; le pseudo, lui, se choisit.
 */

import { useEffect, useState, type FormEvent } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { queryKeys } from '@/hooks/useApi'
import { MsmLogo } from '@/components/brand/MsmLogo'
import { Button } from '@/components/ui/Button'
import { Card, Checkbox, Field, Input, LoadingBlock } from '@/components/ui/primitives'
import { ErrorPanel } from '@/components/common/ErrorPanel'
import { t } from '@/i18n'

export function GoogleSignupPage() {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const pending = useQuery({
    queryKey: ['google-signup'],
    queryFn: () => api.auth.googleSignup(),
    retry: false,
  })
  const [username, setUsername] = useState('')
  const [accept, setAccept] = useState(false)

  useEffect(() => {
    if (pending.data && !username) setUsername(pending.data.suggested_username)
  }, [pending.data, username])

  const create = useMutation({
    mutationFn: () => api.auth.completeGoogleSignup(username.trim(), accept),
    onSuccess: (me) => {
      queryClient.setQueryData(queryKeys.me, me)
      void queryClient.invalidateQueries()
      navigate('/', { replace: true })
    },
  })

  if (pending.isLoading) return <LoadingBlock />

  const onSubmit = (event: FormEvent) => {
    event.preventDefault()
    if (username.trim() && accept) create.mutate()
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-slate-950 px-4 py-10">
      <div className="w-full max-w-sm">
        <div className="mb-6 flex flex-col items-center gap-2 text-center">
          <MsmLogo className="size-14 text-emerald-500" />
          <h1 className="text-lg font-semibold text-slate-100">{t('google.pickTitle')}</h1>
          {pending.data ? (
            <p className="text-sm text-slate-500">{t('google.pickSubtitle', { email: pending.data.email })}</p>
          ) : null}
        </div>

        {pending.error ? (
          <Card className="space-y-3 p-5">
            <ErrorPanel error={pending.error} />
            <Link to="/login" className="text-sm text-emerald-400 hover:text-emerald-300">
              {t('register.signIn')}
            </Link>
          </Card>
        ) : (
          <Card className="p-5">
            <form onSubmit={onSubmit} className="space-y-4">
              <Field label={t('register.username')} hint={t('register.usernameHint')}>
                <Input
                  value={username}
                  maxLength={24}
                  autoFocus
                  onChange={(event) => setUsername(event.target.value)}
                />
              </Field>
              <Checkbox
                label={t('register.accept')}
                hint={t('register.acceptHint')}
                checked={accept}
                onChange={(event) => setAccept(event.target.checked)}
              />
              <ErrorPanel error={create.error} />
              <Button
                type="submit"
                variant="primary"
                size="lg"
                className="w-full"
                loading={create.isPending}
                disabled={!username.trim() || !accept}
              >
                {t('register.submit')}
              </Button>
            </form>
          </Card>
        )}
      </div>
    </div>
  )
}
