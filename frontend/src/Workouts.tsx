import { useState } from 'react'
import type { FormEvent } from 'react'
import { Link } from 'react-router'

import { durationLabel, numberLabel, recordedAt, useAthleteResource, useSaveAthleteData } from './athleteApi'
import type { Workout, WorkoutList } from './athleteApi'
import { Pager, QueryFeedback, SaveFeedback } from './AthleteFeedback'

const pageSize = 20

function RpeForm({ workout }: { workout: Workout }) {
  const save = useSaveAthleteData()

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const data = new FormData(event.currentTarget)
    save.mutate({
      path: '/workouts/' + workout.id + '/rpe',
      body: { score: Number(data.get('score')), comment: String(data.get('comment')).trim() || null },
    })
  }

  return (
    <form className="form-stack rpe-form" onSubmit={submit}>
      <label>Сложность тренировки (RPE)
        <select name="score" defaultValue={workout.rpe?.score ?? ''} required>
          <option value="" disabled>Выберите оценку</option>
          {Array.from({ length: 11 }, (_, score) => <option value={score} key={score}>{score}</option>)}
        </select>
      </label>
      <p className="muted">0 — без усилий, 10 — максимальное усилие.</p>
      <label>Комментарий (необязательно)<textarea name="comment" maxLength={2000} rows={3} defaultValue={workout.rpe?.comment ?? ''} /></label>
      <div className="actions"><button disabled={save.isPending}>Сохранить RPE</button></div>
      <SaveFeedback error={save.error} saved={save.isSuccess} />
    </form>
  )
}

function WorkoutCard({ workout, timezone, readOnly }: { workout: Workout; timezone: string; readOnly: boolean }) {
  const [editing, setEditing] = useState(false)
  const rpe = workout.rpe

  return (
    <article className="panel workout-card" aria-label={'Тренировка от ' + recordedAt(workout.started_at, timezone)}>
      <h2>{recordedAt(workout.started_at, timezone)}</h2>
      <dl className="measurements">
        <div><dt>Время по таймеру</dt><dd>{durationLabel(workout.timer_seconds)}</dd></div>
        <div><dt>RPE</dt><dd>{rpe ? rpe.score + ' из 10' : <span className="status pending">Не заполнено</span>}</dd></div>
        <div><dt>Нагрузка по RPE</dt><dd>{rpe?.session_load != null ? numberLabel(rpe.session_load) + ' усл. ед.' : 'Нет данных'}</dd></div>
      </dl>
      {rpe?.comment && <p className="entry-comment">{rpe.comment}</p>}
      {rpe && <p className="muted entry-time">Заполнено {recordedAt(rpe.created_at, timezone)} · Изменено {recordedAt(rpe.updated_at, timezone)}</p>}
      {!readOnly && (
        <>
          <div className="actions">
            <button type="button" className="secondary" aria-expanded={editing} onClick={() => setEditing(value => !value)}>
              {editing ? 'Скрыть форму' : rpe ? 'Изменить RPE' : 'Указать RPE'}
            </button>
          </div>
          {editing && <RpeForm workout={workout} />}
        </>
      )}
    </article>
  )
}

export function WorkoutPage({ athleteId, readOnly = false, athleteName }: {
  athleteId: number; readOnly?: boolean; athleteName?: string
}) {
  const [offset, setOffset] = useState(0)
  const query = useAthleteResource<WorkoutList>('/athletes/' + athleteId + '/workouts?offset=' + offset)
  const list = query.data
  return (
    <>
      {readOnly && <Link className="back-link" to={'/athletes/' + athleteId}>К профилю спортсмена</Link>}
      <header className="page-heading"><div><h1>Тренировки</h1>{athleteName && <p>{athleteName}</p>}</div></header>
      <QueryFeedback loading={query.isPending} error={query.error} retry={() => void query.refetch()} />
      {list && (
        <>
          {list.missing_rpe_count > 0 && <p className="notice">Тренировок без RPE — {list.missing_rpe_count}</p>}
          {list.workouts.length === 0 && <p className="panel muted">Тренировок пока нет. Здесь появятся записи после импорта FIT.</p>}
          {list.workouts.map(workout => <WorkoutCard key={workout.id} workout={workout} timezone={list.timezone} readOnly={readOnly} />)}
          <Pager offset={offset} size={pageSize} hasMore={list.has_more} onChange={setOffset} />
        </>
      )}
    </>
  )
}
