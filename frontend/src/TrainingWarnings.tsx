import { Link } from 'react-router'

import { dateLabel } from './athleteApi'
import { AttentionBadge, CoverageDetails, DataBadge } from './TrainingSummary'
import { factUnits, measure, metricLabel, periodLabel, signedMeasure } from './trainingApi'
import type { AnalyticsWarning } from './trainingApi'

const baselineLabels = { individual: 'Индивидуальная база', previous_week: 'Предыдущая неделя', required_minimum: 'Необходимый минимум' }
const workoutRoles = { load: 'Нагрузка', control_attempt: 'Контрольная попытка', control_baseline: 'Попытка из базы' }
const gapLabels: Record<string, string> = {
  insufficient_comparable_load_history: 'Мало сопоставимых недель нагрузки',
  insufficient_evaluable_recovery_days: 'Мало дней с оценкой восстановления',
  sparse_diary: 'Недостаточно записей дневника',
}
const warningAnchor = (id: string) => 'training-warning-' + id

export function WarningLink({ warning }: { warning: AnalyticsWarning }) {
  const anchor = warningAnchor(warning.id)
  return <a href={'#' + anchor} onClick={() => {
    const details = document.getElementById(anchor)
    if (details instanceof HTMLDetailsElement) {
      details.open = true
      details.querySelector('summary')?.focus({ preventScroll: true })
    }
  }}>{warning.title}</a>
}

function WarningEvidence({ warning }: { warning: AnalyticsWarning }) {
  const { coverage } = warning
  return <div className="training-warning-body">
    <p className="muted">{periodLabel(warning.period)}</p>
    <p>{warning.explanation}</p>
    {warning.protocol && <p>Протокол <strong>{warning.protocol}</strong> · {measure(warning.duration_seconds, 'с')}</p>}
    {warning.facts.length > 0 && <>
      <h3>Фактические значения</h3>
      <div className="table-wrap training-table" tabIndex={0} role="region" aria-label={'Доказательства · ' + warning.title}>
        <table>
          <thead><tr><th scope="col">Показатель и дата</th><th scope="col">Факт</th><th scope="col">База сравнения</th><th scope="col">Отклонение</th></tr></thead>
          <tbody>{warning.facts.map((fact, index) => <tr key={index}>
            <th scope="row">{metricLabel(fact.metric)}
              {fact.date ? <small>{dateLabel(fact.date)}</small> : fact.period && <small>{periodLabel(fact.period)}</small>}
              {fact.workout_id != null && <small><Link to={'/workouts/' + fact.workout_id}>Тренировка {fact.workout_id}</Link></small>}
              {fact.load_date && <small>
                Нагрузка {dateLabel(fact.load_date)} · {fact.lag_days === 0 ? 'тот же день' : `задержка ${fact.lag_days} дн.`}
              </small>}
            </th>
            <td>{measure(fact.value, factUnits[fact.unit])}</td>
            <td>{measure(fact.baseline, factUnits[fact.unit])}
              {fact.baseline_kind && <small>{baselineLabels[fact.baseline_kind]}</small>}
              {fact.reference_period && <small>{periodLabel(fact.reference_period)}</small>}
              {fact.observations != null && <small>Наблюдений: {fact.observations}</small>}
            </td>
            <td>{signedMeasure(fact.delta, factUnits[fact.unit])}
              {fact.delta_pct != null && <small>{signedMeasure(fact.delta_pct, '%')}</small>}
            </td>
          </tr>)}</tbody>
        </table>
      </div>
    </>}
    <div className="chart-header"><h3>Покрытие данных</h3><DataBadge status={coverage.status} /></div>
    {coverage.load && <CoverageDetails coverage={coverage.load} />}
    {coverage.load_reference_blocks.length > 0 && <details className="entry-item">
      <summary>Недели сравнения</summary>
      <ul className="training-list">{coverage.load_reference_blocks.map(block =>
        <li key={block.period.start}>{periodLabel(block.period)} · {measure(block.tss, 'TSS')} ·
          {' '}{block.coverage.known_days} из {block.coverage.days} дней с TSS</li>)}</ul>
    </details>}
    <ul className="training-list">
      {coverage.evaluated_recovery_dates.length > 0 && <li>Оценённые дни восстановления · {coverage.evaluated_recovery_dates.map(dateLabel).join(', ')}</li>}
      {coverage.worsened_recovery_dates.length > 0 && <li>Дни с ухудшением · {coverage.worsened_recovery_dates.map(dateLabel).join(', ')}</li>}
      {coverage.diary_period && <li>Анкет заполнено: {coverage.diary_filled_days ?? 'нет данных'} · {periodLabel(coverage.diary_period)}</li>}
      {coverage.missing_diary_dates.length > 0 && <li>Дневник пропущен · {coverage.missing_diary_dates.map(dateLabel).join(', ')}</li>}
      {coverage.performance_baseline_attempts != null && <li>
        Попыток в базе: {coverage.performance_baseline_attempts} · новых: {coverage.performance_recent_attempts}
      </li>}
      {coverage.gaps.map(gap => <li key={gap}>{gapLabels[gap] ?? gap}</li>)}
    </ul>
    {warning.workouts.length > 0 && <>
      <h3>Связанные тренировки</h3>
      <ul className="training-list">{warning.workouts.map(workout => <li key={workout.workout_id}>
        <Link to={'/workouts/' + workout.workout_id}>{dateLabel(workout.date)} · тренировка {workout.workout_id}</Link>
        {' · '}{workoutRoles[workout.role]}
      </li>)}</ul>
    </>}
    {warning.limitations.length > 0 && <>
      <h3>Ограничения интерпретации</h3>
      <ul className="training-list muted">{warning.limitations.map(limit => <li key={limit}>{limit}</li>)}</ul>
    </>}
  </div>
}

export function TrainingWarnings({ warnings }: { warnings: AnalyticsWarning[] }) {
  return <section className="panel">
    <h2>Активные предупреждения</h2>
    <p className="muted">Уровень внимания определяет очерёдность просмотра. Предупреждения отражают последние наблюдения.</p>
    {warnings.length === 0 && <p>Активных предупреждений нет.</p>}
    {warnings.map(warning => <details key={warning.id} id={warningAnchor(warning.id)} className="entry-item training-warning">
      <summary>{warning.title} <AttentionBadge attention={warning.attention} /></summary>
      <WarningEvidence warning={warning} />
      {warning.related_warning_ids.some(id => warnings.some(item => item.id === id)) && <p>
        С этим эпизодом также связано{' '}
        {warnings.filter(item => warning.related_warning_ids.includes(item.id)).map((item, index) =>
          <span key={item.id}>{index > 0 && ', '}<WarningLink warning={item} /></span>)}
      </p>}
    </details>)}
  </section>
}
