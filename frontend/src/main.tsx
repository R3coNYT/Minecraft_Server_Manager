import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ApiError } from '@/lib/api'
import { App } from './App'
import { Toaster } from '@/components/common/Toaster'
import './index.css'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // Aucun rafraîchissement au retour sur l'onglet : l'état vivant arrive par
      // WebSocket, et recharger toute l'API à chaque changement de fenêtre
      // reproduirait exactement le défaut que cette version cherche à corriger.
      refetchOnWindowFocus: false,
      staleTime: 10_000,
      retry: (failureCount, error) => {
        // Un refus d'autorisation ne devient pas vrai en réessayant.
        if (error instanceof ApiError && error.status >= 400 && error.status < 500) return false
        return failureCount < 2
      },
    },
  },
})

const container = document.getElementById('root')
if (!container) throw new Error('Élément racine introuvable')

createRoot(container).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <App />
        <Toaster />
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
)
