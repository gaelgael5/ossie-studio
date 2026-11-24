import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { RouterProvider, createBrowserRouter } from 'react-router-dom'
import './i18n'
import './styles.css'
import { App } from './App'

const queryClient = new QueryClient()
// Routeur de données (et non BrowserRouter) : il permet d'intercepter une sortie
// d'écran avec des modifications non enregistrées (useBlocker, bug 5a6b0213).
// Les routes restent déclarées dans App (<Routes>), sous une route attrape-tout.
const router = createBrowserRouter([{ path: '*', element: <App /> }])

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  </StrictMode>,
)
