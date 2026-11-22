import { dateLabel, diaryFields, numberLabel } from './athleteApi'
import type { DateRange } from './coachApi'

export const trainingApi = '/training-intelligence'
export const trainingTeamPath = '/training-intelligence'
export const ownTrainingPath = '/my-load-recovery'
export const trainingAthletePath = (athleteId: number) => `/athletes/${athleteId}/load-recovery`
export const athleteTrainingApi = (athleteId: number) => `${trainingApi}/athletes/${athleteId}`

export const periodOptions = ['7', '28', '42', '90'] as const
export type TrainingPeriod = typeof periodOptions[number]
export type DataStatus = 'sufficient' | 'partial' | 'insufficient' | 'unsupported' | 'missing'
export type Attention = 'informational' | 'moderate' | 'high'

export const statusLabels: Record<DataStatus, string> = {
  sufficient: 'Данных достаточно',
  partial: 'Данных частично достаточно',
  insufficient: 'Недостаточно данных',
  unsupported: 'Показатель не поддерживается',
  missing: 'Данные отсутствуют',
}
export const attentionLabels: Record<Attention, string> = {
  high: 'Высокий', moderate: 'Умеренный', informational: 'Информационный',
}

export const dayLabels = {
  rest: 'Подтверждённый отдых',
  all_workouts_recorded: 'Все тренировки записаны',
  imported: 'Есть FIT, полнота не подтверждена',
  unknown: 'День не подтверждён',
  conflict: 'Записи противоречат друг другу',
}

export const recoveryMetrics = [
  { name: 'sleep_minutes', label: 'Продолжительность сна' },
  ...diaryFields.slice(0, 3),
  { name: 'recovery', label: 'Самооценка восстановления' },
  ...diaryFields.slice(3),
] as const

const metricLabels: Record<string, string> = {
  ...Object.fromEntries(recoveryMetrics.map(metric => [metric.name, metric.label])),
  stress: 'Стресс', resting_heart_rate: 'Пульс покоя', hrv: 'Вариабельность пульса',
  daily_tss: 'Дневной TSS', weekly_tss: 'Недельный TSS',
  days_with_multiple_worsening_metrics: 'Дни с ухудшением нескольких показателей',
  control_mean_power: 'Средняя мощность отрезка', control_baseline_sample: 'Контрольная попытка из базы',
  filled_diary_days: 'Заполненные дни дневника', known_load_days: 'Дни с известной нагрузкой',
  comparable_load_weeks: 'Сопоставимые недели нагрузки', evaluated_recovery_days: 'Оценённые дни восстановления',
}

export function metricLabel(metric: string) {
  if (metric.startsWith('performance:')) {
    const [, seconds, ...protocol] = metric.split(':')
    return `Мощность · ${protocol.join(':')} · ${seconds} с`
  }
  return metricLabels[metric] ?? metric
}

export function periodLabel(period: DateRange) {
  const start = dateLabel(period.start)
  return period.start === period.end ? start : start + ' — ' + dateLabel(period.end)
}

export function measure(value: number | null | undefined, unit = '') {
  return value == null ? 'Нет данных' : numberLabel(value) + (unit ? ' ' + unit : '')
}

export function signedMeasure(value: number | null | undefined, unit = '') {
  return (value != null && value > 0 ? '+' : '') + measure(value, unit)
}

export function effortClock(seconds: number) {
  return Math.floor(seconds / 60) + ':' + String(seconds % 60).padStart(2, '0')
}

export const effortReasonLabels: Record<string, string> = {
  fit_or_algorithm_changed: 'FIT или алгоритм обновился. Проверьте разметку отрезка.',
  possible_duplicate: 'Возможный дубликат. Попытка исключена из сравнения.',
  too_few_comparable_attempts: 'Недостаточно сопоставимых попыток.',
  zero_power_baseline: 'Базовая мощность равна нулю. Процент недоступен.',
}

export interface Coverage {
  days: number
  known_days: number
  confirmed_days: number
  workouts: number
  workouts_with_tss: number
}

export interface LoadReference {
  period: DateRange
  tss: number
  coverage: Coverage
}

export interface LoadWeek {
  period: DateRange
  coverage: Coverage
  recorded_tss: number | null
  comparable_tss: number | null
  baseline_tss: number | null
  delta_pct: number | null
  rest_days: number
  status: DataStatus
}

export interface LoadDay {
  date: string
  day_status: keyof typeof dayLabels
  coverage: Coverage
  preliminary: boolean
  recorded_tss: number | null
  tss: number | null
  baseline: number | null
  atl: number | null
  ctl: number | null
  tsb: number | null
  atl_coverage: Coverage
  ctl_coverage: Coverage
  atl_status: DataStatus
  ctl_status: DataStatus
  tsb_status: DataStatus
}

