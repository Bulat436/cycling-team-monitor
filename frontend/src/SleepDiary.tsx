import { useCallback, useEffect, useRef, useState } from 'react'
import { Link, useBlocker, useOutletContext, useSearchParams } from 'react-router'

import { Pager, QueryFeedback } from './AthleteFeedback'
import { dateLabel, useAthleteResource } from './athleteApi'
import { SleepEditor } from './SleepEditor'
import { SleepReports } from './SleepReports'
import { isCalendarDate, sleepApi, sleepRecordLabel, sleepSources } from './sleepApi'
import type { SleepDay, SleepHistory } from './sleepApi'
import { DatedChart } from './TrainingGraphs'
import { trainingApi } from './trainingApi'
import type { OwnRecovery } from './trainingApi'
import { confirmDiscard } from './unsaved'
import type { FormOutlet } from './unsaved'

const pageSize = 30

function SleepTrend({ data, recent }: { data: OwnRecovery; recent: SleepDay[] }) {
  const byDay = new Map(recent.map(day => [day.date, day]))
  return <DatedChart title="Продолжительность сна" unit="мин" compare
    description={`Последние 28 дней. ${data.recovery_window_days != null ? `Индивидуальная норма рассчитана по предыдущим ${data.recovery_window_days} дням. ` : ''}Пропуски показаны разрывами.`}
    readings={data.recovery.map(day => {
      const point = day.metrics.sleep_minutes
      const stored = byDay.get(day.date)
      return {
        date: day.date, value: point?.value ?? null, baseline: point?.baseline, delta: point?.delta,
        note: point?.value === 0 ? 'Указано отсутствие сна' : point?.value == null
          ? stored ? sleepRecordLabel(stored) : 'Записи сна нет' : undefined,
      }
    })} />
}

export default function SleepDiary({ athleteId }: { athleteId: number }) {
  const [params, setParams] = useSearchParams()
  const [offset, setOffset] = useState(0)
  const [movedDate, setMovedDate] = useState('')
  const [openedDate, setOpenedDate] = useState('')
  const dateField = useRef<HTMLInputElement>(null)
  const base = sleepApi(athleteId)
  const recent = useAthleteResource<SleepHistory>(base + '?offset=0', 60_000)
  const history = useAthleteResource<SleepHistory>(base + '?offset=' + offset, undefined, true)
  const measures = useAthleteResource<OwnRecovery>(trainingApi + '/me?period=28')
  const today = recent.data?.local_date ?? ''
  const requested = params.get('date') ?? ''
  const date = isCalendarDate(requested) ? requested : openedDate || today
  const dirty = useRef(false)
  const setUnsaved = useOutletContext<FormOutlet | undefined>()?.setUnsaved
  const markDirty = useCallback((value: boolean) => {
    dirty.current = value
    setUnsaved?.(value)
  }, [setUnsaved])
  const blocker = useBlocker(({ currentLocation, nextLocation }) => dirty.current && (
    currentLocation.pathname !== nextLocation.pathname || currentLocation.search !== nextLocation.search
  ))

  useEffect(() => {
    if (today) setOpenedDate(value => value || today)
  }, [today])

  useEffect(() => {
    if (blocker.state !== 'blocked') return
    if (confirmDiscard()) {
      markDirty(false)
      blocker.proceed()
    } else {
      blocker.reset()
    }
  }, [blocker, markDirty])

  useEffect(() => {
    const beforeUnload = (event: BeforeUnloadEvent) => {
      if (dirty.current) {
        event.preventDefault()
        event.returnValue = true
      }
    }
    window.addEventListener('beforeunload', beforeUnload)
    return () => window.removeEventListener('beforeunload', beforeUnload)
  }, [])

  function selectDay(next: string) {
    if (isCalendarDate(next) && next !== date) setParams({ date: next }, { replace: true })
  }

  function moved(next: string) {
    markDirty(false)
    setMovedDate(next)
    selectDay(next)
    dateField.current?.focus()
  }

  return <div className="analytics-stack">
    <header className="page-heading sleep-heading"><div><h1>Сон и восстановление</h1></div>
      <div className="actions">
        <Link className="button secondary" to={'/diary' + (date ? `?date=${date}` : '')}>Утренняя анкета</Link>
        <a className="button secondary" href="#sleep-report-title">Импорт отчёта</a>
      </div>
    </header>
    {!today && <QueryFeedback loading={recent.isPending} error={recent.error} retry={() => void recent.refetch()} />}
    {today && <>
      <section className="panel sleep-date-panel">
        <label className="date-picker">Дата пробуждения<input ref={dateField} type="date" value={date}
          max={date > today ? date : today} onChange={event => selectDay(event.target.value)} /></label>
        {movedDate === date && <p className="success" role="status">Сон перенесён на {dateLabel(date)}.</p>}
      </section>
      <SleepDaySection key={date} base={base} date={date} today={today} measures={measures.data} onDirty={markDirty} onMoved={moved} />
    </>}
    <section className="panel" aria-label="График сна">
      <h2>Динамика сна</h2>
      <QueryFeedback loading={measures.isPending} error={measures.error} retry={() => void measures.refetch()} />
      {measures.data && <SleepTrend data={measures.data} recent={recent.data?.entries ?? []} />}
    </section>
    <section className="panel" aria-label="История сна">
      <h2>История сна</h2>
      <QueryFeedback loading={history.isPending} error={history.error} retry={() => void history.refetch()} />
      {history.data && <>
        {!history.data.entries.length && <p className="muted">Записей пока нет.</p>}
        <ul className="sleep-history">
          {history.data.entries.map(day => <li key={day.date}>
            <div><time dateTime={day.date}>{dateLabel(day.date)}</time><strong>{sleepRecordLabel(day)}</strong></div>
            <div className="muted">{day.source && <span>{sleepSources[day.source]}</span>}
              <span>Анкета {day.has_diary ? 'заполнена' : 'не заполнена'}</span>
            </div>
            <button type="button" className="secondary" onClick={() => { selectDay(day.date); dateField.current?.focus() }}
              aria-label={`Открыть сон за ${dateLabel(day.date)}`}>Открыть</button>
          </li>)}
        </ul>
        {!history.isPlaceholderData && <Pager offset={offset} size={pageSize} hasMore={history.data.has_more} onChange={setOffset} />}
      </>}
    </section>
    <SleepReports athleteId={athleteId} />
  </div>
}

function SleepDaySection({ base, date, today, measures, onDirty, onMoved }: {
  base: string; date: string; today: string; measures?: OwnRecovery
  onDirty: (value: boolean) => void; onMoved: (date: string) => void
}) {
  const query = useAthleteResource<SleepDay>(`${base}/${date}`)
  const refresh = () => { void query.refetch() }
  if (!query.data) return <QueryFeedback loading={query.isPending} error={query.error} retry={refresh} />
  return <SleepEditor day={query.data} base={base} today={today} refresh={refresh} refreshError={query.error}
    comparison={measures?.recovery.find(day => day.date === date)?.metrics.sleep_minutes} windowDays={measures?.recovery_window_days}
    onDirty={onDirty} onMoved={onMoved} />
}
