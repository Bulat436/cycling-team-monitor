import { useState } from 'react'
import { Link } from 'react-router'

import { QueryFeedback } from './AthleteFeedback'
import { dateLabel, diaryFields, durationLabel, recordedAt, useAthleteResource } from './athleteApi'
import { alertLabels, periodQuery } from './coachApi'
import type { Advisory, SelectedPeriod, TeamOverview, TeamRow } from './coachApi'
import { StatusLabel } from './OnelapFit'
import { PeriodPicker } from './PeriodPicker'
import { displayMeasure } from './Workouts'

const accountLabels = { active: 'Активен', inactive: 'Деактивирован', invited: 'Ждёт активации', password_reset: 'Сброс пароля' }

export function Advisories({ alerts }: { alerts: Advisory[] }) {
  if (!alerts.length) return <span className="muted">Нет предупреждений</span>
  return <ul className="coach-alerts">{alerts.map(item => (
    <li key={item.code}><span className="status pending">
      {alertLabels[item.code]}{item.value !== null && ' · ' + item.value}
    </span></li>
  ))}</ul>
}

function DailyStatus({ row }: { row: TeamRow }) {
  const entry = row.wellness
  return (
    <div className="coach-cell">
      <Link to={'/athletes/' + row.athlete_id + '/diary'}>{entry ? 'Заполнен' : 'Не заполнен'}</Link>
      <span className="muted">{dateLabel(row.today)}</span>
      {entry && <details>
        <summary>Сон {entry.sleep_quality}/5 · Усталость {entry.general_fatigue}/5</summary>
        <dl className="coach-scores">
          {diaryFields.map(field => <div key={field.name}><dt>{field.label}</dt><dd>{entry[field.name]} из 5</dd></div>)}
        </dl>
      </details>}
    </div>
  )
}

function TableRow({ row }: { row: TeamRow }) {
  const path = '/athletes/' + row.athlete_id
  const last = row.last_workout
  return (
    <tr>
      <th scope="row">
        <div className="coach-cell">
          <Link className="athlete-link" to={path}>{row.name}</Link>
          <span className="muted">{row.group ?? 'Без группы'}</span>
          <span className={'status ' + (row.account === 'active' ? 'active' : 'neutral')}>{accountLabels[row.account]}</span>
        </div>
      </th>
      <td><DailyStatus row={row} /></td>
      <td>{row.wellness ? row.wellness.illness_symptoms ? <span className="status pending">Отмечены</span> : 'Нет' : 'Нет данных'}</td>
      <td>{last ? <div className="coach-cell">
        <Link to={'/workouts/' + last.id}>{recordedAt(last.started_at, row.timezone)}</Link>
        <span className="muted">{durationLabel(last.timer_seconds)} · {displayMeasure(last.distance_m, 'км', .001)}</span>
      </div> : <span className="muted">Нет тренировок</span>}</td>
      <td><div className="coach-cell">
        <span>TSS · {displayMeasure(row.load.tss, '')}</span>
        <span>session-RPE · {displayMeasure(row.load.rpe_load, '')}</span>
        <span className="muted">TSS у {row.load.tss_count} из {row.load.workouts}</span>
      </div></td>
      <td><div className="coach-cell">
        <span>{last ? last.rpe === null ? 'Не заполнен' : last.rpe + ' из 10' : 'Нет данных'}</span>
        <span className="muted">За период {row.load.rpe_count} из {row.load.workouts}</span>
      </div></td>
      <td><div className="coach-cell">
        <StatusLabel connection={row.onelap} />
        {row.onelap.last_success_at !== null && <span className="muted">{recordedAt(row.onelap.last_success_at, row.timezone)}</span>}
        <Link to={path + '#onelapfit'}>Журнал</Link>
      </div></td>
      <td><Advisories alerts={row.alerts} /></td>
    </tr>
  )
}

