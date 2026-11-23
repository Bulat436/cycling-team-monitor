import { useState } from 'react'
import type { FormEvent } from 'react'
import { Link } from 'react-router'

import { durationLabel, numberLabel, recordedAt, useAthleteResource, useSaveAthleteData } from './athleteApi'
import type { FitImport, Workout, WorkoutList } from './athleteApi'
import { Pager, QueryFeedback, SaveFeedback } from './AthleteFeedback'

const pageSize = 20

export function RpeForm({ workout }: { workout: Workout }) {
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

export function displayMeasure(value: number | null | undefined, unit: string, scale = 1) {
  return value == null ? 'Нет данных' : numberLabel(value * scale) + (unit ? ' ' + unit : '')
}

function UploadFit({ onUpload }: { onUpload: () => void }) {
  const upload = useSaveAthleteData<FitImport>()

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const form = event.currentTarget
    const file = new FormData(form).get('file')
    if (!(file instanceof File) || !file.name) return
    upload.mutate({ path: '/workouts/import', method: 'POST', body: file }, {
      onSuccess: () => {
        form.reset()
        onUpload()
      },
    })
  }

  const result = upload.data
  return (
    <section className="panel" id="workout-fit-import">
      <h2>Импорт тренировки</h2>
      <p><Link to="/diary/sleep#sleep-report-title">Загрузить тренировки из JSON или CSV</Link></p>
      <form className="fit-upload" onSubmit={submit}>
        <label>Файл тренировки (.fit)<input required type="file" accept=".fit" name="file" /></label>
        <button disabled={upload.isPending}>{upload.isPending ? 'Обрабатываем FIT…' : 'Загрузить FIT'}</button>
      </form>
      <SaveFeedback error={upload.error} saved={false} />
      {result && <div className="success">
        <p role="status">{result.status === 'duplicate' ? 'Эта тренировка уже в истории.' : 'Тренировка сохранена.'}</p>
        {result.potential_duplicates.length > 0 && <p>В истории есть похожая тренировка с другим исходным файлом.</p>}
        <Link to={'/workouts/' + result.workout_id}>Открыть импортированную тренировку</Link>
      </div>}
    </section>
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
        {workout.summary && <>
          <div><dt>Дистанция</dt><dd>{displayMeasure(workout.summary.distance_m, 'км', .001)}</dd></div>
          <div><dt>Средняя мощность</dt><dd>{displayMeasure(workout.summary.avg_power, 'Вт')}</dd></div>
          <div><dt>NP</dt><dd>{displayMeasure(workout.np, 'Вт')}</dd></div>
          <div><dt>TSS</dt><dd>{displayMeasure(workout.tss, '')}</dd></div>
          <div><dt>Средний пульс</dt><dd>{displayMeasure(workout.summary.avg_heart_rate, 'уд/мин')}</dd></div>
          <div><dt>Средний каденс</dt><dd>{displayMeasure(workout.summary.avg_cadence, 'об/мин')}</dd></div>
        </>}
        <div><dt>RPE</dt><dd>{rpe ? rpe.score + ' из 10' : <span className="status pending">Не заполнено</span>}</dd></div>
        <div><dt>Нагрузка по RPE</dt><dd>{rpe?.session_load != null ? numberLabel(rpe.session_load) + ' усл. ед.' : 'Нет данных'}</dd></div>
      </dl>
      {rpe?.comment && <p className="entry-comment">{rpe.comment}</p>}
      {rpe && <p className="muted entry-time">Заполнено {recordedAt(rpe.created_at, timezone)} · Изменено {recordedAt(rpe.updated_at, timezone)}</p>}
      <div className="actions"><Link to={'/workouts/' + workout.id}>Подробнее о тренировке</Link></div>
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
      {readOnly && <Link className="back-link" to={'/athletes/' + athleteId}>К обзору спортсмена</Link>}
      <header className="page-heading"><div><h1>Тренировки</h1>{athleteName && <p>{athleteName}</p>}</div></header>
      {!readOnly && <UploadFit onUpload={() => setOffset(0)} />}
      <QueryFeedback loading={query.isPending} error={query.error} retry={() => void query.refetch()} />
      {list && (
        <>
          {list.missing_rpe_count > 0 && <p className="notice">Тренировок без RPE — {list.missing_rpe_count}</p>}
          {list.workouts.length === 0 && <p className="panel muted">Тренировок пока нет.</p>}
          {list.workouts.map(workout => <WorkoutCard key={workout.id} workout={workout} timezone={list.timezone} readOnly={readOnly} />)}
          <Pager offset={offset} size={pageSize} hasMore={list.has_more} onChange={setOffset} />
        </>
      )}
    </>
  )
}
