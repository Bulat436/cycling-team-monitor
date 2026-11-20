import { randomBytes } from 'node:crypto'
import { expect, test } from '@playwright/test'
import type { Page } from '@playwright/test'

const coachPassword = process.env.CTM_E2E_PASSWORD!

async function signIn(page: Page, email: string, password: string) {
  await page.getByLabel('Электронная почта').fill(email)
  await page.getByLabel('Пароль', { exact: true }).fill(password)
  await page.getByRole('button', { name: 'Войти', exact: true }).click()
}

test('вход, навигация и выход при заданном размере экрана', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByRole('heading', { name: 'Вход в команду' })).toBeVisible()
  await signIn(page, 'coach@example.com', coachPassword)
  await expect(page.getByRole('heading', { name: 'Команда', exact: true })).toBeVisible()
  await page.getByRole('link', { name: 'Управление доступом' }).click()
  await expect(page.getByRole('heading', { name: 'Управление доступом' })).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
  await page.reload()
  await expect(page.getByRole('heading', { name: 'Управление доступом' })).toBeVisible()
  await page.getByRole('button', { name: 'Выйти' }).click()
  await expect(page).toHaveURL(/\/login$/u)
})

test('тренер приглашает спортсмена, сбрасывает пароль и закрывает доступ', async ({ page, browser }, testInfo) => {
  test.skip(testInfo.project.name !== 'windows-desktop', 'Жизненный цикл доступа проверяется на desktop.')
  const recipient = await browser.newPage()
  const email = 'athlete@example.com'
  const password = randomBytes(24).toString('base64url')
  const newPassword = randomBytes(24).toString('base64url')

  async function setPassword(url: string, value: string) {
    await recipient.goto(url)
    await recipient.getByLabel('Новый пароль').fill(value)
    await recipient.getByLabel('Повторите пароль').fill(value)
    await recipient.getByRole('button', { name: 'Сохранить пароль' }).click()
    await expect(recipient.getByRole('heading', { name: 'Вход в команду' })).toBeVisible()
  }

  try {
    await page.goto('/login')
    await signIn(page, 'coach@example.com', coachPassword)
    await page.getByRole('link', { name: 'Пригласить спортсмена' }).click()
    await page.getByLabel('Имя и фамилия').fill('Саша Спринтер')
    await page.getByLabel('Электронная почта').fill(email)
    await page.getByRole('button', { name: 'Создать приглашение' }).click()
    const invitation = await page.getByLabel('Одноразовая ссылка').inputValue()
    await setPassword(invitation, password)

    await signIn(recipient, email, password)
    await expect(recipient.getByRole('heading', { name: 'Главная', exact: true })).toBeVisible()
    await recipient.getByRole('link', { name: 'Профиль', exact: true }).click()
    await expect(recipient.getByText(email)).toBeVisible()
    await recipient.goto('/access')
    await expect(recipient.getByRole('heading', { name: 'Доступ ограничен' })).toBeVisible()
    await expect(recipient.getByRole('link', { name: 'Управление доступом' })).toHaveCount(0)

    await page.reload()
    page.once('dialog', dialog => void dialog.accept())
    await page.getByRole('button', { name: 'Сбросить пароль' }).click()
    const reset = await page.getByLabel('Одноразовая ссылка').inputValue()
    await recipient.reload()
    await expect(recipient).toHaveURL(/\/login$/u)
    await setPassword(reset, newPassword)
    await signIn(recipient, email, newPassword)
    await expect(recipient.getByRole('heading', { name: 'Главная', exact: true })).toBeVisible()

    page.once('dialog', dialog => void dialog.accept())
    await page.getByRole('button', { name: 'Деактивировать' }).click()
    await expect(page.getByText('Деактивирован', { exact: true })).toBeVisible()
    await recipient.reload()
    await expect(recipient).toHaveURL(/\/login$/u)
    await page.getByRole('link', { name: 'Журнал действий' }).click()
    await expect(page.getByRole('rowheader', { name: 'Учётная запись деактивирована' })).toBeVisible()
  } finally {
    await recipient.close()
  }
})
