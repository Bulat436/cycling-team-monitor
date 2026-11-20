import { useState } from 'react'
import { Link } from 'react-router'

import { QueryFeedback } from './AthleteFeedback'
import {
  dateLabel, diaryFields, durationLabel, metricReasonLabels, recordedAt, useAthleteResource,
} from './athleteApi'
import { DistributionChart, TrendChart } from './Charts'
import { periodQuery } from './coachApi'
import type { AthleteAnalytics, DateRange, SelectedPeriod, WorkoutPoint, ZoneDistribution } from './coachApi'
import { PeriodCaption, PeriodPicker } from './PeriodPicker'
import { displayMeasure } from './Workouts'

const workoutMetrics = [
  { key: 'timer_seconds', name: 'Время по таймеру', unit: 'мин', scale: 1 / 60 },
  { key: 'elapsed_seconds', name: 'Полная длительность', unit: 'мин', scale: 1 / 60 },
  { key: 'distance_m', name: 'Дистанция', unit: 'км', scale: .001 },
  { key: 'avg_power', name: 'Средняя мощность', unit: 'Вт', scale: 1 },
  { key: 'np', name: 'NP', unit: 'Вт', scale: 1 },
  { key: 'tss', name: 'TSS', unit: '', scale: 1 },
  { key: 'rpe', name: 'RPE', unit: '', scale: 1 },
  { key: 'rpe_load', name: 'Нагрузка session-RPE', unit: 'усл. ед.', scale: 1 },
] as const

const shortFormatter = new Intl.DateTimeFormat('ru', { day: '2-digit', month: 'short', timeZone: 'UTC' })
const dayCoordinate = (date: string) => Date.parse(date + 'T00:00:00Z')
const shortDate = (date: string) => shortFormatter.format(dayCoordinate(date))
const shortTick = (value: number) => shortFormatter.format(value)
const windowLabel = (period: DateRange) => shortDate(period.start) + '–' + shortDate(period.end)
const reasonLabel = (reason?: string) => reason && metricReasonLabels[reason] || 'Нет данных'

function WellnessGraph({ data }: { data: AthleteAnalytics['wellness'] }) {
  const [field, setField] = useState<typeof diaryFields[number]['name']>('sleep_quality')
  const title = diaryFields.find(item => item.name === field)?.label ?? 'Дневник'
  return <section className="panel">
    <div className="chart-header">
      <h2>Динамика самочувствия</h2>
      <label>Показатель дневника<select value={field} onChange={event => setField(event.target.value as typeof field)}>
        {diaryFields.map(item => <option key={item.name} value={item.name}>{item.label}</option>)}
      </select></label>
    </div>
    <TrendChart title={title} unit="" domain={[1, 5]} ticks={shortTick} baseline={data.some(entry => entry.baseline !== null)}
      data={data.map(entry => ({ x: dayCoordinate(entry.date), value: entry.scores?.[field] ?? null, baseline: entry.baseline?.[field] ?? null }))} />
    <p className="muted">Индивидуальный уровень — среднее по минимум трём анкетам за предыдущие 28 дней.</p>
  </section>
}

function WorkoutGraph({ points, timezone }: { points: WorkoutPoint[]; timezone: string }) {
  const [selected, setSelected] = useState<typeof workoutMetrics[number]['key']>('timer_seconds')
  const metric = workoutMetrics.find(item => item.key === selected) ?? workoutMetrics[0]
  let formatter = shortFormatter
  try {
    formatter = new Intl.DateTimeFormat('ru', { day: '2-digit', month: 'short', timeZone: timezone })
  } catch (error) {
    if (!(error instanceof RangeError)) throw error
  }
  return <section className="panel">
    <div className="chart-header">
      <h2>Динамика тренировок</h2>
      <label>Показатель тренировки<select value={selected} onChange={event => setSelected(event.target.value as typeof selected)}>
        {workoutMetrics.map(item => <option key={item.key} value={item.key}>{item.name}</option>)}
      </select></label>
    </div>
    <TrendChart title={metric.name} unit={metric.unit} ticks={value => formatter.format(value * 1000)}
      data={points.map(point => {
        const value = point[metric.key]
        return { x: point.started_at, value: value === null ? null : value * metric.scale }
      })} />
    {points.length > 0 && <details className="entry-item">
      <summary>Значения и причины пропусков</summary>
      <div className="table-wrap">
        <table><thead><tr><th scope="col">Тренировка</th><th scope="col">{metric.name}</th><th scope="col">Причина пропуска</th></tr></thead>
          <tbody>{points.map(point => <tr key={point.id}>
            <th scope="row"><Link to={'/workouts/' + point.id}>{recordedAt(point.started_at, timezone)}</Link></th>
            <td>{displayMeasure(point[metric.key], metric.unit, metric.scale)}</td>
            <td>{point[metric.key] === null ? reasonLabel(point.reasons[metric.key]) : '—'}</td>
          </tr>)}</tbody>
        </table>
      </div>
    </details>}
  </section>
}

