import { useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { Link, useSearchParams } from 'react-router'

import { dateLabel, diaryFields, recordedAt, useAthleteResource, useSaveAthleteData } from './athleteApi'
import type { WellnessEntry, WellnessHistory } from './athleteApi'
import { Pager, QueryFeedback, SaveFeedback } from './AthleteFeedback'
import { dayLabels } from './trainingApi'

const pageSize = 30

export function ScoreDetails({ entry, showTimes = false }: { entry: WellnessEntry; showTimes?: boolean }) {
  return (
    <>
      <dl className="measurements wellness-values">
        {diaryFields.map(field => <div key={field.name}><dt>{field.label}</dt><dd>{entry[field.name]} из 5</dd></div>)}
        {entry.sleep_minutes != null && <div><dt>Продолжительность сна</dt><dd>{entry.sleep_minutes} мин</dd></div>}
        {entry.recovery != null && <div><dt>Самооценка восстановления</dt><dd>{entry.recovery} из 5</dd></div>}
        {entry.training_day_status && <div><dt>Записи за день</dt><dd>{dayLabels[entry.training_day_status]}</dd></div>}
        <div><dt>Симптомы заболевания</dt><dd>{entry.illness_symptoms ? 'Да' : 'Нет'}</dd></div>
      </dl>
      {entry.comment && <p className="entry-comment">{entry.comment}</p>}
      {showTimes && <p className="muted entry-time">Создано {recordedAt(entry.created_at, entry.timezone)} · Изменено {recordedAt(entry.updated_at, entry.timezone)} · {entry.timezone}</p>}
    </>
  )
}

function DiaryForm({ path, date, entry, completed }: { path: string; date: string; entry: WellnessEntry | null; completed: boolean }) {
  const save = useSaveAthleteData()

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const data = new FormData(event.currentTarget)
    const values = Object.fromEntries(diaryFields.map(field => [field.name, Number(data.get(field.name))]))
    const sleep = String(data.get('sleep_minutes') ?? '')
    const recovery = String(data.get('recovery') ?? '')
    const confirmation = data.get('training_day_status')
    save.mutate({
      path,
      body: {
        ...values, illness_symptoms: data.get('illness_symptoms') === 'yes', comment: String(data.get('comment')).trim() || null,
        ...(sleep || entry?.sleep_minutes != null ? { sleep_minutes: sleep ? Number(sleep) : null } : {}),
        ...(recovery || entry?.recovery != null ? { recovery: recovery ? Number(recovery) : null } : {}),
        ...(completed && (confirmation || entry?.training_day_status != null)
          ? { training_day_status: confirmation || null } : {}),
      },
    })
  }

  return (
    <form className="form-stack" onSubmit={submit} aria-label="Утренняя анкета">
      <h2>{dateLabel(date)}</h2>
      <div className="score-grid">
        {diaryFields.map(field => (
          <fieldset className="score-field" key={field.name} aria-describedby={field.name + "-scale"}>
            <legend>{field.label}</legend>
            <p className="muted" id={field.name + "-scale"}>{field.direction === 'positive' ? '1 — очень плохо, 5 — отлично' : '1 — минимальная, 5 — очень высокая'}</p>
            <div className="choices">
              {[1, 2, 3, 4, 5].map(value => (
                <label className="choice" key={value}>
                  <input type="radio" name={field.name} value={value} required defaultChecked={entry?.[field.name] === value} />
                  <span>{value}</span>
                </label>
              ))}
            </div>
          </fieldset>
        ))}
        <fieldset className="score-field">
          <legend>Симптомы заболевания</legend>
          <div className="choices binary">
            <label className="choice"><input type="radio" name="illness_symptoms" value="no" required defaultChecked={entry?.illness_symptoms === false} /><span>Нет</span></label>
            <label className="choice"><input type="radio" name="illness_symptoms" value="yes" required defaultChecked={entry?.illness_symptoms === true} /><span>Да</span></label>
          </div>
        </fieldset>
      </div>
      <div className="form-grid training-diary-fields">
        <label>Продолжительность сна, мин (необязательно)
          <input name="sleep_minutes" type="number" inputMode="numeric" min={0} max={1440} step={1} defaultValue={entry?.sleep_minutes ?? ''} />
        </label>
        <label>Самооценка восстановления (необязательно)
          <select name="recovery" defaultValue={entry?.recovery ?? ''}>
            <option value="">Без оценки</option>
            <option value={1}>1 · очень низкое</option><option value={2}>2 · низкое</option>
            <option value={3}>3 · среднее</option><option value={4}>4 · хорошее</option><option value={5}>5 · отличное</option>
          </select>
        </label>
        {completed && <label>Подтверждение записей за день (необязательно)
          <select name="training_day_status" defaultValue={entry?.training_day_status ?? ''}>
            <option value="">День не подтверждён</option>
            <option value="rest">День отдыха</option>
            <option value="all_workouts_recorded">Все тренировки записаны</option>
          </select>
        </label>}
      </div>
      <label>Комментарий (необязательно)<textarea name="comment" rows={3} maxLength={2000} defaultValue={entry?.comment ?? ''} /></label>
      {entry && <p className="muted entry-time">Создано {recordedAt(entry.created_at, entry.timezone)} · Изменено {recordedAt(entry.updated_at, entry.timezone)}</p>}
      <div className="actions"><button disabled={save.isPending}>{save.isPending ? 'Сохраняем…' : 'Сохранить дневник'}</button></div>
      <SaveFeedback error={save.error} saved={save.isSuccess} />
    </form>
  )
}

