import { expect, test } from '@playwright/test'
import type { Page } from '@playwright/test'

const password = process.env.CTM_E2E_PASSWORD!

async function signIn(page: Page, email: string) {
  await page.goto('/login')
  await page.getByLabel('Электронная почта').fill(email)
  await page.getByLabel('Пароль', { exact: true }).fill(password)
  await page.getByRole('button', { name: 'Войти', exact: true }).click()
}

test('спортсмен заполняет и исправляет дневник и оценивает две тренировки', async ({ page }, testInfo) => {
  await signIn(page, testInfo.project.name + '@example.com')
  await expect(page.getByRole('heading', { name: 'Главная' })).toBeVisible()
  await expect(page.getByText('Тренировок без RPE — 2')).toBeVisible()
  await page.getByRole('link', { name: 'Заполнить утренний дневник' }).click()
  const form = page.getByRole('form', { name: 'Утренняя анкета' })
  for (const name of ['Качество сна', 'Общая усталость', 'Усталость ног', 'Мотивация', 'Настроение', 'Мышечная болезненность']) {
    await form.getByRole('group', { name }).getByRole('radio', { name: '3', exact: true }).check()
  }
  await form.getByRole('group', { name: 'Симптомы заболевания' }).getByRole('radio', { name: 'Нет' }).check()
  await form.getByLabel('Комментарий (необязательно)').fill('Перед тренировкой')
  await form.getByRole('button', { name: 'Сохранить дневник' }).click()
  await expect(page.getByText('Сегодня заполнен')).toBeVisible()
  await page.reload()
  await expect(form.getByLabel('Комментарий (необязательно)')).toHaveValue('Перед тренировкой')
  await form.getByRole('group', { name: 'Настроение' }).getByRole('radio', { name: '5', exact: true }).check()
  await form.getByRole('button', { name: 'Сохранить дневник' }).click()
  await expect(form.getByText('Изменения сохранены.')).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)

  await page.getByRole('link', { name: 'Тренировки', exact: true }).click()
  const cards = page.getByRole('article', { name: /Тренировка от/u })
  await expect(cards).toHaveCount(2)
  const morning = cards.filter({ hasText: '60 мин' })
  await morning.getByRole('button', { name: 'Указать RPE' }).click()
  await morning.getByLabel('Сложность тренировки (RPE)').selectOption('0')
  await morning.getByRole('button', { name: 'Сохранить RPE' }).click()
  await expect(morning.getByText('0 из 10')).toBeVisible()
  await expect(morning.getByText('0 усл. ед.')).toBeVisible()
  const evening = cards.filter({ hasNotText: '60 мин' })
  await evening.getByRole('button', { name: 'Указать RPE' }).click()
  await evening.getByLabel('Сложность тренировки (RPE)').selectOption('10')
  await evening.getByRole('button', { name: 'Сохранить RPE' }).click()
  await expect(evening.getByText('10 из 10')).toBeVisible()
  await expect(page.getByText(/Тренировок без RPE/u)).toHaveCount(0)

  await morning.getByLabel('Сложность тренировки (RPE)').selectOption('6')
  await morning.getByRole('button', { name: 'Сохранить RPE' }).click()
  await expect(morning.getByText('360 усл. ед.')).toBeVisible()
})

test('тренер меняет профиль, историю показателей и зоны и читает дневник', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'windows-desktop', 'Редактирование профилей тренером проверяется на desktop.')
  await signIn(page, 'profiles-coach@example.com')
  const row = page.getByRole('row', { name: /Спортсмен coach-review/u })
  await expect(row.getByRole('link', { name: 'Заполнен', exact: true })).toBeVisible()
  await row.getByRole('link', { name: 'Спортсмен coach-review' }).click()
  const tabs = page.getByRole('navigation', { name: 'Вкладки спортсмена' })
  await tabs.getByRole('link', { name: 'Профиль', exact: true }).click()
  await page.getByLabel('Рост, см').fill('179')
  await page.getByLabel('Команда или группа').fill('Шоссе')
  await page.getByLabel('Заметки тренера').fill('Тестовая заметка')
  await page.getByRole('button', { name: 'Сохранить профиль' }).click()
  await expect(page.getByText('Изменения сохранены.')).toBeVisible()

  await page.getByLabel('Дата измерения').fill('2020-01-01')
  await page.getByLabel('Масса, кг').fill('72.5')
  await page.getByRole('button', { name: 'Сохранить массу' }).click()
  await expect(page.getByText('72,5 кг', { exact: true })).toBeVisible()
  await page.reload()
  await expect(page.getByLabel('Заметки тренера')).toHaveValue('Тестовая заметка')
  await tabs.getByRole('link', { name: 'FTP и зоны' }).click()
  await page.getByLabel('Действует с').fill('2020-01-01')
  await page.getByLabel('FTP, Вт').fill('265')
  await page.getByRole('button', { name: 'Сохранить FTP' }).click()
  await expect(page.getByText('265 Вт', { exact: true })).toBeVisible()

  for (const title of ['Зоны мощности', 'Зоны пульса']) {
    const section = page.getByRole('region', { name: title })
    await section.getByRole('button', { name: 'Добавить зону' }).click()
    await section.getByLabel('Название').fill('Аэробная')
    await section.getByLabel('От', { exact: true }).fill('120')
    await section.getByLabel('До', { exact: true }).fill('150')
    await section.getByRole('button', { name: 'Сохранить зоны' }).click()
    await expect(section.getByText('Изменения сохранены.')).toBeVisible()
  }

  await page.reload()
  await expect(page.getByRole('region', { name: 'Зоны мощности' }).getByLabel('Название')).toHaveValue('Аэробная')
  await tabs.getByRole('link', { name: 'Дневник', exact: true }).click()
  await expect(page.getByText('Сегодня заполнен')).toBeVisible()
  await expect(page.getByRole('button', { name: 'Сохранить дневник' })).toHaveCount(0)
})
