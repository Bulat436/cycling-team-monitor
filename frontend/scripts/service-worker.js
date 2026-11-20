const namespace = 'ctm-assets-'
const cacheName = namespace + revision
const publicAssets = new Set(assets)
let previousCaches = []

self.addEventListener('install', event => {
  event.waitUntil(caches.open(cacheName).then(cache => cache.addAll(assets)))
})

self.addEventListener('activate', event => {
  event.waitUntil((async () => {
    const older = (await caches.keys()).filter(name => name.startsWith(namespace) && name !== cacheName)
    for (const name of older.slice(0, -1)) await caches.delete(name)
    await self.clients.claim()
  })())
})

// The worker can be restarted between fetch events, so discover earlier public assets again.
async function findPreviousAsset(request) {
  if (!previousCaches.length) {
    previousCaches = (await caches.keys()).filter(name => name.startsWith(namespace) && name !== cacheName)
  }
  for (const name of previousCaches) {
    const match = await caches.match(request, { cacheName: name })
    if (match) return match
  }
}

self.addEventListener('message', event => {
  if (event.data === 'activate-update') event.waitUntil(self.skipWaiting())
})

// Only preinstalled public, fingerprinted build assets are served from storage.
self.addEventListener('fetch', event => {
  const url = new URL(event.request.url)
  if (event.request.method !== 'GET' || url.origin !== self.location.origin || url.search ||
      !url.pathname.startsWith('/assets/')) return
  event.respondWith((async () => {
    const cached = publicAssets.has(url.pathname)
      ? await caches.match(event.request, { cacheName })
      : await findPreviousAsset(event.request)
    return cached ?? fetch(event.request)
  })())
})
