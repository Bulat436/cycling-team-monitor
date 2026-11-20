import { useState } from 'react'
import type { FormEvent } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router'

import { QueryFeedback, SaveFeedback } from './AthleteFeedback'
import { ApiError, request } from './api'
import type { Session } from './api'
import { forgetSession, sessionKey } from './session'
import { recordedAt, useAthleteResource, useSaveAthleteData } from './athleteApi'

interface Connection {
  available: boolean
  validation_pending: boolean
  status: string
  diagnostic: string | null
  has_session: boolean
  can_disconnect: boolean
  history_days: number
  last_success_at: number | null
  next_sync_at: number | null
}

interface Run {
  id: number
  trigger: string
  status: string
  created_at: number
  started_at: number | null
  ended_at: number | null
  imported: number
  duplicates: number
  rejected: number
  diagnostic: string | null
  errors: string[]
}

const connectionLabels: Record<string, string> = {
  not_connected: 'Не подключено',
  checking: 'Проверка подключения',
  connected: 'Подключено',
  syncing: 'Синхронизация выполняется',
  synced: 'Синхронизировано',
  reauthorization: 'Требуется повторная авторизация',
  interaction_required: 'Требуется подтверждение в OnelapFit',
  temporary_error: 'Временная ошибка',
  unavailable: 'Интеграция недоступна',
}

const diagnostics: Record<string, string> = {
  provider_unavailable: 'Для прямой синхронизации нужен разрешённый шлюз OnelapFit.',
  key_unavailable: 'Сервер не может прочитать ключ шифрования OnelapFit.',
  provider_denied: 'Шлюз отклонил доступ к сервису.',
  account_already_linked: 'Этот аккаунт OnelapFit уже используется в команде.',
  invalid_credentials: 'Шлюз отклонил логин или пароль.',
  reauthorization: 'Откройте профиль и подключите аккаунт заново.',
  interaction_required: 'Завершите подтверждение в официальном приложении OnelapFit, затем подключите аккаунт заново.',
  account_mismatch: 'После обновления сессии шлюз вернул другой аккаунт. Подключите аккаунт заново.',
  rate_limited: 'Сервис ограничил частоту запросов. Повтор состоится после паузы.',
  network_error: 'Связь со шлюзом прервалась. Сервер повторит синхронизацию.',
  invalid_response: 'Шлюз вернул неожиданный ответ.',
  fit_rejected: 'Часть FIT-файлов отклонена. Причины доступны в журнале.',
  fit_bad_crc: 'Не прошла проверка CRC файла FIT.',
  fit_invalid_signature: 'Полученный файл не является FIT.',
  fit_invalid_structure: 'Структура FIT повреждена или не поддерживается.',
  fit_no_start: 'В FIT отсутствует время начала.',
  fit_not_activity: 'Файл FIT не содержит записанную тренировку.',
  fit_empty: 'Получен пустой FIT.',
  fit_too_large: 'FIT превышает разрешённый размер.',
  fit_too_complex: 'FIT превышает лимит длительности или числа записей.',
  fit_unavailable: 'Шлюз пока не предоставил исходный FIT.',
  fit_source_conflict: 'Файл и идентификатор источника относятся к разным тренировкам.',
  fit_import_error: 'Сервер не смог сохранить FIT. Запланирован повтор.',
  interrupted: 'Синхронизация прервалась. Сохранённые тренировки доступны.',
  connection_changed: 'Подключение было изменено.',
  revocation_unconfirmed: 'Локальная сессия удалена. Отзыв доступа у шлюза не подтверждён.',
  internal_sync_error: 'Сервер прервал синхронизацию и повторит её позже.',
}

const runLabels: Record<string, string> = {
  queued: 'В очереди', running: 'Выполняется', complete: 'Завершён', partial: 'Завершён с ошибками FIT',
  failed: 'Ошибка', cancelled: 'Отменён', interrupted: 'Прерван',
}
const triggerLabels: Record<string, string> = { automatic: 'По расписанию', manual: 'Вручную', verify: 'Проверка подключения' }

function useConnection(athleteId: number) {
  return useAthleteResource<Connection>('/athletes/' + athleteId + '/onelapfit', 10_000)
}

function StatusLabel({ connection }: { connection: Connection }) {
  const tone = connection.status === 'synced' ? 'active' : connection.status === 'unavailable' ? 'neutral' : 'pending'
  return <span className={'status ' + tone}>{connectionLabels[connection.status] ?? 'Статус недоступен'}</span>
}

export function OnelapSummary({ athleteId }: { athleteId: number }) {
  const query = useConnection(athleteId)
  if (query.isPending) return <p className="muted">Проверяем статус OnelapFit…</p>
  if (!query.data) return <p className="muted">Статус OnelapFit недоступен.</p>
  return <p>OnelapFit · <StatusLabel connection={query.data} /></p>
}

