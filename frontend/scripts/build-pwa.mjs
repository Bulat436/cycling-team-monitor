import { createHash } from 'node:crypto'
import { readFile, readdir, writeFile } from 'node:fs/promises'

const dist = new URL('../dist/', import.meta.url)
const files = await readdir(new URL('assets/', dist), { withFileTypes: true })
const assets = files.filter(file => file.isFile() && /-[\w-]{8,}\.(css|js)$/u.test(file.name))
  .map(file => '/assets/' + file.name).sort()
if (!assets.length) throw new Error('No fingerprinted Vite assets found')
const worker = await readFile(new URL('./service-worker.js', import.meta.url), 'utf8')
const revision = createHash('sha256')
  .update(worker)
  .update(JSON.stringify(assets))
  .update(await readFile(new URL('index.html', dist)))
  .digest('hex').slice(0, 16)
await writeFile(new URL('sw.js', dist),
  'const revision = ' + JSON.stringify(revision) + '\n' +
  'const assets = ' + JSON.stringify(assets) + '\n' + worker)
