import { useState } from 'react'
import type { FormEvent } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router'

import { ApiError, explain, formatDate, request, roleNames } from './api'
import type { AccessLink, Account, AuditEntry, Invitation, Session } from './api'
import { forgetSession } from './session'
import { QueryFeedback } from './AthleteFeedback'

const usersKey = ['admin', 'users'] as const
const invitationsKey = ['admin', 'invitations'] as const
const recentAuditKey = ['admin', 'audit'] as const

function useAccounts() {
  return useQuery({ queryKey: usersKey, queryFn: ({ signal }) => request<Account[]>('/admin/users', { signal }) })
}

function accountStatus(user: Account) {
  if (!user.active) return { label: 'Деактивирован', tone: 'neutral' }
  if (!user.activated_at) return { label: 'Ждёт активации', tone: 'pending' }
  if (user.password_pending) return { label: 'Сброс пароля', tone: 'pending' }
  return { label: 'Активен', tone: 'active' }
}

function invitationStatus(link: Invitation) {
  if (link.consumed_at) return 'Использована'
  if (link.revoked_at) return 'Отозвана'
  if (link.expires_at * 1000 <= Date.now()) return 'Истекла'
  return 'Действует'
}

export function ManageAccess({ session }: { session: Session }) {
  const client = useQueryClient()
  const accounts = useAccounts()
  const invitations = useQuery({
    queryKey: invitationsKey,
    queryFn: ({ signal }) => request<Invitation[]>('/admin/invitations', { signal }),
  })
  const [link, setLink] = useState<AccessLink>()
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState(false)
  const shareUrl = link && window.location.origin + (link.kind === 'reset' ? '/reset#' : '/invite#') + link.token

  async function run(action: () => Promise<AccessLink | void>, message: string) {
    setBusy(true)
    setError('')
    setNotice('')
    setLink(undefined)
    try {
      const result = await action()
      if (result) setLink(result)
      setNotice(message)
      await Promise.all([
        client.invalidateQueries({ queryKey: ['admin'] }),
        client.invalidateQueries({ queryKey: ['athlete-data'] }),
      ])
      return true
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) forgetSession(client)
      setError(explain(error))
      return false
    } finally {
      setBusy(false)
    }
  }

  function deleteAccount(account: Account) {
    const message = `Удалить аккаунт «${account.name}»? Доступ по этому аккаунту будет закрыт безвозвратно.`
    if (!window.confirm(account.role === 'athlete' ? message + ' Дневник и тренировки останутся в архиве.' : message)) return
    void run(() => request<void>('/admin/users/' + account.id, {
      method: 'DELETE', csrf: session.csrf_token,
    }), 'Аккаунт удалён.')
  }

  async function invite(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const form = event.currentTarget
    const body = Object.fromEntries(new FormData(form))
    if (await run(() => request<AccessLink>('/admin/invitations', { method: 'POST', csrf: session.csrf_token, body }), 'Приглашение создано.')) {
      form.reset()
    }
  }

  function accountAction(account: Account, action: 'deactivate' | 'invitation' | 'password-reset') {
    const descriptions = {
      deactivate: 'Доступ закрыт, активные сессии завершены.',
      invitation: 'Новое приглашение создано.',
      'password-reset': 'Ссылка для сброса создана, активные сессии завершены.',
    }
    if (action === 'deactivate' && !window.confirm('Закрыть доступ для «' + account.name + '»?')) return
    if (action === 'password-reset' && !window.confirm('Сбросить пароль для «' + account.name + '»? Текущий пароль перестанет действовать.')) return
    void run(() => request<AccessLink | void>('/admin/users/' + account.id + '/' + action, {
      method: 'POST', csrf: session.csrf_token,
    }), descriptions[action])
  }

  async function copyLink() {
    if (!shareUrl) return
    try {
      await navigator.clipboard.writeText(shareUrl)
      setNotice('Ссылка скопирована.')
    } catch {
      setNotice('Выделите и скопируйте ссылку из поля.')
    }
  }

  return (
    <>
      <header className="page-heading">
        <div><h1>Управление доступом</h1><p>Приглашения и учётные записи команды</p></div>
        <Link className="button secondary" to="/access/audit">Журнал действий</Link>
      </header>
      <section className="panel">
        <h2>Новое приглашение</h2>
        <form className="invite-form" onSubmit={invite}>
          <label>Имя и фамилия<input name="name" autoComplete="off" maxLength={120} required /></label>
          <label>Электронная почта<input name="email" type="email" autoComplete="off" maxLength={254} required /></label>
          <label>Роль<select name="role" defaultValue="athlete"><option value="athlete">Спортсмен</option><option value="coach">Тренер</option></select></label>
          <button disabled={busy}>Создать приглашение</button>
        </form>
        {error && <p className="error" role="alert">{error}</p>}
        {notice && <p className="success" role="status">{notice}</p>}
        {shareUrl && link && (
          <div className="share">
            <label>Одноразовая ссылка<input readOnly value={shareUrl} onFocus={event => event.currentTarget.select()} /></label>
            <button type="button" className="secondary" onClick={() => void copyLink()}>Скопировать</button>
            <p className="muted">Действует до {formatDate(link.expires_at)}. Передайте её только получателю.</p>
          </div>
        )}
      </section>
      <section className="panel">
        <h2>Учётные записи</h2>
        <QueryFeedback loading={accounts.isPending} error={accounts.error} retry={() => void accounts.refetch()} />
        {accounts.data && (
          <div className="account-list">
            {accounts.data.map(account => {
              const status = accountStatus(account)
              return (
                <article className="account" key={account.id}>
                  <div><h3>{account.name}{account.id === session.user.id && ' (вы)'}</h3>
                    <p>{account.email} · {account.is_head_coach ? 'Главный тренер' : roleNames[account.role]}</p></div>
                  <span className={'status ' + status.tone}>{status.label}</span>
                  {account.active && !account.is_head_coach && account.id !== session.user.id && <div className="actions">
                    {account.activated_at === null ?
                      <button disabled={busy} className="secondary" onClick={() => accountAction(account, 'invitation')}>Новое приглашение</button> :
                      <button disabled={busy} className="secondary" onClick={() => accountAction(account, 'password-reset')}>Сбросить пароль</button>}
                    <button disabled={busy} className="secondary danger" onClick={() => accountAction(account, 'deactivate')}>Деактивировать</button>
                  </div>}
                  {!account.active && !account.is_head_coach && session.user.is_head_coach &&
                    <button type="button" disabled={busy} className="secondary danger" onClick={() => deleteAccount(account)}>
                      Удалить аккаунт
                    </button>}
                </article>
              )
            })}
          </div>
        )}
      </section>
      <section className="panel">
        <h2>Последние ссылки доступа</h2>
        <QueryFeedback loading={invitations.isPending} error={invitations.error} retry={() => void invitations.refetch()} />
        {invitations.data?.length === 0 && <p className="muted">Ссылок пока нет.</p>}
        {invitations.data?.map(item => {
          const label = invitationStatus(item)
          return (
            <article className="account" key={item.id}>
              <div>
                <h3>{accounts.data?.find(user => user.id === item.user_id)?.name ?? 'Пользователь №' + item.user_id}</h3>
                <p>{item.kind === 'reset' ? 'Сброс пароля' : 'Приглашение'} · До {formatDate(item.expires_at)}</p>
              </div>
              <span className={'status ' + (label === 'Действует' ? 'active' : 'neutral')}>{label}</span>
              {label === 'Действует' && <button disabled={busy} className="secondary danger" onClick={() => void run(() => request<void>('/admin/invitations/' + item.id + '/revoke', {
                method: 'POST', csrf: session.csrf_token,
              }), 'Ссылка отозвана.')}>Отозвать</button>}
            </article>
          )
        })}
      </section>
    </>
  )
}