function SelectedDay({ base, date, localDate }: { base: string; date: string; localDate: string }) {
  const path = base + '/' + date
  const day = useAthleteResource<WellnessEntry | null>(path)
  return (
    <>
      <QueryFeedback loading={day.isPending} error={day.error} retry={() => void day.refetch()} />
      {day.data !== undefined && <DiaryForm key={date} path={path} date={date} entry={day.data} completed={date < localDate} />}
    </>
  )
}

export function DiaryPage({ athleteId, readOnly = false, athleteName }: {
  athleteId: number; readOnly?: boolean; athleteName?: string
}) {
  const [offset, setOffset] = useState(0)
  const [params, setParams] = useSearchParams()
  const dateInput = useRef<HTMLInputElement>(null)
  const base = '/athletes/' + athleteId + '/wellness'
  const diary = useAthleteResource<WellnessHistory>(base + '?offset=' + offset, 60_000)
  const history = diary.data
  const targetDate = params.get('date') ?? ''
  const targetTime = Date.parse(targetDate)
  const validDate = /^\d{4}-\d{2}-\d{2}$/.test(targetDate)
    && Number.isFinite(targetTime) && new Date(targetTime).toISOString().slice(0, 10) === targetDate
  const selected = validDate ? targetDate : ''
  const date = selected || history?.local_date || ''
  return (
    <>
      {readOnly && <Link className="back-link" to={'/athletes/' + athleteId}>К обзору спортсмена</Link>}
      <header className="page-heading"><div><h1>Утренний дневник</h1>{athleteName && <p>{athleteName}</p>}</div></header>
      <QueryFeedback loading={diary.isPending} error={diary.error} retry={() => void diary.refetch()} />
      {history && (
        <>
          <section className="panel">
            <p className="muted">Местная дата · {dateLabel(history.local_date)} · {history.timezone}</p>
            <p className={'status ' + (history.today_entry ? 'active' : 'pending')}>
              {history.today_entry ? 'Сегодня заполнен' : 'Сегодня не заполнен'}
            </p>
            {readOnly ? (
              history.today_entry && <ScoreDetails entry={history.today_entry} showTimes />
            ) : (
              <>
                <label className="date-picker">Дата анкеты<input ref={dateInput} type="date" value={date} max={selected > history.local_date ? selected : history.local_date}
                  onChange={event => setParams(event.target.value ? { date: event.target.value } : {}, { replace: true })} /></label>
                <SelectedDay base={base} date={date} localDate={history.local_date} />
              </>
            )}
          </section>
          <section className="panel">
            <h2>История дневника</h2>
            {history.entries.length === 0 && <p className="muted">Записей пока нет.</p>}
            {history.entries.map(entry => (
              <details className="entry-item" key={entry.entry_date}>
                <summary>{dateLabel(entry.entry_date)}{entry.illness_symptoms && <span className="status pending">Есть симптомы</span>}</summary>
                <ScoreDetails entry={entry} showTimes />
                {!readOnly && <button className="secondary" type="button" onClick={() => {
                  setParams({ date: entry.entry_date }, { replace: true })
                  dateInput.current?.focus()
                }}>Редактировать анкету</button>}
              </details>
            ))}
            <Pager offset={offset} size={pageSize} hasMore={history.has_more} onChange={setOffset} />
          </section>
        </>
      )}
    </>
  )
}
