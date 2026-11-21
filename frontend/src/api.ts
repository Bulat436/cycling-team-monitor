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
  invalid_analytics_range: 'Укажите период до 366 дней. Дата начала должна предшествовать или совпадать с датой окончания.',
  onelap_provider_unavailable: 'Интеграция недоступна. Используйте ручной импорт FIT.',
  onelap_key_unavailable: 'Проверьте настройки шифрования на сервере.',
  onelap_https_required: 'Для подключения откройте приложение через HTTPS.',
  onelap_invalid_credentials: 'Проверьте почту или телефон и пароль OnelapFit.',
  onelap_interaction_required: 'Завершите подтверждение в официальном OnelapFit и попробуйте снова.',
  onelap_sync_busy: 'Дождитесь окончания текущей операции.',
  onelap_already_connected: 'OnelapFit уже подключён.',
  onelap_account_already_linked: 'Этот аккаунт OnelapFit уже используется в команде.',
  onelap_not_connected: 'Подключите OnelapFit в профиле спортсмена.',
  onelap_rate_limited: 'Сервис ограничил частоту запросов. Повторите попытку позже.',
  onelap_sync_backoff: 'Перед повторной попыткой нужно подождать.',
  onelap_network_error: 'Не удалось связаться со шлюзом OnelapFit.',
  onelap_provider_denied: 'Шлюз отклонил доступ к OnelapFit.',
  onelap_invalid_response: 'Шлюз вернул неожиданный ответ.',
  onelap_connection_changed: 'Подключение было изменено. Обновите страницу.',
  fit_empty: 'Файл пуст. Выберите исходный FIT-файл тренировки.',
  fit_extension: 'Выберите файл с расширением .fit.',
  fit_invalid_signature: 'Содержимое файла не похоже на FIT.',
  fit_invalid_structure: 'Структура FIT повреждена или не поддерживается. Попробуйте экспортировать тренировку снова.',
  fit_bad_crc: 'Проверка целостности FIT не прошла. Экспортируйте файл повторно.',
  fit_not_activity: 'Для импорта нужен FIT с записанной тренировкой.',
  fit_no_start: 'В FIT отсутствует время начала тренировки.',
  fit_too_large: 'Файл превышает допустимый размер загрузки.',
  fit_too_complex: 'Файл превышает допустимую длительность или число записей.',
  fit_content_type: 'Отправьте FIT как двоичный файл.',
  fit_source_conflict: 'Источники указывают на разные тренировки. Обратитесь к тренеру.',
  fit_original_missing: 'Исходный FIT-файл недоступен.',
  fit_original_changed: 'Исходный файл изменился. Обновите карточку и повторите действие.',
  fit_storage_error: 'Сервер не смог записать FIT-файл. Повторите попытку позже.',
  fit_power_unavailable: 'Для разметки нужна велотренировка FIT с записью мощности.',
  incomplete_effort_power: 'Сервер не смог проверить мощность по всему отрезку. Выберите участок с непрерывной записью и без пауз.',
  athlete_inactive: 'Учётная запись спортсмена деактивирована.',
  future_birth_date: 'Дата рождения не может быть в будущем.',
  future_weight_date: 'Дата измерения массы не может быть в будущем.',
  measurement_missing: 'Измерение массы недоступно.',
  future_diary_date: 'Анкету можно заполнить за сегодня или за прошедшую дату.',
  training_day_not_completed: 'Подтвердить отдых или полноту записей можно после завершения дня.',
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
  const file = options.body instanceof File ? options.body : null
  if (file) {
    headers.set('Content-Type', 'application/octet-stream')
    headers.set('X-FIT-Filename', encodeURIComponent(file.name))
  } else if (options.body !== undefined) {
    headers.set('Content-Type', 'application/json')
  }
  if (options.csrf) headers.set('X-CSRF-Token', options.csrf)
  const response = await fetch('/api/v1' + path, {
    method: options.method ?? 'GET',
    headers,
    body: file ?? (options.body === undefined ? undefined : JSON.stringify(options.body)),
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
