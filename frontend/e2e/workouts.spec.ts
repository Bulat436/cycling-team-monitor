import { readFile } from 'node:fs/promises'
import { resolve } from 'node:path'
import { expect, test } from '@playwright/test'

const password = process.env.CTM_E2E_PASSWORD!

test('импорт FIT, карточка, RPE, повторная загрузка и ошибка CRC', async ({ page }, testInfo) => {
  await page.goto('/login')
  await page.getByLabel('Электронная почта').fill(testInfo.project.name + '@example.com')
  await page.getByLabel('Пароль', { exact: true }).fill(password)
  await page.getByRole('button', { name: 'Войти', exact: true }).click()
  await page.getByRole('link', { name: 'Тренировки', exact: true }).click()
  const historyCards = page.getByRole('article', { name: /Тренировка от/u })
  await expect(historyCards).toHaveCount(2)
  const fit = await readFile(resolve('../backend/tests/fixtures/cycling-power.fit'))
  const fileInput = page.getByLabel('Файл тренировки (.fit)')
  const upload = page.getByRole('button', { name: 'Загрузить FIT' })
  const choose = (buffer: Buffer) => fileInput.setInputFiles({
    name: 'Тренировка.FIT', mimeType: 'application/octet-stream', buffer,
  })

  await choose(fit)
  await upload.click()
  await expect(page.getByText('Тренировка сохранена.')).toBeVisible()
  await expect(historyCards).toHaveCount(3)
  await page.getByRole('link', { name: 'Открыть импортированную тренировку' }).click()
  await expect(page.getByRole('heading', { name: 'Сводка FIT' })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Показания датчиков' })).toHaveCount(0)
  await expect(page.getByText('0,6 км', { exact: true })).toBeVisible()
  await expect(page.getByText('Записан маршрут')).toBeVisible()
  await expect(page.getByText('В истории нет FTP, действовавшего на дату тренировки.')).toHaveCount(2)
  await expect(page.getByRole('link', { name: 'Скачать исходный FIT' })).toBeVisible()
  await page.getByLabel('Сложность тренировки (RPE)').selectOption('7')
  await page.getByRole('button', { name: 'Сохранить RPE' }).click()
  await expect(page.getByText('14 усл. ед.')).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)

  await page.getByRole('link', { name: 'К истории тренировок' }).click()
  await choose(fit)
  await upload.click()
  await expect(page.getByText('Эта тренировка уже в истории.')).toBeVisible()
  await expect(historyCards).toHaveCount(3)
  const corrupt = Buffer.from(fit)
  corrupt[corrupt.length - 1] ^= 1
  await choose(corrupt)
  await upload.click()
  await expect(page.getByRole('alert')).toHaveText('Проверка целостности FIT не прошла. Экспортируйте файл повторно.')
  await expect(historyCards).toHaveCount(3)
})