const auditActions: Record<string, string> = {
  coach_bootstrapped: 'Создан первый тренер',
  athlete_profile_updated: 'Изменён профиль спортсмена',
  athlete_ftp_updated: 'Изменён FTP спортсмена',
  athlete_weight_updated: 'Изменена масса спортсмена',
  athlete_zones_updated: 'Изменены зоны спортсмена',
  workout_reprocessed: 'Повторно обработан FIT',
  onelap_connected: 'Подключён OnelapFit',
  onelap_disconnected: 'Отключён OnelapFit',
  onelap_sync_requested: 'Запрошена синхронизация OnelapFit',
  alert_settings_updated: 'Изменены правила предупреждений',
  workout_parameters_refreshed: 'Пересчитана тренировка с параметрами профиля',
  coach_password_reset_by_operator: 'Пароль тренера восстановлен администратором сервера',
  invitation_issued: 'Выдано приглашение',
  invitation_revoked: 'Отозвана ссылка доступа',
  password_reset_issued: 'Запрошен сброс пароля',
  access_accepted: 'Установлен пароль',
  account_deactivated: 'Учётная запись деактивирована',
  account_deleted: 'Учётная запись удалена',
  login: 'Вход',
  logout: 'Выход',
}

export function Audit() {
  const accounts = useAccounts()
  const [offset, setOffset] = useState(0)
  const audit = useQuery({
    queryKey: [...recentAuditKey, offset],
    queryFn: ({ signal }) => request<AuditEntry[]>('/admin/audit?offset=' + offset, { signal }),
  })
  const name = (id: number | null) => id === null ? 'Система' : accounts.data?.find(user => user.id === id)?.name ?? '№' + id
  return (
    <>
      <header className="page-heading">
        <div><h1>Журнал действий</h1><p>Изменения доступа и события авторизации</p></div>
        <Link className="button secondary" to="/access">Управление доступом</Link>
      </header>
      <section className="panel">
        <QueryFeedback loading={audit.isPending} error={audit.error} retry={() => void audit.refetch()} />
        {audit.data?.length ? (
          <div className="table-wrap">
            <table>
              <thead><tr><th scope="col">Дата</th><th scope="col">Действие</th><th scope="col">Кто</th><th scope="col">Аккаунт</th></tr></thead>
              <tbody>{audit.data.map(entry => <tr key={entry.id}><td>{formatDate(entry.created_at)}</td><th scope="row">{auditActions[entry.action] ?? entry.action}</th><td>{name(entry.actor_id)}</td><td>{entry.target_id === null ? '—' : name(entry.target_id)}</td></tr>)}</tbody>
            </table>
          </div>
        ) : audit.data && <p className="muted">Записей нет.</p>}
        <div className="actions">
          {offset > 0 && <button className="secondary" onClick={() => setOffset(offset - 100)}>Новые записи</button>}
          {audit.data?.length === 100 && <button className="secondary" onClick={() => setOffset(offset + 100)}>Ранние записи</button>}
        </div>
      </section>
    </>
  )
}