function WeeklyGraph({ weeks }: { weeks: AthleteAnalytics['weeks'] }) {
  const [metric, setMetric] = useState<'tss' | 'rpe_load'>('tss')
  return <section className="panel">
    <div className="chart-header">
      <h2>Недельная нагрузка</h2>
      <label>Вид нагрузки<select value={metric} onChange={event => setMetric(event.target.value as typeof metric)}>
        <option value="tss">TSS</option><option value="rpe_load">session-RPE</option>
      </select></label>
    </div>
    <DistributionChart title={metric === 'tss' ? 'TSS по неделям' : 'session-RPE по неделям'} unit=""
      data={weeks.map(week => ({ name: windowLabel(week), value: week[metric] }))} />
    <div className="table-wrap">
      <table><thead><tr><th scope="col">Неделя</th><th scope="col">Тренировки</th><th scope="col">Таймер</th><th scope="col">Дистанция</th>
        <th scope="col">TSS</th><th scope="col">session-RPE</th></tr></thead>
        <tbody>{weeks.map(week => <tr key={week.start}>
          <th scope="row">{windowLabel(week)}</th><td>{week.workouts}</td><td>{durationLabel(week.timer_seconds)}</td>
          <td>{displayMeasure(week.distance_m, 'км', .001)}</td>
          <td>{displayMeasure(week.tss, '')}<div className="muted">{week.tss_count} из {week.workouts}</div></td>
          <td>{displayMeasure(week.rpe_load, '')}<div className="muted">{week.rpe_load_count} из {week.workouts}</div></td>
        </tr>)}</tbody>
      </table>
    </div>
  </section>
}

function ZonesChart({ title, distribution, unit }: { title: string; distribution: ZoneDistribution; unit: string }) {
  const bars = distribution.bands.map(band => ({
    name: band.name + ' · ' + band.minimum + (band.maximum === null ? '+' : '–' + band.maximum),
    value: band.seconds / 60,
  }))
  if (distribution.outside_seconds !== null) bars.push({ name: 'Вне зон', value: distribution.outside_seconds / 60 })
  return <section className="panel">
    <h2>{title}</h2>
    {distribution.workouts_with_data > 0 && <p className="muted">Границы зон в {unit}. Тренировок с данными {distribution.workouts_with_data} из {distribution.workouts}.</p>}
    <DistributionChart title={title} data={bars} unit="мин" horizontal />
    {distribution.unknown_seconds !== null && <p className="muted">Время без показаний · {durationLabel(distribution.unknown_seconds)}</p>}
    {Object.keys(distribution.omitted).length > 0 && <ul className="omitted-list">
      {Object.entries(distribution.omitted).map(([reason, count]) => <li key={reason}>{count} · {reasonLabel(reason)}</li>)}
    </ul>}
  </section>
}

export default function CoachAnalytics({ athleteId }: { athleteId: number }) {
  const [period, setPeriod] = useState<SelectedPeriod>({ preset: '28' })
  const query = useAthleteResource<AthleteAnalytics>('/analytics/athletes/' + athleteId + '?' + periodQuery(period).toString())
  const data = query.data
  return <>
    <header className="page-heading"><div><h1>Аналитика</h1>{data && <PeriodCaption period={data.period} />}</div></header>
    <section className="panel coach-toolbar"><PeriodPicker value={period} onChange={setPeriod} actual={data?.period} /></section>
    <QueryFeedback loading={query.isPending} error={query.error} retry={() => void query.refetch()} />
    {data && <div className="analytics-stack">
      <dl className="stats coach-stats">
        <div><dt>Тренировки</dt><dd>{data.load.workouts}</dd></div>
        <div><dt>Время по таймеру</dt><dd>{durationLabel(data.load.timer_seconds)}</dd></div>
        <div><dt>Дистанция</dt><dd>{displayMeasure(data.load.distance_m, 'км', .001)}</dd></div>
        <div><dt>TSS</dt><dd>{displayMeasure(data.load.tss, '')}</dd></div>
      </dl>
      <WellnessGraph data={data.wellness} />
      <WorkoutGraph points={data.workouts} timezone={data.timezone} />
      <WeeklyGraph weeks={data.weeks} />
      <div className="analytics-pair">
        <ZonesChart title="Распределение по зонам мощности" distribution={data.power_zones} unit="ваттах" />
        <ZonesChart title="Распределение по зонам пульса" distribution={data.heart_rate_zones} unit="уд/мин" />
      </div>
      <section className="panel">
        <h2>Динамика FTP</h2>
        <TrendChart title="FTP" unit="Вт" ticks={shortTick} step data={data.ftp.map(point => ({ x: dayCoordinate(point.date), value: point.watts }))} />
        {data.ftp.length > 0 && <p className="muted">Последнее значение действует с {dateLabel(data.ftp.at(-1)!.effective_from)}.</p>}
      </section>
    </div>}
  </>
}
