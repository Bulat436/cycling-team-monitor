import { lazy, Suspense } from 'react'
import { Link, NavLink, Outlet, useOutletContext, useParams } from 'react-router'

import { QueryFeedback } from './AthleteFeedback'
import { ProfilePage } from './AthleteProfile'
import { dateLabel, durationLabel, recordedAt, useAthleteResource } from './athleteApi'
import type { AthleteProfile } from './athleteApi'
import type { TeamRow } from './coachApi'
import { OnelapSection } from './OnelapFit'
import { Advisories } from './Team'
import { DiaryPage, ScoreDetails } from './Wellness'
import { displayMeasure, WorkoutPage } from './Workouts'

const Analytics = lazy(() => import('./CoachAnalytics'))
const TrainingAthlete = lazy(() => import('./TrainingAthlete'))

const tabs = [
  { path: '', name: 'Обзор' }, { path: 'diary', name: 'Дневник' },
  { path: 'workouts', name: 'Тренировки' }, { path: 'analytics', name: 'Аналитика' },
  { path: 'load-recovery', name: 'Нагрузка и восстановление' },
  { path: 'training', name: 'FTP и зоны' }, { path: 'profile', name: 'Профиль' },
]

function AthleteCard({ athleteId }: { athleteId: number }) {
  const query = useAthleteResource<AthleteProfile>('/athletes/' + athleteId)
  const athlete = query.data
  const path = '/athletes/' + athleteId
  return <>
    <Link className="back-link" to="/">К команде</Link>
    <QueryFeedback loading={query.isPending} error={query.error} retry={() => void query.refetch()} />
    {athlete && <>
      <div className="athlete-card-heading">
        <p>{athlete.name}</p>
        {athlete.team_group && <span className="muted">{athlete.team_group}</span>}
      </div>
      <nav className="athlete-tabs" aria-label="Вкладки спортсмена">
        {tabs.map(tab => <NavLink key={tab.path} end={tab.path === ''} to={path + (tab.path ? '/' + tab.path : '')}>{tab.name}</NavLink>)}
      </nav>
      <Outlet context={athlete} />
    </>}
  </>
}

export function CoachAthlete() {
  const athleteId = Number(useParams().athleteId)
  if (!Number.isSafeInteger(athleteId) || athleteId < 1) {
    return <section className="panel"><h1>Профиль спортсмена недоступен</h1><Link to="/">К команде</Link></section>
  }
  return <AthleteCard key={athleteId} athleteId={athleteId} />
}

function Overview({ athlete }: { athlete: AthleteProfile }) {
  const athleteId = athlete.athlete_id
  const query = useAthleteResource<TeamRow>('/analytics/athletes/' + athleteId + '/overview?period=7', 30_000)
  const row = query.data
  return <>
    <header className="page-heading"><div><h1>Обзор спортсмена</h1>{row && <p>{dateLabel(row.today)} · {row.timezone}</p>}</div></header>
    <QueryFeedback loading={query.isPending} error={query.error} retry={() => void query.refetch()} />
    {row && <>
      <dl className="stats coach-stats">
        <div><dt>Тренировки за 7 дней</dt><dd>{row.load.workouts}</dd></div>
        <div><dt>Время по таймеру</dt><dd>{durationLabel(row.load.timer_seconds)}</dd></div>
        <div><dt>TSS за 7 дней</dt><dd>{displayMeasure(row.load.tss, '')}</dd></div>
        <div><dt>session-RPE за 7 дней</dt><dd>{displayMeasure(row.load.rpe_load, '')}</dd></div>
      </dl>
      <div className="analytics-pair">
        <section className="panel">
          <h2>Утренний дневник</h2>
          {row.wellness ? <ScoreDetails entry={row.wellness} /> : <p className="muted">Сегодня не заполнен.</p>}
        </section>
        <section className="panel">
          <h2>Предупреждения</h2>
          <Advisories alerts={row.alerts} />
          <p className="muted">Информационные сигналы для тренера.</p>
          <h2>Последняя тренировка</h2>
          {row.last_workout ? <Link to={'/workouts/' + row.last_workout.id}>{recordedAt(row.last_workout.started_at, row.timezone)}</Link>
            : <p className="muted">Тренировок пока нет.</p>}
        </section>
      </div>
    </>}
    <OnelapSection athleteId={athleteId} timezone={athlete.timezone} coach />
  </>
}

export function CoachAthleteTab({ page }: { page: 'overview' | 'diary' | 'workouts' | 'analytics' | 'load-recovery' | 'training' | 'profile' }) {
  const athlete = useOutletContext<AthleteProfile>()
  const athleteId = athlete.athlete_id
  if (page === 'overview') return <Overview athlete={athlete} />
  if (page === 'diary') return <DiaryPage athleteId={athleteId} readOnly />
  if (page === 'workouts') return <WorkoutPage athleteId={athleteId} readOnly />
  if (page === 'analytics') {
    return <Suspense fallback={<p role="status">Загружаем графики…</p>}><Analytics athleteId={athleteId} /></Suspense>
  }
  if (page === 'load-recovery') {
    return <Suspense fallback={<p role="status">Загружаем графики…</p>}><TrainingAthlete athleteId={athleteId} /></Suspense>
  }
  return <ProfilePage key={page} athleteId={athleteId} edit view={page} />
}
