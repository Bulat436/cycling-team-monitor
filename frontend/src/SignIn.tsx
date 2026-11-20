import { useEffect, useState } from 'react'
import type { FormEvent, ReactNode } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { Link, Navigate, useLocation, useNavigate } from 'react-router'

import { explain, request } from './api'
import type { Session } from './api'
import { forgetSession, sessionKey, useSession } from './session'

export function Brand() {
  return <span className="brand"><span className="brand-mark" aria-hidden="true" />Cycling Team Monitor</span>
}

function AccessLayout({ children }: { children: ReactNode }) {
  return (
    <main className="access-layout">
      <section className="access-brand">
        <Brand />
        <div className="track" aria-hidden="true"><span /><span /><span /></div>
        <p>Мониторинг велокоманды</p>
      </section>
      <section className="access-content">{children}</section>
    </main>
  )
}

export function SignIn() {
  const client = useQueryClient()
  const session = useSession()
  const location = useLocation()
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const data = new FormData(event.currentTarget)
    setBusy(true)
    setError('')
    try {
      const identity = await request<Session>('/auth/login', {
        method: 'POST',
        body: { email: data.get('email'), password: data.get('password') },
      })
      await client.cancelQueries({ queryKey: sessionKey })
      forgetSession(client)
      client.setQueryData(sessionKey, identity)
    } catch (error) {
      setError(explain(error))
    } finally {
      setBusy(false)
    }
  }

  if (session.data) return <Navigate to="/" replace />
  return (
    <AccessLayout>
      <form className="access-form" onSubmit={submit}>
        <h1>Вход в команду</h1>
        {location.state?.passwordSet && <p role="status" className="success">Пароль сохранён. Войдите в свой аккаунт.</p>}
        <label>Электронная почта<input name="email" type="email" autoComplete="username" maxLength={254} required placeholder="name@example.com" /></label>
        <label>Пароль<input name="password" type="password" autoComplete="current-password" maxLength={128} required /></label>
        {error && <p role="alert" className="error">{error}</p>}
        <button disabled={busy}>{busy ? 'Входим…' : 'Войти'}</button>
        <p className="muted">Для получения приглашения или сброса пароля обратитесь к тренеру.</p>
      </form>
    </AccessLayout>
  )
}

export function AcceptAccess({ reset = false }: { reset?: boolean }) {
  const [token] = useState(() => window.location.hash.slice(1))
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const navigate = useNavigate()

  useEffect(() => {
    if (token) window.history.replaceState(window.history.state, '', window.location.pathname)
  }, [token])

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const data = new FormData(event.currentTarget)
    if (data.get('password') !== data.get('confirmation')) {
      setError('Пароли не совпадают.')
      return
    }
    setBusy(true)
    setError('')
    try {
      await request('/auth/access/accept', { method: 'POST', body: { token, password: data.get('password') } })
      navigate('/login', { replace: true, state: { passwordSet: true } })
    } catch (error) {
      setError(explain(error))
    } finally {
      setBusy(false)
    }
  }

  return (
    <AccessLayout>
      <form className="access-form" onSubmit={submit}>
        <h1>{reset ? 'Сброс пароля' : 'Приглашение в команду'}</h1>
        {token.length === 43 ? (
          <>
            <label>Новый пароль<input name="password" type="password" autoComplete="new-password" minLength={12} maxLength={128} required /><span className="muted">От 12 до 128 символов.</span></label>
            <label>Повторите пароль<input name="confirmation" type="password" autoComplete="new-password" minLength={12} maxLength={128} required /></label>
            {error && <p role="alert" className="error">{error}</p>}
            <button disabled={busy}>{busy ? 'Сохраняем…' : 'Сохранить пароль'}</button>
          </>
        ) : <p role="alert" className="error">Откройте полную ссылку, полученную от тренера.</p>}
        <Link to="/login">Перейти ко входу</Link>
      </form>
    </AccessLayout>
  )
}
