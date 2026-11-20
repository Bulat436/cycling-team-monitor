import { useState } from 'react'
import type { ReactNode } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { Link, Navigate, NavLink, Outlet, Route, Routes } from 'react-router'

import { ApiError, explain, request, roleNames } from './api'
import type { Role, Session } from './api'
import { Audit, ManageAccess, Team } from './Coach'
import { forgetSession, useSession } from './session'
import { AcceptAccess, Brand, SignIn } from './SignIn'

const athletePages = [
  { path: '/diary', label: 'Дневник', action: 'Открыть дневник', summary: 'Утренняя анкета появится на следующем этапе.' },
  { path: '/workouts', label: 'Тренировки', action: 'Открыть тренировки', summary: 'Здесь появятся тренировки после добавления импорта FIT.' },
  { path: '/profile', label: 'Профиль', action: 'Открыть профиль', summary: 'Данные вашей учётной записи' },
]

function Shell({ session }: { session: Session }) {
  const client = useQueryClient()
  const [exitError, setExitError] = useState('')
  const [busy, setBusy] = useState(false)
  const coach = session.user.role === 'coach'

  async function logout() {
    setBusy(true)
    setExitError('')
    try {
      await request('/auth/logout', { method: 'POST', csrf: session.csrf_token })
      await client.cancelQueries()
      forgetSession(client)
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) forgetSession(client)
      else setExitError(explain(error))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="workspace">
      <aside className="sidebar">
        <Link className="home-link" to="/"><Brand /></Link>
        <nav aria-label="Разделы">
          <NavLink to="/" end>{coach ? 'Команда' : 'Главная'}</NavLink>
          {coach ? <NavLink to="/access">Управление доступом</NavLink> :
            athletePages.map(page => <NavLink key={page.path} to={page.path}>{page.label}</NavLink>)}
        </nav>
        <div className="sidebar-account">
          <span>{session.user.name}</span>
          <small>{roleNames[session.user.role]}</small>
          <button className="secondary" disabled={busy} onClick={() => void logout()}>Выйти</button>
          {exitError && <p role="alert" className="error">{exitError}</p>}
        </div>
      </aside>
      <main className="content"><Outlet /></main>
    </div>
  )
}

function Protected() {
  const session = useSession()
  if (session.data) return <Shell session={session.data} />
  if (session.isPending) return <main className="centered" role="status">Проверяем сессию…</main>
  if (session.isError) return <main className="centered"><p role="alert">{explain(session.error)}</p><button onClick={() => void session.refetch()}>Повторить</button></main>
  return <Navigate to="/login" replace />
}

function ForRole({ role, children }: { role: Role; children: ReactNode }) {
  const session = useSession()
  return session.data?.user.role === role ? children : (
    <section className="panel error-page">
      <h1>Доступ ограничен</h1>
      <p>Этот раздел доступен другой роли.</p>
      <Link to="/">На главную</Link>
    </section>
  )
}

function Home() {
  const session = useSession().data!
  if (session.user.role === 'coach') return <Team />
  return (
    <>
      <header className="page-heading"><div><h1>Главная</h1><p>{session.user.name}</p></div></header>
      <div className="overview">
        {athletePages.map(page => (
          <section className="panel" key={page.path}>
            <h2>{page.label}</h2>
            <p className="muted">{page.summary}</p>
            <Link to={page.path}>{page.action}</Link>
          </section>
        ))}
      </div>
    </>
  )
}

function AthletePage({ page }: { page: (typeof athletePages)[number] }) {
  const { user } = useSession().data!
  return (
    <>
      <header className="page-heading"><div><h1>{page.label}</h1></div></header>
      <section className="panel">
        <p className="muted">{page.summary}</p>
        {page.path === '/profile' && <dl className="profile">
          <div><dt>Имя и фамилия</dt><dd>{user.name}</dd></div>
          <div><dt>Электронная почта</dt><dd>{user.email}</dd></div>
          <div><dt>Роль</dt><dd>{roleNames[user.role]}</dd></div>
        </dl>}
      </section>
    </>
  )
}

function AccessPage() {
  return <ManageAccess session={useSession().data!} />
}

export function App() {
  return (
    <Routes>
      <Route path="/login" element={<SignIn />} />
      <Route path="/invite" element={<AcceptAccess key="invite" />} />
      <Route path="/reset" element={<AcceptAccess key="reset" reset />} />
      <Route element={<Protected />}>
        <Route index element={<Home />} />
        <Route path="/access" element={<ForRole role="coach"><AccessPage /></ForRole>} />
        <Route path="/access/audit" element={<ForRole role="coach"><Audit /></ForRole>} />
        {athletePages.map(page => <Route key={page.path} path={page.path} element={<ForRole role="athlete"><AthletePage page={page} /></ForRole>} />)}
        <Route path="*" element={<section className="panel error-page"><h1>Страница не найдена</h1><Link to="/">На главную</Link></section>} />
      </Route>
    </Routes>
  )
}
