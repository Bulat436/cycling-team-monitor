export type Role = 'athlete' | 'coach'

export interface Identity {
  id: number
  email: string
  name: string
  role: Role
}

export interface Account extends Identity {
  active: boolean
  activated_at: number | null
  password_pending: boolean
}

export interface Session {
  user: Identity
  csrf_token: string
}

export type AccessKind = 'activation' | 'reset'

export interface AccessLink {
  id: number
  kind: AccessKind
  token: string
  expires_at: number
}

export interface Invitation {
  id: number
  user_id: number
  issued_by: number
  kind: AccessKind
  expires_at: number
  consumed_at: number | null
  revoked_at: number | null
}

export interface AuditEntry {
  id: number
  actor_id: number | null
  target_id: number | null
  action: string
  created_at: number
}

const messages: Record<string, string> = {
  invalid_credentials: 'Проверьте почту и пароль.',
  authentication_required: 'Сессия завершилась. Войдите снова.',
  role_denied: 'У вас нет доступа к этому разделу.',
  csrf_invalid: 'Обновите страницу и повторите действие.',
  origin_denied: 'Этот адрес приложения не разрешён сервером.',
  login_limited: 'Слишком много попыток входа. Попробуйте позже.',
  access_invalid: 'Ссылка истекла, отозвана или уже использована. Запросите новую у тренера.',
  email_exists: 'Учётная запись с такой почтой уже существует.',
  activation_unavailable: 'Для этой учётной записи нельзя выдать приглашение.',
  invitation_inactive: 'Эта ссылка уже недействительна.',
  reset_unavailable: 'Для этой учётной записи нельзя сбросить пароль.',
  self_deactivation_denied: 'Нельзя деактивировать собственную учётную запись.',
  validation_error: 'Проверьте введённые данные.',
  athlete_missing: 'Профиль спортсмена недоступен.',
  workout_missing: 'Тренировка недоступна.',
  future_birth_date: 'Дата рождения не может быть в будущем.',
  future_weight_date: 'Дата измерения массы не может быть в будущем.',
  measurement_missing: 'Измерение массы недоступно.',
  future_diary_date: 'Анкету можно заполнить за сегодня или за прошедшую дату.',
  invalid_heart_rate_bounds: 'Границы пульсовых зон должны быть в диапазоне 0–300 уд/мин.',
}

export class ApiError extends Error {
  constructor(public status: number, code: string) {
    super(messages[code] ?? 'Не удалось выполнить действие. Повторите попытку позже.')
  }
}

interface RequestOptions {
  method?: 'GET' | 'POST' | 'PUT'
  body?: unknown
  csrf?: string
  signal?: AbortSignal
}

export async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const headers = new Headers()
  if (options.body !== undefined) headers.set('Content-Type', 'application/json')
  if (options.csrf) headers.set('X-CSRF-Token', options.csrf)
  const response = await fetch('/api/v1' + path, {
    method: options.method ?? 'GET',
    headers,
    body: options.body === undefined ? undefined : JSON.stringify(options.body),
    signal: options.signal,
    credentials: 'same-origin',
  })
  if (!response.ok) {
    const error = await response.json().catch(() => ({}))
    throw new ApiError(response.status, error.detail)
  }
  return response.status === 204 ? undefined as T : response.json()
}

export function explain(error: unknown) {
  return error instanceof ApiError ? error.message : 'Не удалось связаться с сервером. Повторите попытку.'
}

export const roleNames: Record<Role, string> = { athlete: 'Спортсмен', coach: 'Тренер' }

export function formatDate(timestamp: number) {
  return new Intl.DateTimeFormat('ru', { dateStyle: 'medium', timeStyle: 'short' }).format(timestamp * 1000)
}
