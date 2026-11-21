import { Link } from 'react-router'

import { dateLabel, recordedAt } from './athleteApi'
import { DataBadge } from './TrainingSummary'
import { TrainingWarnings, WarningLink } from './TrainingWarnings'
import { measure, metricLabel, periodLabel, recoveryMetrics, signedMeasure } from './trainingApi'
import type { AnalyticsWarning, EffortPoint, LoadEpisode, Measurements, RecoveryDay, WorkoutLoad } from './trainingApi'

interface TimelineDay {
  workouts: WorkoutLoad[]
  diary?: RecoveryDay
  efforts: EffortPoint[]
  reactions: LoadEpisode['reactions']
  recovered: boolean
  warnings: AnalyticsWarning[]
}

function Timeline({ data, warnings }: { data: Measurements; warnings: AnalyticsWarning[] }) {
  const days = new Map<string, TimelineDay>()
  function day(date: string) {
    if (date < data.period.start || date > data.period.end) return
    let found = days.get(date)
    if (!found) {
      found = { workouts: [], efforts: [], reactions: [], recovered: false, warnings: [] }
      days.set(date, found)
    }
    return found
  }
  for (const workout of data.workouts) day(workout.date)?.workouts.push(workout)
  for (const attempt of data.performance.efforts) day(attempt.date)?.efforts.push(attempt)
  for (const warning of warnings) day(warning.period.end)?.warnings.push(warning)
  for (const entry of data.recovery) {
    const event = entry.has_entry ? day(entry.date) : undefined
    if (event) event.diary = entry
  }
  for (const episode of data.episodes) {
    for (const reaction of episode.reactions) day(reaction.date)?.reactions.push(reaction)
    for (const date of episode.normalization_dates) {
      const event = day(date)
      if (event) event.recovered = true
    }
  }

  return <section className="panel">
    <h2>Временная шкала</h2>
    <p className="muted">{periodLabel(data.period)} · {data.timezone}</p>
    <p>Шкала показывает порядок по датам. Для событий одного дня последовательность неизвестна. Совпадение по времени само по себе не устанавливает причину.</p>
    {days.size === 0 ? <p className="muted">За выбранный период событий нет.</p> :
      <ol className="training-timeline">{[...days.entries()].sort(([a], [b]) => b.localeCompare(a)).map(([date, event]) =>
        <li key={date}>
          <h3><time dateTime={date}>{dateLabel(date)}</time></h3>
          <ul className="training-list">
            {event.workouts.map(workout => <li key={'workout-' + workout.workout_id}>
              <Link to={'/workouts/' + workout.workout_id}>{workout.intense ? 'Интенсивная тренировка' : 'Тренировка'} {workout.workout_id}</Link>
              {' · '}{recordedAt(workout.started_at, data.timezone)} · {measure(workout.tss, 'TSS')}
              {workout.duplicate_candidates.length > 0 && ' · Возможный дубликат'}
            </li>)}
            {event.diary && <li><strong>Дневник:</strong>{' '}
              {recoveryMetrics.flatMap(metric => {
                const point = event.diary?.metrics[metric.name]
                return point?.value == null ? [] : [metric.label + ' ' + measure(point.value, metric.name === 'sleep_minutes' ? 'мин' : 'из 5')]
              }).join(', ') || 'Запись без доступных показателей'}
            </li>}
            {event.efforts.map(attempt => <li key={'effort-' + attempt.workout_id}>
              <Link to={'/workouts/' + attempt.workout_id}>Контрольный отрезок · {attempt.protocol}</Link>
              {' · '}{measure(attempt.mean_watts, 'Вт')}
              {attempt.delta_pct != null && ' · ' + signedMeasure(attempt.delta_pct, '%') + ' к базе'}
            </li>)}
            {event.reactions.map(reaction => {
              const unit = reaction.workout_id != null ? 'Вт' : reaction.metric === 'sleep_minutes' ? 'мин' : 'балл.'
              return <li key={reaction.metric + ':' + reaction.workout_id}>
                {metricLabel(reaction.metric)} · {signedMeasure(reaction.delta, unit)} от нормы
                {' · '}нагрузка {dateLabel(reaction.load_date)} · {reaction.same_date_only ? 'совпадение по дате' : `задержка ${reaction.lag_days} дн.`}
              </li>
            })}
            {event.recovered && <li>Наблюдаемый возврат показателей к индивидуальной норме</li>}
            {event.warnings.map(warning => <li key={warning.id}><WarningLink warning={warning} /></li>)}
          </ul>
        </li>)}</ol>}
  </section>
}

function LagCorrelations({ data }: { data: Measurements }) {
  const available = data.correlations.filter(item => item.coefficient != null)
  return <section className="panel">
    <h2>Корреляции с задержкой</h2>
    <p className="muted">Сервер анализирует до 180 завершённых дней. Соседние наблюдения могут зависеть друг от друга.</p>
    {available.length === 0 ? <p>Недостаточно пар наблюдений с изменяющимися значениями для оценки корреляций.</p> :
      <details className="entry-item">
        <summary>Коэффициенты Спирмена и покрытие</summary>
        <div className="table-wrap training-table" tabIndex={0} role="region" aria-label="Таблица корреляций">
          <table><thead><tr>
            <th scope="col">Показатель</th><th scope="col">Задержка</th><th scope="col">Коэффициент</th><th scope="col">Пары и устойчивость</th>
          </tr></thead><tbody>{available.map(item => <tr key={item.metric + ':' + item.lag_days}>
            <th scope="row">{metricLabel(item.metric)}</th><td>{item.lag_days} дн.</td>
            <td>{signedMeasure(item.coefficient)}</td><td>{item.pairs} пар, подтверждено {item.confirmed_load_pairs}
              <small>{item.stable ? 'Устойчивый знак' : 'Устойчивость не подтверждена'}</small>
              <DataBadge status={item.status} />
            </td>
          </tr>)}</tbody></table>
        </div>
      </details>}
  </section>
}

export function TrainingRelationships({ data, warnings }: { data: Measurements | undefined; warnings: AnalyticsWarning[] | undefined }) {
  return <div className="analytics-stack">
    {warnings && <TrainingWarnings warnings={warnings} />}
    {data && <>
      <Timeline data={data} warnings={warnings ?? []} />
      <LagCorrelations data={data} />
    </>}
  </div>
}
