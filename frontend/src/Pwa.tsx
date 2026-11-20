import { useEffect, useRef, useState } from 'react'

interface InstallEvent extends Event {
  prompt(): Promise<void>
  userChoice: Promise<{ outcome: string }>
}

export function Pwa() {
  const [registered, setRegistered] = useState(false)
  const [reloadNeeded, setReloadNeeded] = useState(false)
  const [waiting, setWaiting] = useState<ServiceWorker>()
  const [install, setInstall] = useState<InstallEvent>()
  const sessionUpdate = useRef(false)

  useEffect(() => {
    if (!import.meta.env.PROD || !('serviceWorker' in navigator)) return

    const workers = navigator.serviceWorker
    let active = true
    let registered: ServiceWorkerRegistration | undefined
    let installing: ServiceWorker | null = null
    let controller = workers.controller
    let lastCheck = 0

    const controllerChanged = () => {
      const previous = controller
      controller = workers.controller
      if (previous && controller && previous !== controller) {
        if (sessionUpdate.current) window.location.reload()
        else {
          setWaiting(undefined)
          setReloadNeeded(true)
        }
      }
    }
    const showWaiting = () => {
      if (registered?.waiting && workers.controller) setWaiting(registered.waiting)
    }
    const observeUpdate = () => {
      installing?.removeEventListener('statechange', showWaiting)
      installing = registered?.installing ?? null
      installing?.addEventListener('statechange', showWaiting)
      showWaiting()
    }
    const check = () => {
      if (document.visibilityState === 'visible' && Date.now() - lastCheck > 60_000) {
        lastCheck = Date.now()
        void registered?.update().catch(() => {})
      }
    }
    const offer = (event: Event) => {
      event.preventDefault()
      setInstall(event as InstallEvent)
    }
    const installed = () => setInstall(undefined)

    workers.addEventListener('controllerchange', controllerChanged)
    window.addEventListener('beforeinstallprompt', offer)
    window.addEventListener('appinstalled', installed)
    document.addEventListener('visibilitychange', check)
    void workers.register('/sw.js', { updateViaCache: 'none' }).then(value => {
      if (!active) return
      registered = value
      setRegistered(true)
      value.addEventListener('updatefound', observeUpdate)
      observeUpdate()
    }).catch(() => {})

    return () => {
      active = false
      registered?.removeEventListener('updatefound', observeUpdate)
      installing?.removeEventListener('statechange', showWaiting)
      workers.removeEventListener('controllerchange', controllerChanged)
      window.removeEventListener('beforeinstallprompt', offer)
      window.removeEventListener('appinstalled', installed)
      document.removeEventListener('visibilitychange', check)
    }
  }, [])

  if (!waiting && !reloadNeeded && (!install || !registered)) return null
  return (
    <aside className="pwa-actions" aria-label="Установка и обновления">
      {waiting && <button type="button" onClick={() => {
        sessionUpdate.current = true
        waiting.postMessage('activate-update')
      }}>
        Обновить приложение
      </button>}
      {reloadNeeded && <button type="button" onClick={() => window.location.reload()}>Перезагрузить приложение</button>}
      {install && registered && <button type="button" onClick={() => {
        setInstall(undefined)
        void install.prompt().then(() => install.userChoice).catch(() => {})
      }}>Установить приложение</button>}
    </aside>
  )
}
