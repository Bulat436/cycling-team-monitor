import { useState } from 'react'

import { dateLabel } from './athleteApi'
import { TrendChart } from './Charts'
import { WeekSummary } from './TrainingSummary'
import { dayLabels, measure, metricLabel, recoveryMetrics, signedMeasure, statusLabels } from './trainingApi'
import type { Measurements, RecoveryDay } from './trainingApi'

interface Reading {
  date: string
  value: number | null
  baseline?: number | null
  delta?: number | null
  note?: string
}

const shortDate = new Intl.DateTimeFormat('ru', { day: 'numeric', month: 'short', timeZone: 'UTC' })
const ticks = (value: number) => shortDate.format(value)

export function DatedChart({ title, readings, unit, compare = false, step = false, description, domain }: {
  title: string; readings: Reading[]; unit: string; compare?: boolean; step?: boolean;
  description?: string; domain?: [number, number]
}) {
  const data = readings.map(({ date, value, baseline }) => ({ x: Date.parse(date), value, baseline }))
  return <>
    {description && <p className="muted">{description}</p>}
    <TrendChart
      title={title} data={data} unit={unit} ticks={ticks} baseline={compare} step={step}
      valueLabel={title} baselineLabel="Индивидуальная норма" domain={domain}
    />
    {readings.length > 0 && <details className="entry-item training-readings">
      <summary>Данные графика · {title}</summary>
      <div className="table-wrap training-table" role="region" aria-label={'Таблица · ' + title} tabIndex={0}>
        <table>
          <thead><tr>
            <th scope="col">Дата</th><th scope="col">{title} ({unit})</th>
            {compare && <><th scope="col">Норма</th><th scope="col">Отклонение</th></>}
          </tr></thead>
          <tbody>{readings.map(row => <tr key={row.date}>
            <th scope="row">{dateLabel(row.date)}{row.note && <small>{row.note}</small>}</th>
            <td>{measure(row.value, unit)}</td>
            {compare && <><td>{measure(row.baseline, unit)}</td><td>{signedMeasure(row.delta, unit)}</td></>}
          </tr>)}</tbody>
        </table>
      </div>
    </details>}
  </>
}

const curves = [
  { key: 'atl', title: 'ATL', status: 'atl_status', coverage: 'atl_coverage' },
  { key: 'ctl', title: 'CTL', status: 'ctl_status', coverage: 'ctl_coverage' },
  { key: 'tsb', title: 'TSB', status: 'tsb_status', coverage: 'ctl_coverage' },
] as const

export function TrainingLoad({ data }: { data: Measurements }) {
  return <div className="analytics-stack">
    <WeekSummary week={data.current_week} />
    <div className="training-chart-grid">
      <section className="panel">
        <h2>Дневной TSS</h2>
        <DatedChart title="Дневной TSS" unit="TSS" readings={data.load.map(day => ({
          date: day.date, value: day.tss,
          note: [
            dayLabels[day.day_status],
            day.preliminary && 'Показатели за сегодня предварительные',
            day.tss == null && day.recorded_tss != null && 'Записано ' + measure(day.recorded_tss, 'TSS'),
          ].filter(Boolean).join(' · '),
        }))} description="На графике — дни с рассчитанным TSS и подтверждённые дни отдыха." />
      </section>
      {curves.map(curve => <section key={curve.key} className="panel">
        <h2>{curve.title}</h2>
        <DatedChart title={curve.title} unit="TSS/день"
          description={curve.key === 'tsb' ? 'Разница CTL и ATL на конец дня.' :
            `Сглаженная нагрузка · ${curve.key === 'atl' ? data.rules.atl_days : data.rules.ctl_days} дн.`}
          readings={data.load.map(day => {
            const coverage = day[curve.coverage]
            return {
              date: day.date, value: day[curve.key],
              note: [
                statusLabels[day[curve.status]],
                coverage && `известно ${coverage.known_days} из ${coverage.days} дн. · подтверждено ${coverage.confirmed_days}`,
                day.preliminary && 'сегодня, предварительно',
              ].filter(Boolean).join(' · '),
            }
          })}
        />
      </section>)}
    </div>
  </div>
}

export function RecoveryGraph({ days, unsupported = [] }: { days: RecoveryDay[]; unsupported?: string[] }) {
  const [metric, setMetric] = useState('')
  const hasValues = (name: string) => days.some(day => day.metrics[name]?.value != null)
  const selected = metric || recoveryMetrics.find(item => hasValues(item.name))?.name || 'sleep_minutes'
  const unit = selected === 'sleep_minutes' ? 'мин' : 'балл.'
  return <section className="panel">
    <div className="chart-header">
      <h2>Дневник восстановления</h2>
      <label className="training-select">Показатель восстановления
        <select value={selected} onChange={event => setMetric(event.target.value)}>
          {recoveryMetrics.map(item => <option key={item.name} value={item.name}>{item.label}</option>)}
        </select>
      </label>
    </div>
    <DatedChart title={metricLabel(selected)} unit={unit} compare domain={selected === 'sleep_minutes' ? undefined : [1, 5]}
      description="Линии показывают записи дневника и индивидуальную норму. Пропуски оставлены разрывами."
      readings={days.map(day => {
        const point = day.metrics[selected]
        return {
          date: day.date, value: point?.value ?? null, baseline: point?.baseline, delta: point?.delta,
          note: !day.has_entry ? 'Дневник пропущен' : point?.baseline == null ? 'Индивидуальная норма недоступна'
            : `Норма по ${point.observations} наблюдениям`,
        }
      })}
    />
    {unsupported.length > 0 && <p className="muted">Пока не поддерживаются · {unsupported.map(metricLabel).join(', ')}.</p>}
  </section>
}
