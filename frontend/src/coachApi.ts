import type { WellnessEntry, WellnessScores } from './athleteApi'
import type { Connection } from './OnelapFit'

export const alertLabels = {
  diary_missing: 'Дневник пропущен',
  illness: 'Отмечены симптомы',
  fatigue: 'Высокая усталость',
  soreness: 'Мышечная болезненность',
  low_motivation: 'Низкая мотивация',
  rpe_missing: 'Отсутствует RPE',
  sync_error: 'Ошибка синхронизации',
  inactive_training: 'Давно без тренировок',
}

export type AlertCode = keyof typeof alertLabels
export interface Advisory { code: AlertCode; value: number | null }

export interface AlertSettings {
  enabled: AlertCode[]
  diary_due_hour: number
  fatigue_min: number
  soreness_min: number
  motivation_max: number
  rpe_grace_hours: number
  rpe_lookback_days: number
  inactive_days: number
}

export interface DateRange { start: string; end: string }
export type Preset = '7' | '28' | '90' | 'custom'
export type SelectedPeriod = { preset: Exclude<Preset, 'custom'> } | ({ preset: 'custom' } & DateRange)

export function periodQuery(period: SelectedPeriod) {
  const params = new URLSearchParams({ period: period.preset })
  if (period.preset === 'custom') {
    params.set('start', period.start)
    params.set('end', period.end)
  }
  return params
}

export interface Load {
  workouts: number
  timer_seconds: number | null
  distance_m: number | null
  tss: number | null
  rpe_load: number | null
  tss_count: number
  rpe_count: number
  rpe_load_count: number
}

export interface LastWorkout {
  id: number
  started_at: number
  timer_seconds: number | null
  distance_m: number | null
  rpe: number | null
}

export interface TeamRow {
  athlete_id: number
  name: string
  group: string | null
  timezone: string
  today: string
  account: 'inactive' | 'invited' | 'password_reset' | 'active'
  period: DateRange
  wellness: WellnessEntry | null
  last_workout: LastWorkout | null
  load: Load
  onelap: Connection
  alerts: Advisory[]
}

export interface TeamOverview {
  total: number
  active: number
  pending: number
  filled: number
  alerted: number
  groups: string[]
  rows: TeamRow[]
}

export interface WorkoutPoint {
  id: number
  date: string
  started_at: number
  timer_seconds: number | null
  elapsed_seconds: number | null
  distance_m: number | null
  avg_power: number | null
  np: number | null
  tss: number | null
  rpe: number | null
  rpe_load: number | null
  reasons: Record<string, string>
}

export interface ZoneBand { name: string; minimum: number; maximum: number | null; seconds: number }
export interface ZoneDistribution {
  bands: ZoneBand[]
  outside_seconds: number | null
  unknown_seconds: number | null
  workouts_with_data: number
  workouts: number
  omitted: Record<string, number>
}

export interface AthleteAnalytics {
  period: DateRange
  timezone: string
  load: Load
  wellness: { date: string; scores: WellnessScores | null; baseline: WellnessScores | null }[]
  workouts: WorkoutPoint[]
  weeks: (Load & DateRange)[]
  ftp: { date: string; watts: number; effective_from: string }[]
  power_zones: ZoneDistribution
  heart_rate_zones: ZoneDistribution
}

export interface SamplePoint { second: number; value: number | null }
export type WorkoutSeries = Record<'power' | 'heart_rate' | 'cadence', SamplePoint[]>
