import '@testing-library/jest-dom/vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { App } from './App'
import type { Role } from './api'
import { makeQueryClient } from './session'

const info = (role: Role) => ({
  user: { id: 1, name: 'Саша Велосипедов', email: 'rider@example.com', role },
  csrf_token: 'csrf',
})

function json(value: unknown, status = 200) {
  return new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } })
}

function open(path: string, fetch: (url: string, options: RequestInit) => Response) {
  vi.stubGlobal('fetch', vi.fn(async (url: string, options: RequestInit) => fetch(url, options)))
  return render(
    <QueryClientProvider client={makeQueryClient()}>
      <MemoryRouter initialEntries={[path]}><App /></MemoryRouter>
    </QueryClientProvider>,
  )
}

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('вход и маршруты по роли', () => {
  it('перенаправляет гостя на вход и объясняет ошибку', async () => {
    open('/access', url => json({ detail: url.endsWith('/me') ? 'authentication_required' : 'invalid_credentials' }, 401))
    expect(await screen.findByRole('heading', { name: 'Вход в команду' })).toBeVisible()
    fireEvent.change(screen.getByLabelText('Электронная почта'), { target: { value: 'user@example.com' } })
    fireEvent.change(screen.getByLabelText('Пароль'), { target: { value: crypto.randomUUID() } })
    fireEvent.click(screen.getByRole('button', { name: 'Войти' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Проверьте почту и пароль.')
  })

  it('закрывает административный маршрут спортсмену', async () => {
    const fetch = vi.fn(() => json(info('athlete')))
    open('/access', fetch)
    expect(await screen.findByRole('heading', { name: 'Доступ ограничен' })).toBeVisible()
    expect(screen.queryByRole('link', { name: 'Управление доступом' })).toBeNull()
    expect(fetch).toHaveBeenCalledTimes(1)
    fireEvent.click(screen.getByRole('link', { name: 'На главную' }))
    expect(await screen.findByRole('heading', { name: 'Утренний дневник' })).toBeVisible()
  })

  it('показывает состав тренеру и передаёт CSRF при выходе', async () => {
    const fetch = vi.fn((url: string, options: RequestInit) => {
            if (url.startsWith('/api/v1/analytics/team?')) return json({ total: 0, active: 0, pending: 0, filled: 0, alerted: 0, groups: [], rows: [] })
      if (url.endsWith('/logout')) {
        expect(new Headers(options.headers).get('X-CSRF-Token')).toBe('csrf')
        return new Response(null, { status: 204 })
      }
      return json(info('coach'))
    })
    open('/', fetch)
    expect(await screen.findByRole('heading', { name: 'Спортсмены команды' })).toBeVisible()
    fireEvent.click(screen.getByRole('button', { name: 'Выйти' }))
    await waitFor(() => expect(screen.getByRole('heading', { name: 'Вход в команду' })).toBeVisible())
  })

  it('после подтверждения удаляет деактивированный аккаунт от имени главного тренера', async () => {
    let deleted = false
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true)
    try {
      open('/access', (url, options) => {
        if (url.endsWith('/auth/me')) {
          const session = info('coach')
          return json({ ...session, user: { ...session.user, is_head_coach: true } })
        }
        if (url.endsWith('/admin/users/2') && options.method === 'DELETE') {
          expect(new Headers(options.headers).get('X-CSRF-Token')).toBe('csrf')
          deleted = true
          return new Response(null, { status: 204 })
        }
        if (url.endsWith('/admin/users')) return json(deleted ? [] : [
          { id: 2, email: 'archived@example.com', name: 'Деактивированный спортсмен', role: 'athlete',
            active: false, activated_at: 1, password_pending: false },
        ])
        return json([])
      })
      fireEvent.click(await screen.findByRole('button', { name: 'Удалить аккаунт' }))
      expect(confirm).toHaveBeenCalledOnce()
      expect(await screen.findByText('Аккаунт удалён.')).toBeVisible()
      await waitFor(() => expect(screen.queryByText('Деактивированный спортсмен')).toBeNull())
    } finally {
      confirm.mockRestore()
    }
  })
})