export function Team() {
  const [period, setPeriod] = useState<SelectedPeriod>({ preset: '7' })
  const [filters, setFilters] = useState({
    search: '', group: '', account: '', diary: '', warning: '', sort: 'name', direction: 'asc',
  })
  const params = periodQuery(period)
  for (const [key, value] of Object.entries(filters)) if (value) params.set(key, value)
  const query = useAthleteResource<TeamOverview>('/analytics/team?' + params.toString(), 30_000, true)
  const data = query.data
  const setFilter = (key: keyof typeof filters, value: string) => setFilters(current => ({ ...current, [key]: value }))

  return (
    <>
      <header className="page-heading">
        <div><h1>Команда</h1><p>Дневники, тренировки и предупреждения</p></div>
        <div className="actions">
          <Link className="button secondary" to="/alerts/settings">Настроить предупреждения</Link>
          <Link className="button" to="/access">Пригласить спортсмена</Link>
        </div>
      </header>
      {data && (
        <dl className="stats coach-stats">
          <div><dt>Спортсмены</dt><dd>{data.total}</dd></div>
          <div><dt>Дневники заполнены</dt><dd>{data.filled}</dd></div>
          <div><dt>С предупреждениями</dt><dd>{data.alerted}</dd></div>
          <div><dt>Ожидают пароль</dt><dd>{data.pending}</dd></div>
        </dl>
      )}
      <section className="panel coach-toolbar">
        <h2>Период нагрузки</h2>
        <PeriodPicker value={period} onChange={setPeriod} />
        <div className="team-filters">
          <label>Поиск по имени<input type="search" value={filters.search} onChange={event => setFilter('search', event.target.value)} maxLength={120} /></label>
          <label>Группа<select value={filters.group} onChange={event => setFilter('group', event.target.value)}>
            <option value="">Все группы</option>
            {[...new Set([...(data?.groups ?? []), ...(filters.group ? [filters.group] : [])])].map(group => <option key={group}>{group}</option>)}
          </select></label>
          <label>Аккаунт<select value={filters.account} onChange={event => setFilter('account', event.target.value)}>
            <option value="">Все аккаунты</option><option value="active">Активные</option><option value="pending">Ожидают пароль</option><option value="inactive">Деактивированные</option>
          </select></label>
          <label>Дневник<select value={filters.diary} onChange={event => setFilter('diary', event.target.value)}>
            <option value="">Любой статус</option><option value="filled">Заполнен</option><option value="missing">Не заполнен</option>
          </select></label>
          <label>Предупреждения<select value={filters.warning} onChange={event => setFilter('warning', event.target.value)}>
            <option value="">Все спортсмены</option><option value="any">Есть предупреждения</option>
            {Object.entries(alertLabels).map(([code, label]) => <option key={code} value={code}>{label}</option>)}
          </select></label>
          <label>Сортировка<select value={filters.sort} onChange={event => setFilter('sort', event.target.value)}>
            <option value="name">Имя</option><option value="last_workout">Последняя тренировка</option>
            <option value="tss">TSS</option><option value="rpe_load">session-RPE</option><option value="alerts">Число предупреждений</option>
          </select></label>
          <label>Порядок<select value={filters.direction} onChange={event => setFilter('direction', event.target.value)}>
            <option value="asc">По возрастанию</option><option value="desc">По убыванию</option>
          </select></label>
        </div>
      </section>
      <section className="panel">
        <h2>Спортсмены команды</h2>
        {query.isPlaceholderData && <p role="status" className="muted">Обновляем таблицу…</p>}
        <QueryFeedback loading={query.isPending} error={query.error} retry={() => void query.refetch()} />
        {data && (data.rows.length === 0 ? <p className="muted">{data.total ? 'По выбранным фильтрам никого нет.' : 'В команде пока нет спортсменов.'}</p> : (
          <div className="table-wrap team-table-scroll" role="region" aria-label="Таблица спортсменов" tabIndex={0}>
            <table className="team-table">
              <thead><tr>
                <th scope="col">Спортсмен</th><th scope="col">Дневник сегодня</th><th scope="col">Симптомы</th>
                <th scope="col">Последняя тренировка</th><th scope="col">Нагрузка за период</th>
                <th scope="col">RPE</th><th scope="col">OnelapFit</th><th scope="col">Предупреждения</th>
              </tr></thead>
              <tbody>{data.rows.map(row => <TableRow key={row.athlete_id} row={row} />)}</tbody>
            </table>
          </div>
        ))}
      </section>
    </>
  )
}
