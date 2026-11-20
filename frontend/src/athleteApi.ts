import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

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

export interface WellnessEntry extends WellnessScores {
  entry_date: string
  timezone: string
  illness_symptoms: boolean
  comment: string | null
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

export interface Workout {
  id: number
  athlete_id: number
  started_at: number
  timer_seconds: number | null
  rpe: RpeEntry | null
}

export interface WorkoutList {
  timezone: string
  workouts: Workout[]
  missing_rpe_count: number
  has_more: boolean
}

export function useAthleteResource<T>(path: string, refetchInterval?: number) {
  return useQuery({
    queryKey: ['athlete-data', path],
    queryFn: ({ signal }) => request<T>(path, { signal }),
    refetchInterval,
  })
}

export function useSaveAthleteData() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: ({ path, method = 'PUT', body }: { path: string; method?: 'POST' | 'PUT'; body: unknown }) => {
      const session = client.getQueryData<Session | null>(sessionKey)
      if (!session) throw new ApiError(401, 'authentication_required')
      return request(path, { method, body, csrf: session.csrf_token })
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
