import { useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import { Link } from 'react-router'

import { QueryFeedback, SaveFeedback } from './AthleteFeedback'
import { useAthleteResource, useSaveAthleteData } from './athleteApi'
import type { WorkoutDetail } from './athleteApi'
import type { ChartRange } from './Charts'
import { effortClock, effortReasonLabels, measure, trainingApi, trainingAthletePath } from './trainingApi'
import type { Effort } from './trainingApi'

const maxFitSeconds = 7 * 86400
type Draft = { protocol: string; start: string; duration: string }

export function ControlEffort({ workout, onRange }: { workout: WorkoutDetail; onRange: (range: ChartRange | undefined) => void }) {
  const path = trainingApi + '/workouts/' + workout.id + '/effort'
  const query = useAthleteResource<Effort | null>(path)
  const save = useSaveAthleteData<Effort>()
  const [draft, setDraft] = useState<Draft | null>(null)
  const current = query.data
  const values = draft ?? {
    protocol: current?.protocol ?? '',
    start: current ? String(current.start_seconds) : '',
    duration: current ? String(current.duration_seconds) : '',
  }
  const elapsed = workout.summary?.elapsed_seconds
  const limit = elapsed == null ? maxFitSeconds : Math.min(maxFitSeconds, Math.round(elapsed))
  const start = values.start === '' ? NaN : Number(values.start)
  const duration = values.duration === '' ? NaN : Number(values.duration)
  const end = start + duration
  const valid = Number.isInteger(start) && Number.isInteger(duration) && start >= 0 && duration >= 1 && end <= limit
  const outOfBounds = Number.isInteger(start) && Number.isInteger(duration) && end > limit

  useEffect(() => {
    onRange(valid ? { start, end } : undefined)
    return () => onRange(undefined)
  }, [onRange, valid, start, end])

  function update(key: keyof Draft, value: string) {
    setDraft({ ...values, [key]: value })
    if (save.isSuccess || save.isError) save.reset()
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const protocol = values.protocol.trim()
    if (!valid || !protocol || save.isPending) return
    save.mutate({ path, body: { protocol, start_seconds: start, duration_seconds: duration } }, {
      onSuccess: () => setDraft(null),
    })
  }

  return <section className="training-subsection" id="control-effort">
    <h3>Контрольный отрезок</h3>
    <p className="muted">Выберите время по графику мощности. Используйте один код для повторений одинакового задания в сопоставимых условиях.</p>
    <QueryFeedback loading={query.isPending} error={query.error} retry={() => void query.refetch()} />
    {current && <div className="training-effort-result">
      <p>Сохранено · <strong>{current.protocol}</strong> · {effortClock(current.start_seconds)}–
        {effortClock(current.start_seconds + current.duration_seconds)} от начала FIT</p>
      <p>Средняя мощность: {measure(current.mean_watts, 'Вт')}</p>
      {current.reason && <p className="notice">{effortReasonLabels[current.reason] ?? 'Проверьте разметку отрезка.'}</p>}
    </div>}
    {current !== undefined && <form className="form-stack" onSubmit={submit} aria-label="Разметка контрольного отрезка">
      <fieldset className="training-effort-fields" disabled={save.isPending}>
        <label>Код протокола<input name="protocol" required maxLength={64}
          value={values.protocol} onChange={event => update('protocol', event.target.value)} /></label>
        <label>Начало от старта FIT, с<input name="start" type="number" inputMode="numeric" required min={0} max={Math.max(0, limit - 1)}
          step={1} value={values.start} onChange={event => update('start', event.target.value)} /></label>
        <label>Длительность отрезка, с<input name="duration" type="number" inputMode="numeric" required min={1} max={limit}
          step={1} value={values.duration} onChange={event => update('duration', event.target.value)} /></label>
      </fieldset>
      {valid && <p>Выбранный отрезок: {effortClock(start)}–{effortClock(end)} от начала FIT.</p>}
      {outOfBounds && <p role="alert" className="error">Отрезок выходит за границы записи FIT.</p>}
      <p className="muted">При сохранении сервер проверит активное время и полноту показаний мощности.</p>
      <div className="actions">
        <button disabled={save.isPending || !valid || !values.protocol.trim()}>
          {save.isPending ? 'Проверяем отрезок…' : 'Проверить и сохранить отрезок'}
        </button>
      </div>
      <SaveFeedback error={save.error} saved={save.isSuccess} />
    </form>}
    <Link to={trainingAthletePath(workout.athlete_id) + '?tab=performance'}>К истории сопоставимых попыток</Link>
  </section>
}
