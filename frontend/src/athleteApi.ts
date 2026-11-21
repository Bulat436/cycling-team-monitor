import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { ApiError, request } from './api'
import type { Session } from './api'
import { forgetSession, sessionKey } from './session'

export interface ProfileSummary {
  athlete_id: number
  name: string
  active: boolean
  team_group: string | null
  timezone: string
}

export interface FtpMeasurement { effective_from: string; watts: number }
export interface WeightMeasurement { id: number; measured_on: string; kg: number }
export interface Zone { name: string; minimum: number; maximum: number | null }

export interface AthleteProfile extends ProfileSummary {
  email: string
  birth_date: string | null
  height_cm: number | null
  specialization: string | null
  coach_notes: string | null
  local_date: string
  indicators_updated_at: number | null
  current_ftp: FtpMeasurement | null
  current_weight: WeightMeasurement | null
  ftp_history: FtpMeasurement[]
  weight_history: WeightMeasurement[]
  power_zones: Zone[]
  heart_rate_zones: Zone[]
}

export const diaryFields = [
  { name: 'sleep_quality', label: 'Качество сна', direction: 'positive' },
  { name: 'general_fatigue', label: 'Общая усталость', direction: 'negative' },
  { name: 'leg_fatigue', label: 'Усталость ног', direction: 'negative' },
  { name: 'motivation', label: 'Мотивация', direction: 'positive' },
  { name: 'mood', label: 'Настроение', direction: 'positive' },
  { name: 'muscle_soreness', label: 'Мышечная болезненность', direction: 'negative' },
] as const

export type WellnessScores = Record<typeof diaryFields[number]['name'], number>
export type TrainingDayConfirmation = 'rest' | 'all_workouts_recorded'

export interface WellnessEntry extends WellnessScores {
  entry_date: string
  timezone: string
  illness_symptoms: boolean
  comment: string | null
  sleep_minutes?: number | null
  recovery?: number | null
  training_day_status?: TrainingDayConfirmation | null
  created_at: number
  updated_at: number
}

export interface WellnessHistory {
  timezone: string
  local_date: string
  today_entry: WellnessEntry | null
  entries: WellnessEntry[]
  has_more: boolean
}

export interface TeamWellness extends ProfileSummary {
  local_date: string
  entry: WellnessEntry | null
}

export interface RpeEntry {
  workout_id: number
  score: number
  comment: string | null
  session_load: number | null
  created_at: number
  updated_at: number
}

export interface WorkoutSummary {
  elapsed_seconds: number | null
  moving_seconds: number | null
  distance_m: number | null
  avg_speed: number | null
  max_speed: number | null
  ascent_m: number | null
  descent_m: number | null
  avg_power: number | null
  max_power: number | null
  avg_heart_rate: number | null
  max_heart_rate: number | null
  avg_cadence: number | null
  max_cadence: number | null
  avg_temperature: number | null
  max_temperature: number | null
  min_altitude_m: number | null
  max_altitude_m: number | null
  calories: number | null
  gps: boolean
  sample_count: number
}

export interface ZoneTotals {
  zones: (Zone & { seconds: number })[]
  outside_seconds: number
  unknown_seconds: number
}

export interface WorkoutDetail extends Workout {
  timezone: string
  original_available: boolean
  sources: { source: string }[]
  metrics: {
    version: string
    snapshot: { ftp_watts: number | null; ftp_effective_from: string | null }
    result: {
      if_value: number | null
      power_zones: ZoneTotals | null
      heart_rate_zones: ZoneTotals | null
      power_distribution: { minimum: number; maximum: number; seconds: number }[] | null
      quality: {
        timing_source: string
        expected_seconds: number | null
        covered_seconds: Record<string, number>
        missing_channels: string[]
      }
      reasons: Record<string, string>
    }
  } | null
}

export interface FitImport {
  workout_id: number
  status: 'imported' | 'duplicate' | 'updated'
  potential_duplicates: number[]
}

export interface Workout {
  id: number
  athlete_id: number
  started_at: number
  timer_seconds: number | null
  summary?: WorkoutSummary | null
  np?: number | null
  tss?: number | null
  rpe: RpeEntry | null
}

export interface WorkoutList {
  timezone: string
  workouts: Workout[]
  missing_rpe_count: number
  has_more: boolean
}

export function useAthleteResource<T>(path: string, refetchInterval?: number, retainPrevious = false) {
  return useQuery({
    queryKey: ['athlete-data', path],
    queryFn: ({ signal }) => request<T>(path, { signal }),
    refetchInterval,
    placeholderData: retainPrevious ? keepPreviousData : undefined,
  })
}

export function useSaveAthleteData<T = unknown>() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: ({ path, method = 'PUT', body }: { path: string; method?: 'POST' | 'PUT'; body?: unknown }) => {
      const session = client.getQueryData<Session | null>(sessionKey)
      if (!session) throw new ApiError(401, 'authentication_required')
      return request<T>(path, { method, body, csrf: session.csrf_token })
    },
    onSuccess: async () => {
      await client.invalidateQueries({ queryKey: ['athlete-data'] })
    },
    onError: error => {
      if (error instanceof ApiError && error.status === 401) forgetSession(client)
    },
  })
}

export function dateLabel(date: string) {
  return new Intl.DateTimeFormat('ru', { dateStyle: 'long', timeZone: 'UTC' })
    .format(new Date(date + 'T00:00:00Z'))
}

export function recordedAt(timestamp: number, timeZone: string) {
  const options = { dateStyle: 'medium', timeStyle: 'medium' } as const
  // Older browser ICU databases can lack a zone the server knows.
  try {
    return new Intl.DateTimeFormat('ru', { ...options, timeZone }).format(timestamp * 1000)
  } catch (error) {
    if (!(error instanceof RangeError)) throw error
    return new Intl.DateTimeFormat('ru', { ...options, timeZone: 'UTC' }).format(timestamp * 1000) + ' UTC'
  }
}

export const numberLabel = (value: number) =>
  new Intl.NumberFormat('ru', { maximumFractionDigits: 2 }).format(value)

export function durationLabel(seconds: number | null) {
  return seconds === null ? 'Нет данных' : numberLabel(seconds / 60) + ' мин'
}

export const metricReasonLabels: Record<string, string> = {
  channel_missing: 'В FIT нет записей этого датчика.',
  zones_not_configured: 'Индивидуальные зоны не настроены для этого расчёта.',
  timer_unresolved: 'По FIT не удалось определить участки с работающим таймером.',
  no_active_samples: 'Нет показаний при работающем таймере.',
  short_power_windows: 'Для NP нужно хотя бы одно полное окно длительностью 30 секунд.',
  insufficient_power_coverage: 'В ряду мощности слишком много пропусков для расчёта NP.',
  not_recorded: 'Значение отсутствует в FIT.',
  rpe_missing: 'Спортсмен ещё не указал RPE.',
  ftp_missing: 'В истории нет FTP, действовавшего на дату тренировки.',
  timer_missing: 'В FIT отсутствует надёжное время таймера.',
  speed_coverage_missing: 'В FIT нет времени движения и недостаточно показаний скорости.',
  non_cycling_activity: 'NP рассчитан только для велотренировок.',
}
