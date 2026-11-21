import '@testing-library/jest-dom/vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router'
import { afterEach, expect, it, vi } from 'vitest'

import { App } from './App'
import type { Role } from './api'
import { makeQueryClient } from './session'
import type { AnalyticsWarning, AthleteWarnings, EffortPoint, LoadWeek, Measurements, TeamTraining } from './trainingApi'

vi.mock('./Charts', () => ({
  TrendChart: ({ title, unit }: { title: string; unit: string }) => <div role="group" aria-label={title}>{unit}</div>,
}))

const period = { start: '2026-04-01', end: '2026-04-07' }
const day = '2026-04-06'
const coverage = { days: 7, known_days: 6, confirmed_days: 4, workouts: 5, workouts_with_tss: 5 }
const week: LoadWeek = {
  period, coverage, recorded_tss: 130, comparable_tss: 130, baseline_tss: 100, delta_pct: 30, rest_days: 1, status: 'partial',
}
const titles: [AnalyticsWarning['type'], string][] = [
  ['possible_accumulated_fatigue', 'Возможное накопление усталости'],
  ['load_growth_with_worsening_recovery', 'Рост нагрузки на фоне ухудшения восстановления'],
  ['possible_performance_decline', 'Возможное снижение работоспособности'],
  ['positive_adaptation_signs', 'Признаки положительной адаптации'],
  ['insufficient_data', 'Недостаточность данных'],
  ['recovery_after_load_block', 'Восстановление после нагрузочного блока'],
]
const warnings: AnalyticsWarning[] = titles.map(([type, title], index) => ({
  type, title, id: '8:' + index, episode_id: '8:load:2026-04-03', related_warning_ids: [],
  attention: index === 0 ? 'high' : 'informational', period, protocol: null, duration_seconds: null,
  explanation: 'Наблюдаемое изменение относительно индивидуальной нормы.',
  limitations: ['Наблюдение не устанавливает причинную связь.'],
  coverage: {
    status: 'partial', load: coverage, load_status: 'partial', load_reference_blocks: [],
    evaluated_recovery_dates: [day], worsened_recovery_dates: [day], diary_period: null, diary_filled_days: null,
    missing_diary_dates: [], gaps: [], performance_baseline_attempts: null, performance_recent_attempts: null,
  },
  workouts: [{ workout_id: 17, date: day, role: 'load' }],
  facts: [{
    metric: 'weekly_tss', unit: 'tss', value: 130, baseline: 100, baseline_kind: 'individual', delta: 30, delta_pct: 30,
    date: day, period, reference_period: null, observations: 3, workout_id: 17, load_date: null, lag_days: null,
  }],
}))
const snapshot: AthleteWarnings = {
  athlete_id: 8, completed_through: day, timezone: 'Europe/Moscow', current_week: week, performance_status: 'partial',
  recovery: { evaluated_dates: [day], deterioration_dates: [], sustained: null }, max_attention: 'high', warnings,
}
const team: TeamTraining = { athletes: [
  { ...snapshot, name: 'Ярослава Велосипедова', group: 'Шоссе', active: true, warnings: warnings.slice(0, 1) },
  {
    ...snapshot, athlete_id: 9, name: 'Анна Безданных', group: null, active: true, max_attention: 'informational',
    current_week: { ...week, status: 'missing', recorded_tss: null }, warnings: warnings.slice(4, 5),
  },
  {
    ...snapshot, athlete_id: 10, name: 'Борис Спокойный', group: null, active: true, max_attention: null,
    current_week: { ...week, status: 'sufficient' }, warnings: [],
  },
] }
const attempt: EffortPoint = {
  workout_id: 17, date: day, protocol: 'erg-60', start_seconds: 10, duration_seconds: 60, mean_watts: 200,
  status: 'sufficient', reason: null, baseline_watts: 210, delta_pct: -4.76, baseline_workout_ids: [11, 13, 15],
}
const measurements: Measurements = {
  period, timezone: snapshot.timezone, completed_through: day, current_week: week, rules: { atl_days: 7, ctl_days: 42 },
  load: [{
    date: day, day_status: 'imported', coverage, preliminary: false, tss: 40, recorded_tss: 40, baseline: 32,
    atl: 32, ctl: 30, tsb: -2, atl_status: 'partial', ctl_status: 'insufficient', tsb_status: 'insufficient',
    atl_coverage: coverage, ctl_coverage: coverage,
  }],
  workouts: [{
    workout_id: 17, date: day, started_at: 1775480400, timer_seconds: 120, tss: 40, intense: true,
    duplicate_candidates: [], reasons: {},
  }],
  recovery: [{
    date: day, has_entry: true,
    metrics: { sleep_minutes: { value: 420, baseline: 465, observations: 8, delta: -45, status: 'sufficient', deteriorated: true } },
  }],
  performance: {
    status: 'partial', ftp: [{ date: day, watts: 250, effective_from: day }],
    efforts: [attempt], repeated_changes: [], planned_intensity: 'unsupported',
  },
  unsupported_recovery: ['hrv'],
  episodes: [{
    period, elevated_dates: ['2026-04-05'], workout_ids: [17], reaction_window_end: '2026-04-12', open: true,
    normalization_dates: [], reactions: [{
      date: day, metric: 'sleep_minutes', value: 420, baseline: 465, delta: -45,
      workout_id: null, lag_days: 1, load_date: '2026-04-05', same_date_only: false,
    }],
  }],
  correlations: [],
}
const workout = {
  id: 17, athlete_id: 8, started_at: 1775480400, timezone: snapshot.timezone, original_available: true,
  timer_seconds: 120, tss: 40, np: 200, rpe: null, sources: [{ source: 'manual' }],
  summary: { elapsed_seconds: 120, sample_count: 120 },
  metrics: {
    snapshot: { ftp_watts: 250, ftp_effective_from: day },
    result: { reasons: {}, if_value: .8, quality: { expected_seconds: 120, covered_seconds: { power: 120 }, missing_channels: [] } },
  },
}

