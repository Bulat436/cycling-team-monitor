import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { createBrowserRouter, RouterProvider } from 'react-router'
import { QueryClientProvider } from '@tanstack/react-query'

import { App } from './App'
import { Pwa } from './Pwa'
import { makeQueryClient } from './session'
import './styles.css'

const router = createBrowserRouter([{ path: '*', element: <App /> }])

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={makeQueryClient()}>
      <RouterProvider router={router} />
      <Pwa />
    </QueryClientProvider>
  </StrictMode>,
)
