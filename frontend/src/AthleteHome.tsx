import { Link } from 'react-router'

import { durationLabel, recordedAt, useAthleteResource } from './athleteApi'
import type { WellnessHistory, WorkoutList } from './athleteApi'
import { QueryFeedback } from './AthleteFeedback'
import { OnelapSummary } from './OnelapFit'
import type { Identity } from './api'

export function AthleteHome({ user }: { user: Identity }) {
  const base = '/athletes/' + user.id
  const wellness = useAthleteResource<WellnessHistory>(base + '/wellness?limit=1', 60_000)
  const workouts = useAthleteResource<WorkoutList>(base + '/workouts?limit=1')
  const latest = workouts.data?.workouts[0]

  return (
    <>
      <header className="page-heading"><div><h1>Главная</h1><p>Здравствуйте, {user.name}!</p></div></header>
      <div className="overview athlete-overview">
        <section className="panel">
          <h2>Утренний дневник</h2>
          <QueryFeedback loading={wellness.isPending} error={wellness.error} retry={() => void wellness.refetch()} />
          {wellness.data && (
            <>
              <p className={'status ' + (wellness.data.today_entry ? 'active' : 'pending')}>{wellness.data.today_entry ? 'Сегодня заполнен' : 'Сегодня не заполнен'}</p>
              <Link className="button" to="/diary">{wellness.data.today_entry ? 'Открыть утренний дневник' : 'Заполнить утренний дневник'}</Link>
            </>
          )}
        </section>
        <section className="panel">
          <h2>Последняя тренировка</h2>
          <QueryFeedback loading={workouts.isPending} error={workouts.error} retry={() => void workouts.refetch()} />
          {workouts.data && (
            <>
              <p>{latest ? recordedAt(latest.started_at, workouts.data.timezone) + ' · ' + durationLabel(latest.timer_seconds) : 'Тренировок пока нет.'}</p>
              {workouts.data.missing_rpe_count > 0 && <p className="status pending">Тренировок без RPE — {workouts.data.missing_rpe_count}</p>}
              <Link to="/workouts">Открыть тренировки</Link>
            </>
          )}
        </section>
        <section className="panel">
          <h2>Профиль</h2>
          <OnelapSummary athleteId={user.id} />
          <Link to="/profile">Открыть профиль</Link>
        </section>
      </div>
    </>
  )
}
