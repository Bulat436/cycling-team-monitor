import { useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import { useQueryClient } from '@tanstack/react-query'

import { ApiError } from './api'
import { QueryFeedback, SaveFeedback } from './AthleteFeedback'
import { dateLabel, recordedAt, useSaveAthleteData } from './athleteApi'
import { SleepSummary } from './SleepSummary'
import { hasManualSleep, sleepDurationLabel, sleepSources } from './sleepApi'
import type { SleepDay, SleepSource } from './sleepApi'
import { offsetLabel, sleepPreview } from './sleepTime'
import type { RecoveryValue } from './trainingApi'

type Operation = 'manual' | 'selection' | 'move'
type Boundary = 'start' | 'end'
type OffsetKey = `${Boundary}_offset_minutes`

function fieldsOf(day: SleepDay) {
  return {
    start_time: day.start_time ?? '', end_time: day.end_time ?? '',
    start_offset_minutes: day.start_offset_minutes, end_offset_minutes: day.end_offset_minutes,
    duration_minutes: String(day.entered_duration_minutes ?? ''),
    sleep_quality: String(day.sleep_quality ?? ''), recovery: String(day.recovery ?? ''),
  }
}

type Fields = ReturnType<typeof fieldsOf>

function changedFields(fields: Fields, base: SleepDay) {
  const saved = fieldsOf(base)
  const changes: Record<string, string | number | null> = {}
  for (const name of Object.keys(fields) as (keyof Fields)[]) {
    const value = fields[name]
    if (value === saved[name]) continue
    changes[name] = value === '' || value == null ? null : name.endsWith('_time') ? value : Number(value)
  }
  return changes
}

function overnight(fields: Fields) {
  return Boolean(fields.start_time && fields.end_time && fields.start_time > fields.end_time)
}

function OffsetPicker({ label, values, value, choose, required = true }: {
  label: string; values: number[]; value: number | null; choose: (value: number | null) => void; required?: boolean
}) {
  return <label>{label}<select required={required} value={value ?? ''}
    onChange={event => choose(event.target.value === '' ? null : Number(event.target.value))}>
    <option value="">Выберите смещение</option>
    {values.map(offset => <option key={offset} value={offset}>{offsetLabel(offset)}</option>)}
  </select></label>
}

const ratingOptions = ['1 · очень низкое', '2 · низкое', '3 · среднее', '4 · хорошее', '5 · отличное']

function Rating({ label, value, choose, required = false }: {
  label: string; value: string; choose: (value: string) => void; required?: boolean
}) {
  return <label>{label}<select value={value} required={required} onChange={event => choose(event.target.value)}>
    <option value="">Без оценки</option>
    {ratingOptions.map((item, index) => <option key={item} value={index + 1}>{item}</option>)}
  </select></label>
}

export function SleepEditor({ day, base, today, comparison, windowDays, onDirty, onMoved, refresh, refreshError }: {
  day: SleepDay; base: string; today: string; comparison?: RecoveryValue; windowDays?: number
  onDirty: (value: boolean) => void; onMoved: (date: string) => void; refresh: () => void; refreshError: Error | null
}) {
  const client = useQueryClient()
  const mutation = useSaveAthleteData<SleepDay>()
  const [draft, setDraft] = useState<{ base: SleepDay; fields: Fields } | null>(null)
  const [operation, setOperation] = useState<Operation>()
  const [moveDate, setMoveDate] = useState('')
  const [moveOffsets, setMoveOffsets] = useState<Partial<Record<OffsetKey, number | null>>>({})
  const fields = draft?.fields ?? fieldsOf(day)
  const outdated = draft != null && draft.base.version !== day.version
  const error = mutation.error instanceof ApiError ? mutation.error : undefined
  const dirty = draft != null || moveDate !== ''
  const preview = sleepPreview(day.date, day.timezone, fields.start_time, fields.end_time, fields.start_offset_minutes, fields.end_offset_minutes)
  const windowChanged = draft != null && ['start_time', 'end_time', 'start_offset_minutes', 'end_offset_minutes']
    .some(field => field in changedFields(fields, draft.base))
  const calculated = windowChanged ? preview.minutes : day.calculated_duration_minutes
  const candidates = (Object.keys(sleepSources) as SleepSource[]).filter(source => day.candidates[source] != null)

  useEffect(() => { onDirty(dirty) }, [dirty, onDirty])
  useEffect(() => () => onDirty(false), [onDirty])

  function edit(next: Fields) {
    const original = draft?.base ?? day
    setDraft(Object.keys(changedFields(next, original)).length ? { base: original, fields: next } : null)
  }

  function changeTime(field: 'start_time' | 'end_time', value: string) {
    const next = { ...fields, [field]: value }
    if (field === 'start_time' || overnight(next) !== overnight(fields)) next.start_offset_minutes = null
    if (field === 'end_time') next.end_offset_minutes = null
    if (operation === 'manual' && mutation.error) mutation.reset()
    edit(next)
  }

  function send(action: Operation, body: unknown) {
    setOperation(action)
    mutation.mutate({ path: `${base}/${day.date}/${action}`, method: action === 'manual' ? 'PATCH' : action === 'move' ? 'POST' : 'PUT', body }, {
      onSuccess(result) {
        client.setQueryData(['athlete-data', `${base}/${result.date}`], result)
        if (action === 'manual') setDraft(null)
        if (action === 'move') onMoved(result.date)
      },
      onError(failure) {
        if (failure instanceof ApiError && failure.code === 'sleep_version_conflict') refresh()
      },
    })
  }

  function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (draft && !outdated) send('manual', { version: draft.base.version, ...changedFields(draft.fields, draft.base) })
  }

  function offsetsFor(boundary: Boundary) {
    if (operation === 'manual' && error?.field === `${boundary}_time` && error.offsets.length) return error.offsets
    return boundary === 'start' ? preview.startOffsets : preview.endOffsets
  }

  const moveBoundary = operation === 'move' && error?.offsets.length
    ? error.field === 'start_time' ? 'start' : error.field === 'end_time' ? 'end' : undefined : undefined
  const moveOffsetKey = moveBoundary ? `${moveBoundary}_offset_minutes` as const : undefined

  return <>
    <section className="panel sleep-panel" aria-labelledby="sleep-day-title">
      <h2 id="sleep-day-title">{dateLabel(day.date)}</h2>
      <p className="muted">{day.timezone}{day.updated_at != null ? ` · обновлено ${recordedAt(day.updated_at, day.timezone)}` : ''}</p>
      <SleepSummary day={day} comparison={comparison} windowDays={windowDays} />
      <QueryFeedback loading={false} error={refreshError} retry={refresh} />

      <form className="form-stack" aria-label="Запись сна" onSubmit={save}>
        <fieldset className="sleep-body" disabled={mutation.isPending}>
          <div className="form-grid sleep-time-fields">
            <label>Время засыпания<input type="time" value={fields.start_time}
              onChange={event => changeTime('start_time', event.target.value)} /></label>
            <label>Время пробуждения<input type="time" value={fields.end_time}
              onChange={event => changeTime('end_time', event.target.value)} /></label>
            {(['start', 'end'] as const).map(boundary => {
              const offsets = offsetsFor(boundary)
              const key: OffsetKey = `${boundary}_offset_minutes`
              return (offsets.length > 1 || (operation === 'manual' && error?.field === `${boundary}_time` && offsets.length > 0)) &&
                <OffsetPicker key={key} label={boundary === 'start' ? 'Смещение времени засыпания' : 'Смещение времени пробуждения'}
                  values={offsets} value={fields[key]} choose={value => edit({ ...fields, [key]: value })} />
            })}
          </div>
          <p className="muted sleep-calculation" aria-live="polite">
            {calculated != null ? `По времени: ${sleepDurationLabel(calculated)}` : fields.start_time && fields.end_time
              ? 'Уточните время или смещение. Сервер проверит интервал при сохранении.' : 'Укажите оба времени для расчёта.'}
          </p>
          <div className="form-grid sleep-detail-fields">
            <label>Длительность вручную, мин<input type="number" inputMode="numeric" min={0} max={1440} step={1}
              value={fields.duration_minutes} placeholder={calculated != null ? String(calculated) : undefined}
              onChange={event => edit({ ...fields, duration_minutes: event.target.value })} /></label>
            <Rating label="Субъективное качество сна" value={fields.sleep_quality} required={day.has_diary}
              choose={value => edit({ ...fields, sleep_quality: value })} />
            <Rating label="Самооценка восстановления" value={fields.recovery} choose={value => edit({ ...fields, recovery: value })} />
          </div>
          <div className="actions">
            <button disabled={!draft || outdated}>{mutation.isPending && operation === 'manual' ? 'Сохраняем…' : 'Сохранить сон'}</button>
            {draft && <button type="button" className="secondary" onClick={() => { setDraft(null); mutation.reset() }}>
              {outdated ? 'Загрузить актуальную запись' : 'Отменить изменения'}
            </button>}
          </div>
        </fieldset>
        {outdated && <p role="alert" className="error">Запись обновилась. Перед продолжением загрузите актуальную версию.</p>}
        {operation === 'manual' && <SaveFeedback error={mutation.error} saved={mutation.isSuccess && !draft} />}
      </form>

      {(day.conflict || day.selected_source != null || (day.source == null && candidates.length > 0)) && <fieldset
        className="sleep-choice panel" disabled={mutation.isPending || dirty}>
        <legend>Значение для аналитики</legend>
        {day.conflict && <p>Длительности различаются. Выберите, какую использовать. Оба значения сохранятся.</p>}
        <div className="sleep-choices">
          {candidates.map(source => <button key={source} type="button" className="secondary" aria-pressed={day.source === source}
            onClick={() => send('selection', { version: day.version, source })}>
            <span>{sleepSources[source]}</span><strong>{sleepDurationLabel(day.candidates[source] ?? null)}</strong>
          </button>)}
        </div>
        {day.selected_source && <button type="button" className="secondary" onClick={() => send('selection', { version: day.version, source: null })}>
          Вернуть выбор по умолчанию
        </button>}
        {operation === 'selection' && <SaveFeedback error={mutation.error} saved={mutation.isSuccess} />}
      </fieldset>}

      {hasManualSleep(day) && <details className="entry-item">
        <summary>Перенести запись сна</summary>
        <form className="form-stack" aria-label="Перенос сна" onSubmit={event => {
          event.preventDefault()
          if (!draft && moveDate && moveDate !== day.date) send('move', { version: day.version, date: moveDate, ...moveOffsets })
        }}>
          <fieldset className="sleep-body" disabled={mutation.isPending || draft != null}>
            <label className="date-picker">Новая дата пробуждения<input type="date" required max={today} value={moveDate}
              onChange={event => { setMoveDate(event.target.value); setMoveOffsets({}); if (operation === 'move') mutation.reset() }} /></label>
            {moveBoundary && moveOffsetKey && error && <OffsetPicker
              label={moveBoundary === 'start' ? 'Смещение засыпания на новой дате' : 'Смещение пробуждения на новой дате'}
              values={error.offsets} value={moveOffsets[moveOffsetKey] ?? null}
              choose={value => setMoveOffsets(previous => ({ ...previous, [moveOffsetKey]: value }))} />}
            <div className="actions">
              <button type="submit" disabled={!moveDate || moveDate === day.date}>
                {mutation.isPending && operation === 'move' ? 'Переносим…' : 'Перенести сон'}
              </button>
              {moveDate && <button type="button" className="secondary" onClick={() => {
                setMoveDate(''); setMoveOffsets({}); if (operation === 'move') mutation.reset()
              }}>Отменить перенос</button>}
            </div>
          </fieldset>
          {draft && <p className="muted">Сохраните или отмените изменения перед переносом.</p>}
          {operation === 'move' && <SaveFeedback error={mutation.error} saved={false} />}
        </form>
      </details>}
    </section>
  </>
}
