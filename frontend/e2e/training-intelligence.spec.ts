import { expect, test } from '@playwright/test'
import type { Page } from '@playwright/test'

async function signIn(page: Page, email: string) {
  await page.goto('/login')
  await page.getByLabel('Электронная почта').fill(email)
  await page.getByLabel('Пароль', { exact: true }).fill(process.env.CTM_E2E_PASSWORD!)
  await page.getByRole('button', { name: 'Войти', exact: true }).click()
}

async function expectFits(page: Page) {
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
}

test('тренер открывает расчёты, проверяет предупреждение и размечает контрольный FIT', async ({ page }, testInfo) => {
  if (testInfo.project.name !== 'windows-desktop') await page.setViewportSize({ width: 320, height: 720 })
  await signIn(page, 'profiles-coach@example.com')
  await page.getByRole('navigation', { name: 'Разделы' }).getByRole('link', { name: 'Нагрузка и восстановление' }).click()
  await page.getByLabel('Достаточность данных').selectOption('insufficient')
  await page.getByRole('link', { name: 'Спортсмен analytics-review' }).click()
  await page.getByRole('button', { name: '42 дня' }).click()
  await expect(page.getByRole('heading', { name: 'Дневной TSS' })).toBeVisible()
  await expect(page.getByRole('group', { name: 'Дневной TSS', exact: true }).locator('.recharts-surface')).toBeVisible()
  await expectFits(page)
  const tabs = page.getByRole('navigation', { name: 'Вкладки нагрузки и восстановления' })
  await tabs.getByRole('link', { name: 'Восстановление', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Дневник восстановления' })).toBeVisible()
  await tabs.getByRole('link', { name: 'Взаимосвязи' }).click()
  await page.locator('summary').filter({ hasText: 'Недостаточность данных' }).click()
  await expect(page.getByRole('heading', { name: 'Фактические значения' })).toBeVisible()
  await expectFits(page)
  await tabs.getByRole('link', { name: 'Работоспособность' }).click()
  await page.getByText('Открыть FIT-тренировку для разметки').click()
  await page.getByRole('link', { name: /· тренировка \d+$/u }).last().click()
  const form = page.getByRole('form', { name: 'Разметка контрольного отрезка' })
  await form.getByLabel('Код протокола').fill('erg-60')
  await form.getByLabel('Начало от старта FIT, с').fill('10')
  await form.getByLabel('Длительность отрезка, с').fill('60')
  await expect(form.getByText('Выбранный отрезок: 0:10–1:10 от начала FIT.')).toBeVisible()
  await form.getByRole('button', { name: 'Проверить и сохранить отрезок' }).click()
  await expect(page.getByText('Средняя мощность: 200 Вт', { exact: true })).toBeVisible()
  await expectFits(page)
  await page.getByRole('link', { name: 'К истории сопоставимых попыток' }).click()
  await expect(page.getByRole('heading', { name: 'erg-60 · 60 с' })).toBeVisible()
})

test('спортсмен видит свою сводку и сохраняет дополнительные поля пропущенного дня', async ({ page }, testInfo) => {
  await signIn(page, testInfo.project.name + '@example.com')
  await page.getByRole('link', { name: 'Моя нагрузка и восстановление' }).click()
  await expect(page.getByRole('heading', { name: 'Моя нагрузка и восстановление' })).toBeVisible()
  await expectFits(page)
  await page.getByRole('link', { name: /^Заполнить за/u }).first().click()
  const form = page.getByRole('form', { name: 'Утренняя анкета' })
  for (const name of ['Качество сна', 'Общая усталость', 'Усталость ног', 'Мотивация', 'Настроение', 'Мышечная болезненность']) {
    await form.getByRole('group', { name }).getByRole('radio', { name: '3', exact: true }).check()
  }
  await form.getByRole('radio', { name: 'Нет' }).check()
  await form.getByLabel(/Продолжительность сна/u).fill('430')
  await form.getByLabel(/Самооценка восстановления/u).selectOption('4')
  await form.getByLabel(/Подтверждение записей/u).selectOption('rest')
  await form.getByRole('button', { name: 'Сохранить дневник' }).click()
  await expect(form.getByText('Изменения сохранены.')).toBeVisible()
  await page.reload()
  await expect(form.getByLabel(/Продолжительность сна/u)).toHaveValue('430')
  await expect(form.getByLabel(/Подтверждение записей/u)).toHaveValue('rest')
  await page.goto('/training-intelligence')
  await expect(page.getByRole('heading', { name: 'Доступ ограничен' })).toBeVisible()
})
