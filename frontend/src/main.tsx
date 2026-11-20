import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router'
import { QueryClientProvider } from '@tanstack/react-query'

import { App } from './App'
import { Pwa } from './Pwa'
import { makeQueryClient } from './session'
import './styles.css'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={makeQueryClient()}>
      <BrowserRouter><App /></BrowserRouter>
      <Pwa />
    </QueryClientProvider>
  </StrictMode>,
)
