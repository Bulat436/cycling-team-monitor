export type SleepSource = 'manual' | 'legacy' | 'automatic'

export const sleepSources: Record<SleepSource, string> = {
  manual: 'Введено вручную',
  legacy: 'Историческая запись',
  automatic: 'Загруженный отчёт',
}

export interface SleepDay {
  date: string
  timezone: string
  version: number
  updated_at: number | null
  duration_minutes: number | null
  source: SleepSource | null
  selected_source: SleepSource | null
  conflict: boolean
  manually_corrected: boolean
  candidates: Partial<Record<SleepSource, number>>
  start_time: string | null
  end_time: string | null
  start_offset_minutes: number | null
  end_offset_minutes: number | null
  entered_duration_minutes: number | null
  calculated_duration_minutes: number | null
  sleep_quality: number | null
  recovery: number | null
  has_diary: boolean
  has_report?: boolean
}

export interface SleepHistory {
  timezone: string
  local_date: string
  entries: SleepDay[]
  has_more: boolean
}

export const sleepDiaryPath = '/diary/sleep'
export const sleepApi = (id: number) => `/athletes/${id}/sleep`

export function isCalendarDate(value: string) {
  const timestamp = Date.parse(value)
  return /^\d{4}-\d{2}-\d{2}$/.test(value) && Number.isFinite(timestamp) && new Date(timestamp).toISOString().slice(0, 10) === value
}

export function hasManualSleep(day: SleepDay) {
  return day.start_time != null || day.end_time != null || day.entered_duration_minutes != null
    || (!day.has_diary && (day.sleep_quality != null || day.recovery != null))
}

export function sleepDurationLabel(minutes: number | null) {
  if (minutes == null) return 'Длительность не указана'
  if (minutes === 0) return '0 мин · указано отсутствие сна'
  const hours = Math.floor(minutes / 60)
  const remainder = minutes % 60
  return hours ? `${hours} ч${remainder ? ` ${remainder} мин` : ''}` : `${remainder} мин`
}

export function sleepRecordLabel(day: SleepDay) {
  return day.duration_minutes == null && !day.has_report && !hasManualSleep(day) && !Object.keys(day.candidates).length
    ? 'Записи сна нет' : sleepDurationLabel(day.duration_minutes)
}
