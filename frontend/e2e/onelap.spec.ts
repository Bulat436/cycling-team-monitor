import { expect, test } from '@playwright/test'

const password = process.env.CTM_E2E_PASSWORD!

test('без разрешённого шлюза OnelapFit показывает статус и оставляет ручной импорт', async ({ page }, testInfo) => {
  await page.goto('/login')
  await page.getByLabel('Электронная почта').fill(testInfo.project.name + '@example.com')
  await page.getByLabel('Пароль', { exact: true }).fill(password)
  await page.getByRole('button', { name: 'Войти', exact: true }).click()
  await page.getByRole('link', { name: 'Профиль', exact: true }).click()
  await expect(page.getByText('Интеграция недоступна', { exact: true })).toBeVisible()
  await expect(page.getByLabel('Пароль OnelapFit')).toHaveCount(0)
  await page.getByText('Журнал синхронизации').click()
  await expect(page.getByText('Запусков пока нет.')).toBeVisible()
  await page.getByRole('link', { name: 'Загрузить FIT вручную' }).click()
  await expect(page.getByRole('button', { name: 'Загрузить FIT' })).toBeVisible()
})
