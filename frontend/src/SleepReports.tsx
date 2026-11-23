import { useEffect, useRef, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router'
import csvTemplate from './activity-report.example.csv?raw'

import { Pager, QueryFeedback } from './AthleteFeedback'
import { dateLabel, useAthleteResource } from './athleteApi'
import type { FitImport } from './athleteApi'
import { ApiError, explain, request } from './api'
import { forgetSession, useSession } from './session'
import { sleepDurationLabel } from './sleepApi'

export interface SleepReportNight {
  sleep_date: string
  start_at: string | null
  end_at: string | null
  duration_minutes: number | null
  light_minutes: number | null
  deep_minutes: number | null
  rem_minutes: number | null
  awake_minutes: number | null
  sleep_score: number | null
  avg_heart_rate_bpm: number | null
  overnight_hrv_ms?: number | null
}

interface ReportHistory {
  entries: SleepReportNight[]
  has_more: boolean
}

interface ReportResult {
  saved: number
  unchanged: number
  workouts_saved?: number
  workouts_unchanged?: number
}

interface ReportWorkout {
  started_at: string
  timer_seconds: number
  distance_m?: number | null
}

interface ReportPreview { sleep: SleepReportNight[]; workouts?: ReportWorkout[] }

const pageSize = 30
const maxBytes = 512 * 1024

const phases = [
  ['light_minutes', 'Лёгкий сон'], ['deep_minutes', 'Глубокий сон'],
  ['rem_minutes', 'REM-сон'], ['awake_minutes', 'Бодрствование'],
] as const

function reportTime(value: string) {
  const offset = value.endsWith('Z') ? 'UTC' : `UTC${value.slice(-6)}`
  return `${dateLabel(value.slice(0, 10))}, ${value.slice(11, 19)} ${offset}`
}

export function SleepReportMeasurements({ night }: { night: SleepReportNight }) {
  return <dl className="measurements">
        <div><dt>Продолжительность</dt><dd>{sleepDurationLabel(night.duration_minutes)}</dd></div>
        {night.start_at && <div><dt>Засыпание</dt><dd><time dateTime={night.start_at}>{reportTime(night.start_at)}</time></dd></div>}
        {night.end_at && <div><dt>Пробуждение</dt><dd><time dateTime={night.end_at}>{reportTime(night.end_at)}</time></dd></div>}
        {phases.map(([key, label]) => night[key] != null && <div key={key}><dt>{label}</dt><dd>{night[key]} мин</dd></div>)}
        {night.sleep_score != null && <div><dt>Оценка сна</dt><dd>{night.sleep_score} из 100</dd></div>}
        {night.avg_heart_rate_bpm != null && <div><dt>Средний пульс</dt><dd>{night.avg_heart_rate_bpm} уд/мин</dd></div>}
        {night.overnight_hrv_ms != null && <div><dt>Ночная ВСР</dt><dd>{night.overnight_hrv_ms} мс</dd></div>}
  </dl>
}

function Nights({ nights }: { nights: SleepReportNight[] }) {
  return <ul className="sleep-report-nights">
    {nights.map(night => <li key={night.sleep_date}>
      <h4><time dateTime={night.sleep_date}>{dateLabel(night.sleep_date)}</time></h4>
      <SleepReportMeasurements night={night} />
    </li>)}
  </ul>
}

export function SleepReports({ athleteId }: { athleteId: number }) {
  const path = `/athletes/${athleteId}/sleep-reports`
  const client = useQueryClient()
  const session = useSession()
  const [offset, setOffset] = useState(0)
  const history = useAthleteResource<ReportHistory>(`${path}?offset=${offset}&limit=${pageSize}`)
  const fileInput = useRef<HTMLInputElement>(null)
  const activeRequest = useRef<AbortController | null>(null)
  const [file, setFile] = useState<File | null>(null)
  const [preview, setPreview] = useState<ReportPreview | null>(null)
  const [operation, setOperation] = useState<'preview' | 'save' | null>(null)
  const [error, setError] = useState<Error | null>(null)
  const [result, setResult] = useState<ReportResult | null>(null)
  const [fitResult, setFitResult] = useState<FitImport | null>(null)
  const isFit = file?.name.toLowerCase().endsWith('.fit') ?? false

  useEffect(() => () => activeRequest.current?.abort(), [])

  function chooseFile(selected: File | null) {
    setFile(selected)
    setError(null)
    setPreview(null)
    setResult(null)
    setFitResult(null)
  }

  async function send(mode: 'preview' | 'save') {
    if (!file || activeRequest.current) return
    setError(null)
    if (!isFit && file.size > maxBytes) {
      setError(new ApiError(413, 'sleep_report_too_large'))
      return
    }
    const controller = new AbortController()
    activeRequest.current = controller
    setOperation(mode)
    try {
      if (!session.data) throw new ApiError(401, 'authentication_required')
      const auth = { csrf: session.data.csrf_token, signal: controller.signal }
      if (isFit) {
        const saved = await request<FitImport>('/workouts/import', { ...auth, method: 'POST', body: file })
        chooseFile(null)
        if (fileInput.current) fileInput.current.value = ''
        setFitResult(saved)
        void client.invalidateQueries({ queryKey: ['athlete-data'] })
        return
      }
      const type = file.name.toLowerCase().endsWith('.csv') ? 'text/csv' : 'application/json'
      const options = { ...auth, body: new Blob([file], { type }) }
      if (mode === 'preview') {
        setPreview(await request<ReportPreview>(`${path}/preview`, { ...options, method: 'POST' }))
      } else {
        const saved = await request<ReportResult>(`${path}?confirm=share-and-replace`, { ...options, method: 'PUT' })
        chooseFile(null)
        if (fileInput.current) fileInput.current.value = ''
        setResult(saved)
        setOffset(0)
        void client.invalidateQueries({ queryKey: ['athlete-data'] })
      }
    } catch (cause) {
      if (controller.signal.aborted) return
      if (cause instanceof ApiError && cause.status === 401) forgetSession(client)
      setError(cause instanceof Error ? cause : new Error())
    } finally {
      activeRequest.current = null
      if (!controller.signal.aborted) setOperation(null)
    }
  }

  const errorText = error instanceof ApiError && error.code === 'sleep_report_night_invalid'
    ? `Ночь №${error.field}. ${explain(error)}` : error instanceof ApiError && error.code === 'report_workout_invalid'
      ? `Тренировка №${error.field}. ${explain(error)}` : error ? explain(error) : ''

  return <section className="panel sleep-reports" aria-labelledby="sleep-report-title">
    <h2 id="sleep-report-title" tabIndex={-1}>Импорт сна и тренировок</h2>
    <p>Выберите отчёт JSON или CSV либо файл тренировки FIT. <a href={`data:text/csv;charset=utf-8,${encodeURIComponent(csvTemplate)}`}
      download="activity-report.example.csv">Скачать шаблон CSV</a>.</p>
    <form className="sleep-report-upload" aria-label="Выбор отчёта о сне" onSubmit={event => { event.preventDefault(); void send('preview') }}>
      <label>Файл JSON, CSV или FIT<input ref={fileInput} type="file" accept=".json,.csv,.fit,application/json,text/csv" disabled={operation != null}
        onChange={event => chooseFile(event.target.files?.[0] ?? null)} /></label>
      <button disabled={!file || operation != null || preview != null}>{operation === 'preview' ? 'Обрабатываем…' : isFit ? 'Загрузить FIT' : 'Проверить файл'}</button>
    </form>
    {errorText && <p role="alert" className="error">{errorText}</p>}
    {preview && <div>
      <h3>Предпросмотр отчёта</h3>
      <p role="status">Ночей к загрузке — {preview.sleep.length}. Тренировок — {preview.workouts?.length ?? 0}.</p>
      {preview.sleep.length > 0 && <>
        <p>Показатели сна увидит тренер. При расхождении отчёт заменит ручные замеры за эту ночь.</p>
        <div className="sleep-report-preview-list" role="region" aria-label="Ночи из файла" tabIndex={0}>
          <Nights nights={preview.sleep} />
        </div>
      </>}
      {!!preview.workouts?.length && <ul aria-label="Тренировки из файла" className="sleep-report-preview-list">
        {preview.workouts.map(workout => <li key={workout.started_at}>
          {reportTime(workout.started_at)} · {Math.round(workout.timer_seconds / 60)} мин
          {workout.distance_m != null && ` · ${workout.distance_m / 1000} км`}
        </li>)}
      </ul>}
      <div className="actions">
        <button type="button" disabled={operation != null} onClick={() => void send('save')}>
          {operation === 'save' ? 'Сохраняем…' : 'Сохранить отчёт'}
        </button>
        <button type="button" className="secondary" disabled={operation != null} onClick={() => {
          chooseFile(null)
          if (fileInput.current) fileInput.current.value = ''
          fileInput.current?.focus()
        }}>Отмена</button>
      </div>
    </div>}
    {result && <p className="success" role="status">Ночей сохранено — {result.saved}. Без изменений — {result.unchanged}.
      {result.workouts_saved != null && ` Тренировок сохранено — ${result.workouts_saved}. Без изменений — ${result.workouts_unchanged}.`}</p>}
    {fitResult && <p className="success" role="status">
      {fitResult.status === 'duplicate' ? 'Тренировка уже в истории. ' : 'Тренировка сохранена. '}
      <Link to={`/workouts/${fitResult.workout_id}`}>Открыть тренировку</Link>
    </p>}
    <div role="region" aria-label="Загруженные ночи">
      <h3>Загруженные ночи</h3>
      <QueryFeedback loading={history.isPending} error={history.error} retry={() => void history.refetch()} />
      {history.data && <>
        {history.data.entries.length ? <Nights nights={history.data.entries} /> : <p className="muted">Отчётов пока нет.</p>}
        <Pager offset={offset} size={pageSize} hasMore={history.data.has_more} onChange={setOffset} />
      </>}
    </div>
  </section>
}
