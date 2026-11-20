import { afterEach, expect, it, vi } from 'vitest'
import { ApiError, request } from './api'

afterEach(() => vi.unstubAllGlobals())

it('передаёт FIT двоичным телом с именем файла и CSRF', async () => {
  const fit = new File([Uint8Array.of(1, 2, 3)], 'Заезд.FIT')
  const fetch = vi.fn<typeof globalThis.fetch>(async () => new Response(JSON.stringify({ workout_id: 12 }), { status: 200 }))
  vi.stubGlobal('fetch', fetch)
  await expect(request('/workouts/import', { method: 'POST', csrf: 'csrf-import', body: fit }))
    .resolves.toEqual({ workout_id: 12 })
  expect(fetch).toHaveBeenCalledExactlyOnceWith('/api/v1/workouts/import', expect.objectContaining({
    method: 'POST', body: fit, credentials: 'same-origin',
  }))
  const headers = new Headers(fetch.mock.calls[0][1]?.headers)
  expect(headers.get('Content-Type')).toBe('application/octet-stream')
  expect(headers.get('X-FIT-Filename')).toBe(encodeURIComponent(fit.name))
  expect(headers.get('X-CSRF-Token')).toBe('csrf-import')
})

it('показывает понятную ошибку проверки FIT', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ detail: 'fit_bad_crc' }), { status: 422 })))
  await expect(request('/workouts/import', { method: 'POST' })).rejects.toEqual(
    new ApiError(422, 'fit_bad_crc'),
  )
})
