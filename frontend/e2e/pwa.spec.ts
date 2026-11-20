import { createServer } from 'node:http'
import { readFile } from 'node:fs/promises'
import { resolve } from 'node:path'
import type { AddressInfo } from 'node:net'
import { expect, test } from '@playwright/test'

test('production PWA, иконки и кеш без персональных данных', async ({ page, context, request }) => {
  await page.goto('/')
  await expect(page.getByRole('heading', { name: 'Вход в команду' })).toBeVisible()
  await expect.poll(() => page.evaluate(() => navigator.serviceWorker.controller?.scriptURL)).toMatch(/\/sw\.js$/u)

  const browser = await context.newCDPSession(page)
  const { installabilityErrors } = await browser.send('Page.getInstallabilityErrors')
  expect(installabilityErrors).toEqual([])
  await browser.detach()

  const result = await request.get('/manifest.webmanifest')
  expect(result.headers()['content-type']).toBe('application/manifest+json')
  const manifest = await result.json()
  expect(manifest).toMatchObject({ display: 'standalone', start_url: '/', scope: '/', lang: 'ru' })
  for (const { src, sizes } of [...manifest.icons, { src: '/icons/apple-touch-icon.png', sizes: '180x180' }]) {
    const image = await request.get(src)
    expect(image.headers()['content-type']).toBe('image/png')
    const pixels = await image.body()
    expect(pixels.readUInt32BE(16)).toBe(Number(sizes.split('x')[0]))
    expect(pixels.readUInt32BE(20)).toBe(Number(sizes.split('x')[1]))
  }

  const resultStatus = await page.evaluate(async () => (await fetch('/api/v1/auth/me')).status)
  expect(resultStatus).toBe(401)
  const cached = await page.evaluate(async () => {
    const assets = await Promise.all((await caches.keys()).map(async key => (await caches.open(key)).keys()))
    return assets.flat().map(item => new URL(item.url).pathname)
  })
  expect(cached.length).toBeGreaterThan(0)
  expect(cached.every(path => path.startsWith('/assets/'))).toBe(true)
  expect((await request.get('/api/v1/auth/me')).headers()['cache-control']).toBe('no-store')
})

test('обновление ожидает решения и сохраняет открытые формы в другой вкладке', async ({ page, context }, testInfo) => {
  test.skip(testInfo.project.name !== 'windows-desktop', 'Переход на новую версию проверяется в двух десктопных вкладках.')
  const dist = resolve('dist')
  const worker = await readFile(resolve(dist, 'sw.js'), 'utf8')
  let revision = worker
  const server = createServer(async (req, res) => {
    res.setHeader('Cache-Control', 'no-store')
    const path = req.url ?? '/'
    if (path === '/sw.js') {
      res.setHeader('Content-Type', 'text/javascript').end(revision)
    } else if (path === '/api/v1/auth/me') {
      res.writeHead(401, { 'Content-Type': 'application/json' }).end('{"detail":"authentication_required"}')
    } else if (path === '/' || path === '/login' || /^\/assets\/[\w-]+\.(css|js)$/u.test(path)) {
      const filename = path.startsWith('/assets/') ? '.' + path : 'index.html'
      const type = path.endsWith('.css') ? 'text/css' : path.endsWith('.js') ? 'text/javascript' : 'text/html'
      res.setHeader('Content-Type', type).end(await readFile(resolve(dist, filename)))
    } else {
      res.writeHead(404).end()
    }
  })
  await new Promise<void>(done => server.listen(0, '127.0.0.1', done))
  try {
    const origin = 'http://127.0.0.1:' + (server.address() as AddressInfo).port
    await page.goto(origin)
    await expect.poll(() => page.evaluate(() => !!navigator.serviceWorker.controller)).toBe(true)
    const second = await context.newPage()
    await second.goto(origin)
    const email = second.getByLabel('Электронная почта')
    await email.fill('unsaved@example.com')
    revision = worker.replace(/const revision = "[a-f0-9]{16}"/u, 'const revision = "1111111111111111"')
    expect(revision).not.toBe(worker)
    await page.evaluate(async () => (await navigator.serviceWorker.ready).update().then(() => null))
    const update = page.getByRole('button', { name: 'Обновить приложение' })
    await expect(update).toBeVisible()
    await expect(second.getByRole('button', { name: 'Обновить приложение' })).toBeVisible()
    await update.click()
    await expect(update).toHaveCount(0)
    await expect(second.getByRole('button', { name: 'Перезагрузить приложение' })).toBeVisible()
    await expect(email).toHaveValue('unsaved@example.com')
  } finally {
    server.closeAllConnections()
    await new Promise<void>(done => server.close(() => done()))
  }
})
