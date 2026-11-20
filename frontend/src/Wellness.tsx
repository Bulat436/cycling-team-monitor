import { useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { Link } from 'react-router'

import { dateLabel, diaryFields, recordedAt, useAthleteResource, useSaveAthleteData } from './athleteApi'
import type { WellnessEntry, WellnessHistory } from './athleteApi'
import { Pager, QueryFeedback, SaveFeedback } from './AthleteFeedback'

const pageSize = 30

export function ScoreDetails({ entry, showTimes = false }: { entry: WellnessEntry; showTimes?: boolean }) {
  return (
    <>
      <dl className="measurements wellness-values">
        {diaryFields.map(field => <div key={field.name}><dt>{field.label}</dt><dd>{entry[field.name]} из 5</dd></div>)}
        <div><dt>Симптомы заболевания</dt><dd>{entry.illness_symptoms ? 'Да' : 'Нет'}</dd></div>
      </dl>
      {entry.comment && <p className="entry-comment">{entry.comment}</p>}
      {showTimes && <p className="muted entry-time">Создано {recordedAt(entry.created_at, entry.timezone)} · Изменено {recordedAt(entry.updated_at, entry.timezone)} · {entry.timezone}</p>}
    </>
  )
}

function DiaryForm({ path, date, entry }: { path: string; date: string; entry: WellnessEntry | null }) {
  const save = useSaveAthleteData()

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const data = new FormData(event.currentTarget)
    const values = Object.fromEntries(diaryFields.map(field => [field.name, Number(data.get(field.name))]))
    save.mutate({
      path,
      body: { ...values, illness_symptoms: data.get('illness_symptoms') === 'yes', comment: String(data.get('comment')).trim() || null },
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
      <label>Комментарий (необязательно)<textarea name="comment" rows={3} maxLength={2000} defaultValue={entry?.comment ?? ''} /></label>
      {entry && <p className="muted entry-time">Создано {recordedAt(entry.created_at, entry.timezone)} · Изменено {recordedAt(entry.updated_at, entry.timezone)}</p>}
      <div className="actions"><button disabled={save.isPending}>{save.isPending ? 'Сохраняем…' : 'Сохранить дневник'}</button></div>
      <SaveFeedback error={save.error} saved={save.isSuccess} />
    </form>
  )
}

function SelectedDay({ base, date }: { base: string; date: string }) {
  const path = base + '/' + date
  const day = useAthleteResource<WellnessEntry | null>(path)
  return (
    <>
      <QueryFeedback loading={day.isPending} error={day.error} retry={() => void day.refetch()} />
      {day.data !== undefined && <DiaryForm key={date} path={path} date={date} entry={day.data} />}
    </>
  )
}

export function DiaryPage({ athleteId, readOnly = false, athleteName }: {
  athleteId: number; readOnly?: boolean; athleteName?: string
}) {
  const [offset, setOffset] = useState(0)
  const [selected, setSelected] = useState('')
  const dateInput = useRef<HTMLInputElement>(null)
  const base = '/athletes/' + athleteId + '/wellness'
  const diary = useAthleteResource<WellnessHistory>(base + '?offset=' + offset, 60_000)
  const history = diary.data
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
                <label className="date-picker">Дата анкеты<input ref={dateInput} type="date" value={date} max={selected > history.local_date ? selected : history.local_date} onChange={event => setSelected(event.target.value)} /></label>
                <SelectedDay base={base} date={date} />
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
                  setSelected(entry.entry_date)
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