export interface RecoveryValue {
  value: number | null
  baseline: number | null
  observations: number
  delta: number | null
  status: DataStatus
  deteriorated: boolean | null
}

export interface RecoveryDay {
  date: string
  has_entry: boolean
  metrics: Record<string, RecoveryValue>
}

export interface RecoveryPattern {
  evaluated_dates: string[]
  deterioration_dates: string[]
  sustained: boolean | null
}

export interface WorkoutLoad {
  workout_id: number
  date: string
  started_at: number
  timer_seconds: number | null
  tss: number | null
  intense: boolean
  duplicate_candidates: number[]
  reasons: Record<string, string>
}

export interface EffortInput { protocol: string; start_seconds: number; duration_seconds: number }

export interface Effort extends EffortInput {
  workout_id: number
  mean_watts: number | null
  status: DataStatus
  reason: string | null
}

export interface EffortPoint extends Effort {
  date: string
  baseline_watts: number | null
  baseline_workout_ids: number[]
  delta_pct: number | null
}

export interface Performance {
  status: DataStatus
  efforts: EffortPoint[]
  repeated_changes: {
    protocol: string
    duration_seconds: number
    direction: 'decreased' | 'improved'
    baseline_watts: number
    baseline_attempts: EffortPoint[]
    workouts: EffortPoint[]
  }[]
  ftp: { date: string; watts: number; effective_from: string }[]
  planned_intensity: 'unsupported'
}

export interface LoadEpisode {
  period: DateRange
  elevated_dates: string[]
  workout_ids: number[]
  reaction_window_end: string
  open: boolean
  normalization_dates: string[]
  reactions: {
    date: string
    metric: string
    value: number
    baseline: number
    delta: number
    workout_id: number | null
    load_date: string
    lag_days: number
    same_date_only: boolean
  }[]
}

export interface Measurements {
  timezone: string
  period: DateRange
  completed_through: string
  rules: { atl_days: number; ctl_days: number; recovery_window_days?: number }
  current_week: LoadWeek
  load: LoadDay[]
  workouts: WorkoutLoad[]
  recovery: RecoveryDay[]
  unsupported_recovery: string[]
  performance: Performance
  episodes: LoadEpisode[]
  correlations: {
    metric: string
    lag_days: number
    pairs: number
    confirmed_load_pairs: number
    coefficient: number | null
    stable: boolean | null
    status: DataStatus
  }[]
}

type FactUnit = 'tss' | 'points' | 'minutes' | 'watts' | 'days' | 'weeks'
export const factUnits: Record<FactUnit, string> = {
  tss: 'TSS', points: 'балл.', minutes: 'мин', watts: 'Вт', days: 'дн.', weeks: 'нед.',
}

export type WarningType = 'possible_accumulated_fatigue' | 'load_growth_with_worsening_recovery'
  | 'possible_performance_decline' | 'positive_adaptation_signs' | 'insufficient_data' | 'recovery_after_load_block'

export interface AnalyticsWarning {
  id: string
  episode_id: string | null
  related_warning_ids: string[]
  type: WarningType
  title: string
  attention: Attention
  period: DateRange
  explanation: string
  limitations: string[]
  protocol: string | null
  duration_seconds: number | null
  facts: {
    metric: string
    unit: FactUnit
    value: number | null
    baseline: number | null
    baseline_kind: 'individual' | 'previous_week' | 'required_minimum' | null
    delta: number | null
    delta_pct: number | null
    period: DateRange | null
    reference_period: DateRange | null
    date: string | null
    observations: number | null
    workout_id: number | null
    load_date: string | null
    lag_days: number | null
  }[]
  workouts: { workout_id: number; date: string; role: 'load' | 'control_attempt' | 'control_baseline' }[]
  coverage: {
    status: DataStatus
    load: Coverage | null
    load_status: DataStatus | null
    load_reference_blocks: LoadReference[]
    evaluated_recovery_dates: string[]
    worsened_recovery_dates: string[]
    diary_period: DateRange | null
    diary_filled_days: number | null
    missing_diary_dates: string[]
    performance_baseline_attempts: number | null
    performance_recent_attempts: number | null
    gaps: string[]
  }
}

export interface AthleteWarnings {
  athlete_id: number
  timezone: string
  completed_through: string
  current_week: LoadWeek
  recovery: RecoveryPattern
  performance_status: DataStatus
  max_attention: Attention | null
  warnings: AnalyticsWarning[]
}

export interface TeamTraining {
  athletes: (AthleteWarnings & { name: string; group: string | null; active: boolean })[]
}

export interface OwnRecovery {
  athlete_id: number
  timezone: string
  completed_through: string
  load: LoadWeek
  recovery: RecoveryDay[]
  missing_diary_dates: string[]
  recovery_window_days?: number
}