function json(value: unknown, status = 200) {
  return new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } })
}

function open(path: string, role: Role, handler: (url: string, options: RequestInit) => Response) {
  const fetch = vi.fn(async (url: string, options: RequestInit) => url === '/api/v1/auth/me'
    ? json({ user: { id: role === 'coach' ? 1 : 8, role, name: 'Тестовая учётная запись' }, csrf_token: 'training-csrf' })
    : handler(url, options))
  vi.stubGlobal('fetch', fetch)
  render(<QueryClientProvider client={makeQueryClient()}>
    <MemoryRouter initialEntries={[path]}><App /></MemoryRouter>
  </QueryClientProvider>)
  return fetch
}

function getCoachData(url: string) {
  if (url === '/api/v1/training-intelligence/team') return json(team)
  if (url === '/api/v1/athletes/8') return json({ athlete_id: 8, name: team.athletes[0].name })
  if (url.includes('/athletes/8/measurements?period=')) return json(measurements)
  if (url.endsWith('/athletes/8/warnings')) return json(snapshot)
  return json({ detail: 'workout_missing' }, 404)
}

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

it('отличает недостаточные данные от отсутствия предупреждений и фильтрует команду', async () => {
  open('/training-intelligence', 'coach', getCoachData)
  expect(await screen.findByRole('heading', { name: 'Нагрузка и восстановление' })).toBeVisible()
  await screen.findByRole('heading', { name: 'Ярослава Велосипедова' })
  const names = () => screen.getAllByRole('heading', { level: 2 }).map(item => item.textContent)
  expect(names()).toEqual(['Ярослава Велосипедова', 'Анна Безданных', 'Борис Спокойный'])
  fireEvent.change(screen.getByLabelText('Сортировка'), { target: { value: 'low-first' } })
  expect(names()).toEqual(['Борис Спокойный', 'Анна Безданных', 'Ярослава Велосипедова'])
  fireEvent.change(screen.getByLabelText('Достаточность данных'), { target: { value: 'insufficient' } })
  expect(names()).toEqual(['Анна Безданных'])
  expect(screen.getByText('Недостаточно данных', { selector: 'span' })).toBeVisible()
  fireEvent.change(screen.getByLabelText('Уровень внимания'), { target: { value: 'none' } })
  expect(screen.getByText('По выбранным фильтрам никого нет.')).toBeVisible()
  fireEvent.change(screen.getByLabelText('Достаточность данных'), { target: { value: 'all' } })
  expect(names()).toEqual(['Борис Спокойный'])
  expect(screen.getByText('Нет предупреждений', { selector: 'span' })).toBeVisible()
})

