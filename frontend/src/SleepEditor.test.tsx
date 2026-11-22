import '@testing-library/jest-dom/vitest'
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClientProvider } from '@tanstack/react-query'
import { createMemoryRouter, RouterProvider } from 'react-router'
import { afterEach, expect, it, vi } from 'vitest'

import { SleepEditor } from './SleepEditor'
import SleepDiary from './SleepDiary'
import type { SleepDay } from './sleepApi'
import { sleepPreview } from './sleepTime'
import { makeQueryClient, sessionKey } from './session'

const date = '2026-11-18'
const day: SleepDay = {
  date, timezone: 'Europe/Moscow', version: 3, updated_at: null, duration_minutes: 420, source: 'legacy',
  selected_source: null, conflict: false, manually_corrected: false, candidates: { legacy: 420 },
  start_time: null, end_time: null, start_offset_minutes: null, end_offset_minutes: null,
  entered_duration_minutes: null, calculated_duration_minutes: null, sleep_quality: null, recovery: null, has_diary: false,
}

function setup(response: () => Response, record = day) {
  const refresh = vi.fn()
  const call = vi.fn((_path: string, _options: RequestInit) => Promise.resolve(response()))
  vi.stubGlobal('fetch', call)
  const client = makeQueryClient()
  client.setQueryData(sessionKey, {
    user: { id: 17, role: 'athlete', name: 'Спортсмен', email: 'athlete@example.com' }, csrf_token: 'fixture-csrf',
  })
  render(<QueryClientProvider client={client}>
    <SleepEditor day={record} base="/athletes/17/sleep" today="2026-11-22" onDirty={() => {}} onMoved={() => {}}
      refresh={refresh} refreshError={null} />
  </QueryClientProvider>)
  return { call, refresh }
}

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

it('рассчитывает ночь по часовому поясу и явно обрабатывает перевод часов', () => {
  expect(sleepPreview(date, 'Asia/Kathmandu', '23:15', '07:00', null, null)).toEqual({
    minutes: 465, startOffsets: [345], endOffsets: [345],
  })
  expect(sleepPreview('2026-03-29', 'Europe/Berlin', '23:30', '07:30', null, null).minutes).toBe(420)
  expect(sleepPreview('2026-03-29', 'Europe/Berlin', '00:30', '02:30', null, null).endOffsets).toEqual([])
  expect(sleepPreview('2026-10-25', 'Europe/Berlin', '23:30', '02:30', null, null)).toEqual({
    minutes: null, startOffsets: [120], endOffsets: [60, 120],
  })
  expect(sleepPreview('2026-10-25', 'Europe/Berlin', '23:30', '02:30', null, 60).minutes).toBe(240)
})

it('сохраняет введённые поля при конфликте версии и перечитывает запись', async () => {
  const { call, refresh } = setup(() => Response.json({ detail: 'sleep_version_conflict' }, { status: 409 }))
  fireEvent.change(screen.getByLabelText('Время засыпания'), { target: { value: '23:15' } })
  fireEvent.change(screen.getByLabelText('Время пробуждения'), { target: { value: '07:00' } })
  expect(screen.getByText('По времени: 7 ч 45 мин')).toBeVisible()
  fireEvent.change(screen.getByLabelText('Длительность вручную, мин'), { target: { value: '0' } })
  fireEvent.click(screen.getByRole('button', { name: 'Сохранить сон' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('Запись изменилась на сервере')
  expect(screen.getByLabelText('Длительность вручную, мин')).toHaveValue(0)
  expect(refresh).toHaveBeenCalledOnce()
  const [path, options] = call.mock.calls[0]
  expect(path).toBe(`/api/v1/athletes/17/sleep/${date}/manual`)
  expect(options.method).toBe('PATCH')
  expect(new Headers(options.headers).get('X-CSRF-Token')).toBe('fixture-csrf')
  expect(JSON.parse(String(options.body))).toEqual({ version: 3, start_time: '23:15', end_time: '07:00', duration_minutes: 0 })
})

it('показывает оба варианта и отправляет только явный выбор источника', async () => {
  const record = { ...day, duration_minutes: 395, source: 'manual' as const, conflict: true, candidates: { manual: 395, legacy: 420 } }
  const { call } = setup(() => Response.json({ ...record, duration_minutes: 420, source: 'legacy' }), record)
  const sources = screen.getByRole('group', { name: 'Значение для аналитики' })
  expect(within(sources).getByRole('button', { name: /Введено вручную/u })).toHaveAttribute('aria-pressed', 'true')
  fireEvent.click(within(sources).getByRole('button', { name: /Историческая запись/u }))
  expect(await within(sources).findByRole('status')).toHaveTextContent('Изменения сохранены')
  expect(call.mock.calls[0][0]).toBe(`/api/v1/athletes/17/sleep/${date}/selection`)
  expect(JSON.parse(String(call.mock.calls[0][1].body))).toEqual({ version: 3, source: 'legacy' })
})

it('сохраняет открытую дату и черновик при обновлении местной даты с сервера', async () => {
  const nextDate = '2026-11-19'
  const history = { timezone: day.timezone, local_date: date, entries: [], has_more: false }
  vi.stubGlobal('fetch', vi.fn(async (path: string) => {
    if (path.endsWith('/sleep?offset=0')) return Response.json(history)
    if (path.includes('/sleep-reports?')) return Response.json({ entries: [], has_more: false })
    if (path.endsWith('/auth/me')) return Response.json({ user: { id: 17, role: 'athlete' }, csrf_token: 'test-csrf' })
    if (path.includes('/training-intelligence/')) return Response.json({ recovery: [] })
    return Response.json({ ...day, date: path.endsWith(nextDate) ? nextDate : date })
  }))
  const client = makeQueryClient()
  const router = createMemoryRouter([{ path: '/diary/sleep', element: <SleepDiary athleteId={17} /> }], {
    initialEntries: ['/diary/sleep'],
  })
  render(<QueryClientProvider client={client}><RouterProvider router={router} /></QueryClientProvider>)
  const bedtime = await screen.findByLabelText('Время засыпания')
  fireEvent.change(bedtime, { target: { value: '23:30' } })
  act(() => { client.setQueryData(['athlete-data', '/athletes/17/sleep?offset=0'], { ...history, local_date: nextDate }) })
  await waitFor(() => expect(screen.getByLabelText('Дата пробуждения')).toHaveAttribute('max', nextDate))
  expect(screen.getByLabelText('Дата пробуждения')).toHaveValue(date)
  expect(screen.getByLabelText('Время засыпания')).toHaveValue('23:30')
})
