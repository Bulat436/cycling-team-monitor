import { randomBytes } from 'node:crypto'
import { existsSync, mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { defineConfig, devices } from '@playwright/test'

const origin = 'http://127.0.0.1:8765'
const defaultPython = resolve(process.platform === 'win32' ? '../.venv/Scripts/python.exe' : '../.venv/bin/python')
const python = process.env.CTM_TEST_PYTHON ?? (existsSync(defaultPython) ? defaultPython : 'python')
const password = process.env.CTM_E2E_PASSWORD ?? randomBytes(24).toString('base64url')
process.env.CTM_E2E_PASSWORD = password

function createTestDirectory() {
  const directory = mkdtempSync(join(tmpdir(), 'ctm-playwright-'))
  // The Playwright runner stops the server before exiting, including on Windows.
  process.once('exit', () => rmSync(directory, { recursive: true, force: true, maxRetries: 5, retryDelay: 200 }))
  return directory
}

const directory = process.env.TEST_WORKER_INDEX === undefined ? createTestDirectory() : ''

export default defineConfig({
  testDir: './e2e',
  workers: 1,
  use: { baseURL: origin },
  projects: [
    { name: 'windows-desktop', use: { ...devices['Desktop Chrome'], viewport: { width: 1440, height: 900 } } },
    { name: 'android-viewport', use: { ...devices['Pixel 7'] } },
    { name: 'iphone-viewport', use: { ...devices['iPhone 13'], defaultBrowserType: 'chromium' } },
  ],
  webServer: {
    command: '"' + python + '" tests/run_e2e.py',
    cwd: '../backend',
    url: origin + '/api/v1/health',
    env: { CTM_E2E_PASSWORD: password, CTM_E2E_ORIGIN: origin, CTM_E2E_DIRECTORY: directory },
  },
})
