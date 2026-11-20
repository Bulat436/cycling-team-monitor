import { useState } from 'react'
import type { ReactNode } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { Link, Navigate, NavLink, Outlet, Route, Routes } from 'react-router'

import { ApiError, explain, request, roleNames } from './api'
import type { Role, Session } from './api'
import { Audit, ManageAccess } from './Coach'
import { Team } from './Team'
import { AlertSettingsPage } from './AlertSettings'
import { CoachAthlete, CoachAthleteTab } from './CoachAthlete'
import { AthleteHome } from './AthleteHome'
import { ProfilePage } from './AthleteProfile'
import { DiaryPage } from './Wellness'
import { WorkoutPage } from './Workouts'
import { WorkoutDetailRoute } from './WorkoutDetail'
import { forgetSession, useSession } from './session'
import { AcceptAccess, Brand, SignIn } from './SignIn'

const athletePages = [
  { path: '/diary', label: 'Дневник' },
  { path: '/workouts', label: 'Тренировки' },
  { path: '/profile', label: 'Профиль' },
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
    <div className={coach ? "workspace coach-workspace" : "workspace athlete-workspace"}>
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
  return session.user.role === 'coach' ? <Team /> : <AthleteHome user={session.user} />
}

function AthleteArea({ page }: { page: 'diary' | 'workouts' | 'profile' }) {
  const athleteId = useSession().data!.user.id
  if (page === 'diary') return <DiaryPage athleteId={athleteId} />
  if (page === 'workouts') return <WorkoutPage athleteId={athleteId} />
  return <ProfilePage athleteId={athleteId} />
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
        <Route path="/diary" element={<ForRole role="athlete"><AthleteArea page="diary" /></ForRole>} />
        <Route path="/workouts" element={<ForRole role="athlete"><AthleteArea page="workouts" /></ForRole>} />
        <Route path="/workouts/:workoutId" element={<WorkoutDetailRoute />} />
        <Route path="/profile" element={<ForRole role="athlete"><AthleteArea page="profile" /></ForRole>} />
        <Route path="/alerts/settings" element={<ForRole role="coach"><AlertSettingsPage /></ForRole>} />
        <Route path="/athletes/:athleteId" element={<ForRole role="coach"><CoachAthlete /></ForRole>}>
          <Route index element={<CoachAthleteTab page="overview" />} />
          <Route path="diary" element={<CoachAthleteTab page="diary" />} />
          <Route path="workouts" element={<CoachAthleteTab page="workouts" />} />
          <Route path="analytics" element={<CoachAthleteTab page="analytics" />} />
          <Route path="training" element={<CoachAthleteTab page="training" />} />
          <Route path="profile" element={<CoachAthleteTab page="profile" />} />
        </Route>
        <Route path="*" element={<section className="panel error-page"><h1>Страница не найдена</h1><Link to="/">На главную</Link></section>} />
      </Route>
    </Routes>
  )
}