function History({ athleteId, timezone }: { athleteId: number; timezone: string }) {
  const history = useAthleteResource<Run[]>('/athletes/' + athleteId + '/onelapfit/runs', 10_000)
  return (
    <details className="entry-item">
      <summary>Журнал синхронизации</summary>
      <QueryFeedback loading={history.isPending} error={history.error} retry={() => void history.refetch()} />
      {history.data?.length === 0 && <p className="muted">Запусков пока нет.</p>}
      {!!history.data?.length && <div className="table-wrap">
        <table>
          <thead><tr><th scope="col">Дата</th><th scope="col">Запуск</th><th scope="col">Результат</th></tr></thead>
          <tbody>{history.data.map(run => (
            <tr key={run.id}>
              <td>{recordedAt(run.started_at ?? run.created_at, timezone)}</td>
              <td>{triggerLabels[run.trigger]}</td>
              <td>
                <strong>{runLabels[run.status]}</strong>
                {run.trigger !== 'verify' && <p>Сохранено {run.imported} · Дубликаты {run.duplicates} · Отклонено {run.rejected}</p>}
                {[...new Set([run.diagnostic, ...run.errors])].map(code => code && (
                  <p key={code} className="muted">{diagnostics[code] ?? 'Ошибка синхронизации'}</p>
                ))}
              </td>
            </tr>
          ))}</tbody>
        </table>
      </div>}
    </details>
  )
}

export function OnelapSection({ athleteId, timezone, coach }: { athleteId: number; timezone: string; coach: boolean }) {
  const query = useConnection(athleteId)
  const client = useQueryClient()
  const [connecting, setConnecting] = useState(false)
  const [connectionError, setConnectionError] = useState<Error | null>(null)
  const action = useSaveAthleteData()
  const connection = query.data
  const active = connection?.status === 'syncing' || connection?.status === 'checking'
  const base = '/athletes/' + athleteId + '/onelapfit'

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const form = event.currentTarget
    const data = new FormData(form)
    const body = { login: String(data.get('login')).trim(), password: data.get('password'), history_days: Number(data.get('history_days')) }
    form.reset()
    const session = client.getQueryData<Session | null>(sessionKey)
    if (!session) return
    action.reset()
    setConnecting(true)
    setConnectionError(null)
    try {
      await request('/onelapfit/connect', { method: 'POST', csrf: session.csrf_token, body })
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) forgetSession(client)
      else setConnectionError(error instanceof Error ? error : new Error('connection_failed'))
    } finally {
      setConnecting(false)
      void query.refetch()
    }
  }

  return (
    <section className="panel">
      <h2>OnelapFit</h2>
      <QueryFeedback loading={query.isPending} error={query.error} retry={() => void query.refetch()} />
      {connection && (
        <>
          <div className="actions">
            <StatusLabel connection={connection} />
            {connection.validation_pending && <span className="status neutral">Ожидает проверки на реальном аккаунте</span>}
          </div>
          {connection.diagnostic && <p className="muted">{diagnostics[connection.diagnostic] ?? 'Ошибка подключения'}</p>}
          {connection.last_success_at != null && <p>Последняя успешная синхронизация · {recordedAt(connection.last_success_at, timezone)}</p>}
          {connection.has_session && connection.next_sync_at != null && !active && (
            <p className="muted">Следующая попытка · {recordedAt(connection.next_sync_at, timezone)}</p>
          )}
          {!coach && !connection.available && <Link to="/workouts">Загрузить FIT вручную</Link>}
          {!coach && connection.available && !connection.has_session && (
            <form className="form-stack" onSubmit={submit}>
              <p className="muted">Вход выполняется через настроенный HTTPS-шлюз. Cycling Team Monitor хранит только зашифрованную сессию.</p>
              <div className="form-grid">
                <label>Почта или телефон OnelapFit<input name="login" autoComplete="off" maxLength={254} required /></label>
                <label>Пароль OnelapFit<input name="password" type="password" autoComplete="new-password" maxLength={512} required /></label>
                <label>Глубина первой загрузки, дней<input name="history_days" type="number" min={1} max={365} defaultValue={connection.history_days} required /></label>
              </div>
              <div className="actions"><button disabled={connecting || active}>{connecting ? 'Проверяем подключение…' : 'Подключить OnelapFit'}</button></div>
            </form>
          )}
          <SaveFeedback error={connectionError} saved={false} />
          {(connection.has_session || (!coach && connection.can_disconnect)) && (
            <div className="actions">
              {connection.available && connection.has_session && <>
                <button type="button" className="secondary" disabled={active || action.isPending} onClick={() => action.mutate({ path: base + '/test', method: 'POST' })}>Проверить подключение</button>
                <button type="button" disabled={active || action.isPending} onClick={() => action.mutate({ path: base + '/sync', method: 'POST' })}>Синхронизировать сейчас</button>
              </>}
              {!coach && connection.can_disconnect && <button type="button" className="secondary danger" disabled={action.isPending} onClick={() => action.mutate({ path: '/onelapfit/disconnect', method: 'POST' })}>Отключить OnelapFit</button>}
            </div>
          )}
          <SaveFeedback error={action.error} saved={false} />
          {action.isSuccess && <p className="success" role="status">{action.variables?.path === '/onelapfit/disconnect' ? 'Подключение отключено.' : 'Запуск добавлен в очередь.'}</p>}
          <History athleteId={athleteId} timezone={timezone} />
        </>
      )}
    </section>
  )
}
