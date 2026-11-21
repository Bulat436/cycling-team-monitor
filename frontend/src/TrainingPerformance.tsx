import { Link } from 'react-router'

import { dateLabel } from './athleteApi'
import { DatedChart } from './TrainingGraphs'
import { DataBadge } from './TrainingSummary'
import { effortClock, effortReasonLabels, measure, signedMeasure } from './trainingApi'
import type { EffortPoint, Measurements } from './trainingApi'

function EffortHistory({ attempts, label }: { attempts: EffortPoint[]; label: string }) {
  return <div className="table-wrap training-table" role="region" tabIndex={0} aria-label={label}>
    <table>
      <thead><tr><th scope="col">Дата и отрезок</th><th scope="col">Мощность</th><th scope="col">База</th><th scope="col">Отклонение</th></tr></thead>
      <tbody>{attempts.map(attempt => <tr key={attempt.workout_id}>
        <th scope="row"><Link to={'/workouts/' + attempt.workout_id}>{dateLabel(attempt.date)}</Link>
          <small>{effortClock(attempt.start_seconds)}–{effortClock(attempt.start_seconds + attempt.duration_seconds)} от старта</small>
        </th>
        <td>{measure(attempt.mean_watts, 'Вт')}</td>
        <td>{measure(attempt.baseline_watts, 'Вт')}
          {attempt.baseline_workout_ids.length > 0 && <details className="entry-item">
            <summary>Попыток в базе: {attempt.baseline_workout_ids.length}</summary>
            <ul className="training-list">{attempt.baseline_workout_ids.map(id =>
              <li key={id}><Link to={'/workouts/' + id}>Тренировка {id}</Link></li>)}</ul>
          </details>}
        </td>
        <td>{signedMeasure(attempt.delta_pct, '%')}
          {attempt.reason && <small>{effortReasonLabels[attempt.reason] ?? 'Сравнение недоступно.'}</small>}
        </td>
      </tr>)}</tbody>
    </table>
  </div>
}

export function TrainingPerformance({ data }: { data: Measurements }) {
  const { performance, workouts } = data
  const groups = new Map<string, EffortPoint[]>()
  for (const attempt of performance.efforts) {
    const key = JSON.stringify([attempt.protocol, attempt.duration_seconds])
    const group = groups.get(key) ?? []
    group.push(attempt)
    groups.set(key, group)
  }
  return <div className="analytics-stack">
    <section className="panel">
      <h2>История FTP</h2>
      <DatedChart title="FTP" unit="Вт" step
        description="На графике — значения FTP из истории профиля и даты их действия."
        readings={performance.ftp.map(point => ({
          date: point.date, value: point.watts, note: 'Действует с ' + dateLabel(point.effective_from),
        }))}
      />
    </section>
    <section className="panel">
      <div className="chart-header"><h2>Контрольные отрезки</h2><DataBadge status={performance.status} /></div>
      <p className="muted">Сервер сравнивает попытки одного протокола и одинаковой длительности.</p>
      {groups.size === 0 && <p>За выбранный период нет размеченных контрольных отрезков.</p>}
      {[...groups.entries()].map(([key, group]) => {
        const { protocol, duration_seconds } = group[0]
        const label = protocol + ' · ' + measure(duration_seconds, 'с')
        return <section key={key} className="training-subsection">
          <h3>{label}</h3>
          <EffortHistory label={'История попыток · ' + label} attempts={[...group].reverse()} />
        </section>
      })}
      {workouts.length > 0 && <details className="entry-item">
        <summary>Открыть FIT-тренировку для разметки</summary>
        <ul className="training-list">{[...workouts].reverse().map(workout =>
          <li key={workout.workout_id}><Link to={'/workouts/' + workout.workout_id}>
            {dateLabel(workout.date)} · тренировка {workout.workout_id}
          </Link></li>)}</ul>
      </details>}
    </section>
    {performance.repeated_changes.map(change => <section key={JSON.stringify([change.protocol, change.duration_seconds])} className="panel">
      <h2>{change.direction === 'improved' ? 'Повторное повышение мощности' : 'Повторное снижение мощности'}</h2>
      <p>{change.protocol} · {measure(change.duration_seconds, 'с')} · общая база {measure(change.baseline_watts, 'Вт')}</p>
      <EffortHistory attempts={change.workouts} label={'Повторные контрольные попытки · ' + change.protocol} />
      <details className="entry-item">
        <summary>Исходные попытки общей базы</summary>
        <ul className="training-list">{change.baseline_attempts.map(attempt =>
          <li key={attempt.workout_id}><Link to={'/workouts/' + attempt.workout_id}>{dateLabel(attempt.date)}</Link>
            {' · '}{measure(attempt.mean_watts, 'Вт')}</li>)}</ul>
      </details>
    </section>)}
  </div>
}
