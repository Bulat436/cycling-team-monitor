import { useState } from 'react'
import { Link } from 'react-router'

import { QueryFeedback } from './AthleteFeedback'
import { useAthleteResource } from './athleteApi'
import { AttentionBadge, DataBadge, recoveryStatus } from './TrainingSummary'
import { attentionLabels, measure, signedMeasure, statusLabels, trainingApi, trainingAthletePath } from './trainingApi'
import type { Attention, DataStatus, TeamTraining } from './trainingApi'

type Row = TeamTraining['athletes'][number]
const order: Record<Attention, number> = { high: 3, moderate: 2, informational: 1 }

function dataStatus(row: Row): DataStatus {
  if (row.warnings.some(warning => warning.type === 'insufficient_data') || row.current_week.status === 'missing') {
    return 'insufficient'
  }
  return row.current_week.status
}

function compareNames(first: Row, second: Row) {
  return first.name.localeCompare(second.name, 'ru') || first.athlete_id - second.athlete_id
}

export default function TrainingTeam() {
  const [status, setStatus] = useState('all')
  const [attention, setAttention] = useState('all')
  const [sort, setSort] = useState('priority')
  const query = useAthleteResource<TeamTraining>(trainingApi + '/team', 60_000)
  const athletes = query.data?.athletes ?? []
  const rows = athletes.filter(row =>
    (status === 'all' || dataStatus(row) === status) &&
    (attention === 'all' || (row.max_attention ?? 'none') === attention),
  )
  if (sort === 'name') rows.sort(compareNames)
  if (sort === 'low-first') rows.sort((first, second) => {
    const a = first.max_attention ? order[first.max_attention] : 0
    const b = second.max_attention ? order[second.max_attention] : 0
    return a - b || compareNames(first, second)
  })

  return <div className="analytics-stack">
    <header className="page-heading"><div>
      <h1>Нагрузка и восстановление</h1>
      <p>Недельная нагрузка, восстановление и предупреждения по спортсменам команды.</p>
    </div></header>
    <QueryFeedback loading={query.isPending} error={query.error} retry={() => void query.refetch()} />
    {query.data && <>
      {athletes.length > 0 && <section className="panel training-filters" aria-label="Фильтры команды">
        <label>Достаточность данных<select value={status} onChange={event => setStatus(event.target.value)}>
          <option value="all">Все статусы</option>
          {(['sufficient', 'partial', 'insufficient'] as const).map(value =>
            <option value={value} key={value}>{statusLabels[value]}</option>)}
        </select></label>
        <label>Уровень внимания<select value={attention} onChange={event => setAttention(event.target.value)}>
          <option value="all">Все уровни</option>
          {Object.entries(attentionLabels).map(([value, label]) => <option value={value} key={value}>{label}</option>)}
          <option value="none">Нет предупреждений</option>
        </select></label>
        <label>Сортировка<select value={sort} onChange={event => setSort(event.target.value)}>
          <option value="priority">Сначала высокий уровень внимания</option>
          <option value="low-first">Сначала низкий уровень внимания</option>
          <option value="name">По имени</option>
        </select></label>
      </section>}
      {rows.length ? <ul className="training-team">
        {rows.map(row => <li key={row.athlete_id} className="panel training-member">
          <div className="chart-header">
            <h2><Link to={trainingAthletePath(row.athlete_id)}>{row.name}</Link></h2>
            <AttentionBadge attention={row.max_attention} />
          </div>
          {(row.group || !row.active) && <p className="muted">{[row.group, !row.active && 'Неактивный аккаунт'].filter(Boolean).join(' · ')}</p>}
          <dl className="measurements">
            <div><dt>TSS за 7 дней</dt><dd>{measure(row.current_week.recorded_tss)}</dd>
              <dd className="muted">Известно дней: {row.current_week.coverage.known_days} из {row.current_week.coverage.days}</dd>
            </div>
            <div><dt>Отклонение от нормы</dt><dd>{signedMeasure(row.current_week.delta_pct, '%')}</dd></div>
            <div><dt>Восстановление</dt><dd>{recoveryStatus(row.recovery)}</dd>
              <dd className="muted">Оценено дней: {row.recovery.evaluated_dates.length} из {row.current_week.coverage.days}</dd>
              {row.recovery.deterioration_dates.length > 0 && <dd className="muted">
                Дней с ухудшением: {row.recovery.deterioration_dates.length}
              </dd>}
            </div>
          </dl>
          <div className="chart-header">
            <DataBadge status={dataStatus(row)} />
            {row.warnings.length > 0 && <Link to={trainingAthletePath(row.athlete_id) + '?tab=relationships'}>
              Предупреждения: {row.warnings.length}
            </Link>}
            {!row.warnings.length && <p className="muted">Предупреждения: 0</p>}
          </div>
        </li>)}
      </ul> : <p className="panel muted" role="status">
        {athletes.length ? 'По выбранным фильтрам никого нет.' : 'В команде пока нет спортсменов.'}
      </p>}
    </>}
  </div>
}
