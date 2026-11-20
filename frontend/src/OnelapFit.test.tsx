import '@testing-library/jest-dom/vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router'
import { afterEach, expect, it, vi } from 'vitest'

import { OnelapSection } from './OnelapFit'
import { makeQueryClient, sessionKey } from './session'

function renderSection(coach: boolean, handler: (path: string, options?: RequestInit) => Response) {
  const fetch = vi.fn<typeof globalThis.fetch>(async (input, options) => handler(String(input), options))
  vi.stubGlobal('fetch', fetch)
  const client = makeQueryClient()
  client.setQueryData(sessionKey, {
    user: { id: 1, role: coach ? 'coach' : 'athlete', name: 'Участник' }, csrf_token: 'fixture-csrf',
  })
  render(<QueryClientProvider client={client}>
    <MemoryRouter><OnelapSection athleteId={5} timezone="Europe/Moscow" coach={coach} /></MemoryRouter>
  </QueryClientProvider>)
  return fetch
}

function json(value: unknown, status = 200) {
  return new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } })
}

const base = {
  available: true, validation_pending: true, status: 'not_connected', has_session: false,
  history_days: 90, diagnostic: null, last_success_at: null, next_sync_at: null,
}

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

it('стирает поля после отправки и объясняет запрос подтверждения', async () => {
  const sent = vi.fn()
  renderSection(false, (path, options) => {
    if (path.endsWith('/connect')) {
      sent(JSON.parse(String(options?.body)), new Headers(options?.headers).get('X-CSRF-Token'))
      return json({ detail: 'onelap_interaction_required' }, 409)
    }
    if (path.endsWith('/runs')) return json([])
    return json(base)
  })
  expect(await screen.findByText('Ожидает проверки на реальном аккаунте')).toBeVisible()
  const password = screen.getByLabelText('Пароль OnelapFit')
  const input = crypto.randomUUID()
  fireEvent.change(screen.getByLabelText('Почта или телефон OnelapFit'), { target: { value: 'test@example.com' } })
  fireEvent.change(password, { target: { value: input } })
  fireEvent.click(screen.getByRole('button', { name: 'Подключить OnelapFit' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('Завершите подтверждение')
  expect(password).toHaveValue('')
  expect(sent).toHaveBeenCalledWith({ login: 'test@example.com', password: input, history_days: 90 }, 'fixture-csrf')
})

it('тренер видит статус и может запустить синхронизацию спортсмена', async () => {
  const calls = renderSection(true, path => {
    if (path.endsWith('/sync')) return json({ run_id: 10 }, 202)
    if (path.endsWith('/runs')) return json([])
    return json({ ...base, status: 'connected', has_session: true })
  })
  fireEvent.click(await screen.findByRole('button', { name: 'Синхронизировать сейчас' }))
  await waitFor(() => expect(screen.getByText('Запуск добавлен в очередь.')).toBeVisible())
  expect(calls).toHaveBeenCalledWith('/api/v1/athletes/5/onelapfit/sync', expect.objectContaining({ method: 'POST' }))
  expect(screen.queryByLabelText('Пароль OnelapFit')).toBeNull()
  expect(screen.queryByRole('button', { name: 'Отключить OnelapFit' })).toBeNull()
})