it('открывает спортсмена, запрашивает период 42 дня и показывает четыре вкладки с единицами измерения', async () => {
  const fetch = open('/training-intelligence', 'coach', getCoachData)
  fireEvent.click(await screen.findByRole('link', { name: 'Ярослава Велосипедова' }))
  expect(await screen.findByRole('heading', { name: 'Дневной TSS' })).toBeVisible()
  expect(screen.getByRole('group', { name: 'Дневной TSS' })).toHaveTextContent('TSS')
  for (const name of ['ATL', 'CTL', 'TSB']) expect(screen.getByRole('group', { name })).toHaveTextContent('TSS/день')
  fireEvent.click(screen.getByRole('button', { name: '42 дня' }))
  await waitFor(() => expect(fetch.mock.calls.some(([url]) => url.endsWith('measurements?period=42'))).toBe(true))
  expect(screen.getByRole('button', { name: '42 дня' })).toHaveAttribute('aria-pressed', 'true')
  const tabs = screen.getByRole('navigation', { name: 'Вкладки нагрузки и восстановления' })
  fireEvent.click(within(tabs).getByRole('link', { name: 'Восстановление' }))
  expect(await screen.findByRole('group', { name: 'Продолжительность сна' })).toHaveTextContent('мин')
  fireEvent.change(screen.getByLabelText('Показатель восстановления'), { target: { value: 'general_fatigue' } })
  expect(screen.getByRole('combobox', { name: 'Показатель восстановления' })).toHaveValue('general_fatigue')
  fireEvent.click(within(tabs).getByRole('link', { name: 'Работоспособность' }))
  expect(screen.getByRole('group', { name: 'FTP' })).toHaveTextContent('Вт')
  expect(screen.getByRole('heading', { name: 'erg-60 · 60 с' })).toBeVisible()
  expect(screen.getByRole('cell', { name: /-4,76 %/u })).toBeVisible()
  fireEvent.click(within(tabs).getByRole('link', { name: 'Взаимосвязи' }))
  expect(screen.getByRole('heading', { name: 'Временная шкала' })).toBeVisible()
  expect(screen.getByText(/задержка 1 дн\./u)).toBeVisible()
})

it('раскрывает все типы предупреждений с фактами, базой, датой и ограничениями', async () => {
  open('/athletes/8/load-recovery?tab=relationships', 'coach', getCoachData)
  expect(await screen.findByRole('heading', { name: 'Активные предупреждения' })).toBeVisible()
  for (const [, title] of titles) {
    const summary = screen.getByText(title, { selector: 'summary' })
    const details = summary.closest('details')!
    expect(within(details).getByText('Фактические значения')).not.toBeVisible()
    fireEvent.click(summary)
    expect(within(details).getByRole('cell', { name: '130 TSS' })).toBeVisible()
    expect(within(details).getByRole('cell', { name: /100 TSS.*Индивидуальная база/u })).toBeVisible()
    expect(within(details).getByRole('cell', { name: /\+30 TSS.*\+30 %/u })).toBeVisible()
    expect(within(details).getByText('6 апреля 2026 г.')).toBeVisible()
    expect(within(details).getByText(/Дней с известным TSS 6 из 7/u)).toBeVisible()
    expect(within(details).getByText('Наблюдение не устанавливает причинную связь.')).toBeVisible()
    fireEvent.click(summary)
  }
})

