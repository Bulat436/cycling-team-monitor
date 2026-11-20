import '@testing-library/jest-dom/vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router'
import { afterEach, expect, it, vi } from 'vitest'

import { AlertSettingsPage } from './AlertSettings'
import { makeQueryClient, sessionKey } from './session'
import { Team } from './Team'

function open(page: React.ReactNode, handler: (path: string, options?: RequestInit) => unknown) {
  const fetch = vi.fn<typeof globalThis.fetch>(async (input, options) =>
    new Response(JSON.stringify(handler(String(input), options)), { status: 200 }),
  )
  vi.stubGlobal('fetch', fetch)
  const client = makeQueryClient()
  client.setQueryData(sessionKey, { user: { id: 1, role: 'coach', name: 'Тренер' }, csrf_token: 'fixture-csrf' })
  render(<QueryClientProvider client={client}><MemoryRouter>{page}</MemoryRouter></QueryClientProvider>)
  return fetch
}

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

it('передаёт период, фильтры и сортировку команде через API', async () => {
  const fetch = open(<Team />, () => ({ total: 2, active: 2, pending: 0, filled: 1, alerted: 1, groups: ['Трек'], rows: [] }))
  expect(await screen.findByText('По выбранным фильтрам никого нет.')).toBeVisible()
  fireEvent.change(screen.getByLabelText('Поиск по имени'), { target: { value: 'Рита' } })
  fireEvent.change(screen.getByLabelText('Группа'), { target: { value: 'Трек' } })
  fireEvent.change(screen.getByLabelText('Дневник'), { target: { value: 'missing' } })
  fireEvent.change(screen.getByLabelText('Сортировка'), { target: { value: 'tss' } })
  fireEvent.click(screen.getByRole('button', { name: '90 дней' }))
  await waitFor(() => {
    const [url] = fetch.mock.calls.at(-1)!
    const params = new URL(String(url), 'https://example.com').searchParams
    expect(Object.fromEntries(params)).toMatchObject({ period: '90', search: 'Рита', group: 'Трек', diary: 'missing', sort: 'tss' })
  })
  fireEvent.click(screen.getByRole('button', { name: 'Свой период' }))
  fireEvent.change(screen.getByLabelText('Начало периода'), { target: { value: '2026-08-01' } })
  fireEvent.change(screen.getByLabelText('Конец периода'), { target: { value: '2026-08-06' } })
  fireEvent.click(screen.getByRole('button', { name: 'Применить период' }))
  await waitFor(() => expect(String(fetch.mock.calls.at(-1)?.[0])).toContain('period=custom&start=2026-08-01&end=2026-08-06'))
})

it('сохраняет пороги и выключатели с CSRF', async () => {
  const settings = {
    enabled: ['fatigue', 'illness'], diary_due_hour: 10, fatigue_min: 4, soreness_min: 4, motivation_max: 2,
    rpe_grace_hours: 24, rpe_lookback_days: 14, inactive_days: 7,
  }
  const calls = open(<AlertSettingsPage />, () => settings)
  fireEvent.change(await screen.findByLabelText('Усталость от'), { target: { value: '5' } })
  fireEvent.click(screen.getByLabelText('Отмечены симптомы'))
  fireEvent.click(screen.getByRole('button', { name: 'Сохранить настройки' }))
  expect(await screen.findByText('Изменения сохранены.')).toBeVisible()
  const [, options] = calls.mock.calls.find(([, request]) => request?.method === 'PUT')!
  expect(new Headers(options?.headers).get('X-CSRF-Token')).toBe('fixture-csrf')
  expect(JSON.parse(String(options?.body))).toMatchObject({ fatigue_min: 5, enabled: ['fatigue'] })
})
