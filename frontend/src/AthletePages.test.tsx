import '@testing-library/jest-dom/vitest'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router'
import { afterEach, expect, it, vi } from 'vitest'

import { makeQueryClient, sessionKey } from './session'
import { DiaryPage } from './Wellness'
import { WorkoutPage } from './Workouts'

const session = {
  user: { id: 17, role: 'athlete', name: 'Спортсмен', email: 'athlete@example.com' },
  csrf_token: 'fixture-csrf',
}
const day = '2026-03-12'
const tz = 'Pacific/Honolulu'
const athletePath = '/api/v1/athletes/17'

function setup(element: React.ReactNode, handler: (url: string, options: RequestInit) => unknown) {
  vi.stubGlobal('fetch', vi.fn(async (url: string, options: RequestInit) => {
    const body = url.endsWith('/auth/me') ? session : handler(url, options)
    return new Response(JSON.stringify(body), { headers: { 'Content-Type': 'application/json' } })
  }))
  const client = makeQueryClient()
  client.setQueryData(sessionKey, session)
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>{element}</MemoryRouter>
    </QueryClientProvider>,
  )
}

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

it('использует дату спортсмена и отправляет все поля дневника', async () => {
  let completed: Record<string, unknown> | null = null
  const save = vi.fn()
  setup(<DiaryPage athleteId={17} />, (url, options) => {
    if (url === athletePath + '/wellness/' + day) {
      if (options.method === 'PUT') {
        save(new Headers(options.headers).get('X-CSRF-Token'), JSON.parse(String(options.body)))
        completed = {
          ...JSON.parse(String(options.body)), entry_date: day, timezone: tz,
          created_at: 1773340000, updated_at: 1773340000,
        }
      }
      return completed
    }
    return { timezone: tz, local_date: day, today_entry: completed, entries: completed ? [completed] : [], has_more: false }
  })
  const form = await screen.findByRole('form', { name: 'Утренняя анкета' })
  expect(screen.getByLabelText('Дата анкеты')).toHaveValue(day)
  expect(within(form).getAllByText('1 — очень плохо, 5 — отлично')).toHaveLength(3)
  expect(within(form).getAllByText('1 — минимальная, 5 — очень высокая')).toHaveLength(3)
  for (const name of ['Качество сна', 'Общая усталость', 'Усталость ног', 'Мотивация', 'Настроение', 'Мышечная болезненность']) {
    const group = within(form).getByRole('group', { name })
    fireEvent.click(within(group).getByRole('radio', { name: '4' }))
  }
  fireEvent.click(within(form).getByRole('radio', { name: 'Нет' }))
  fireEvent.change(within(form).getByLabelText('Комментарий (необязательно)'), { target: { value: 'Запись' } })
  fireEvent.click(within(form).getByRole('button', { name: 'Сохранить дневник' }))
  expect(await screen.findByText('Сегодня заполнен')).toBeVisible()
  expect(save).toHaveBeenCalledWith('fixture-csrf', {
    sleep_quality: 4, general_fatigue: 4, leg_fatigue: 4, motivation: 4, mood: 4,
    muscle_soreness: 4, illness_symptoms: false, comment: 'Запись',
  })
})

it('отправляет RPE 0 по идентификатору тренировки и снимает напоминание', async () => {
  let rpe: Record<string, unknown> | null = null
  const save = vi.fn()
  setup(<WorkoutPage athleteId={17} />, (url, options) => {
    if (url === '/api/v1/workouts/53/rpe') {
      save(new Headers(options.headers).get('X-CSRF-Token'), JSON.parse(String(options.body)))
      rpe = { score: 0, session_load: 0, created_at: 1773340000, updated_at: 1773340000 }
      return rpe
    }
    return {
      timezone: tz, missing_rpe_count: rpe === null ? 1 : 0, has_more: false,
      workouts: [{ id: 53, athlete_id: 17, started_at: 1773340000, timer_seconds: 3600, rpe }],
    }
  })
  expect(await screen.findByText('Тренировок без RPE — 1')).toBeVisible()
  fireEvent.click(screen.getByRole('button', { name: 'Указать RPE' }))
  fireEvent.change(screen.getByLabelText('Сложность тренировки (RPE)'), { target: { value: '0' } })
  fireEvent.click(screen.getByRole('button', { name: 'Сохранить RPE' }))
  expect(await screen.findByText('0 из 10')).toBeVisible()
  expect(screen.queryByText('Тренировок без RPE — 1')).toBeNull()
  expect(save).toHaveBeenCalledWith('fixture-csrf', { score: 0, comment: null })
})