it('размечает контрольный отрезок с CSRF и сохраняет только после серверной проверки', async () => {
  let saved: typeof attempt | null = null
  let tries = 0
  const fetch = open('/workouts/17', 'coach', (url, options) => {
    if (url === '/api/v1/workouts/17') return json(workout)
    if (url.endsWith('/series')) return json({ power: [{ second: 10, value: 200 }], heart_rate: [], cadence: [] })
    if (url.endsWith('/effort')) {
      if (options.method === 'PUT') {
        tries += 1
        if (tries === 1) return json({ detail: 'incomplete_effort_power' }, 422)
        expect(new Headers(options.headers).get('X-CSRF-Token')).toBe('training-csrf')
        saved = { ...attempt, ...JSON.parse(String(options.body)) }
      }
      return json(saved)
    }
    return json(null)
  })
  const form = await screen.findByRole('form', { name: 'Разметка контрольного отрезка' })
  fireEvent.change(within(form).getByLabelText('Код протокола'), { target: { value: 'erg-60' } })
  fireEvent.change(within(form).getByLabelText('Начало от старта FIT, с'), { target: { value: '10' } })
  fireEvent.change(within(form).getByLabelText('Длительность отрезка, с'), { target: { value: '120' } })
  const submit = within(form).getByRole('button', { name: 'Проверить и сохранить отрезок' })
  expect(submit).toBeDisabled()
  expect(within(form).getByRole('alert')).toHaveTextContent('Отрезок выходит за границы записи FIT.')
  fireEvent.change(within(form).getByLabelText('Длительность отрезка, с'), { target: { value: '60' } })
  fireEvent.click(submit)
  expect(await within(form).findByRole('alert')).toHaveTextContent('Сервер не смог проверить мощность')
  expect(within(form).queryByText('Изменения сохранены.')).toBeNull()
  fireEvent.click(submit)
  expect(await screen.findByText('Средняя мощность: 200 Вт')).toBeVisible()
  expect(fetch.mock.calls.filter(([url, options]) => url.endsWith('/effort') && options.method === 'PUT')).toHaveLength(2)
  expect(saved).toMatchObject({ protocol: 'erg-60', start_seconds: 10, duration_seconds: 60 })
})

it('спортсмен запрашивает только свою сводку и переходит к пропущенной анкете', async () => {
  const fetch = open('/my-load-recovery?athlete_id=9', 'athlete', url => {
    if (url.endsWith('/training-intelligence/me')) return json({
      athlete_id: 8, timezone: snapshot.timezone, completed_through: day, load: week,
      recovery: measurements.recovery, missing_diary_dates: [day],
    })
    if (url === '/api/v1/athletes/8/wellness?offset=0') return json({
      timezone: snapshot.timezone, local_date: period.end, today_entry: null, entries: [], has_more: false,
    })
    return json(null)
  })
  expect(await screen.findByRole('heading', { name: 'Моя нагрузка и восстановление' })).toBeVisible()
  expect(await screen.findByText(/Отклонение от вашей обычной недельной нагрузки: \+30 %/u)).toBeVisible()
  expect(screen.queryByRole('link', { name: 'Нагрузка и восстановление' })).toBeNull()
  fireEvent.click(screen.getByRole('link', { name: /Заполнить за 6 апреля/u }))
  expect(await screen.findByRole('form', { name: 'Утренняя анкета' })).toBeVisible()
  expect(fetch.mock.calls.map(([url]) => url)).toContain('/api/v1/athletes/8/wellness/' + day)
  expect(fetch.mock.calls.some(([url]) => url.includes('/athletes/9'))).toBe(false)
})

it.each([
  ['/training-intelligence', 'athlete'],
  ['/athletes/8/load-recovery', 'athlete'],
  ['/my-load-recovery', 'coach'],
] as const)('не открывает %s для роли %s', async (path, role) => {
  const fetch = open(path, role, getCoachData)
  expect(await screen.findByRole('heading', { name: 'Доступ ограничен' })).toBeVisible()
  expect(fetch.mock.calls.map(([url]) => url)).toEqual(['/api/v1/auth/me'])
})

it('не запрашивает разметку FIT для спортсмена', async () => {
  const fetch = open('/workouts/17', 'athlete', () => json(workout))
  expect(await screen.findByRole('heading', { name: 'Сводка FIT' })).toBeVisible()
  expect(screen.queryByRole('form', { name: 'Разметка контрольного отрезка' })).toBeNull()
  expect(fetch.mock.calls.some(([url]) => url.includes('/training-intelligence'))).toBe(false)
})
