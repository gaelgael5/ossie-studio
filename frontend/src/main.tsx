import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { RouterProvider, createBrowserRouter } from 'react-router-dom'
import './i18n'
import './styles.css'
import { App } from './App'
import { onAutoMerged } from './draftView'

const queryClient = new QueryClient()
// Première modification en vue « Publiée » ou « Brouillon » : la vue repasse en
// « Fusionnée » (bug a512d8c3) ; les écrans se relisent dans cette vue, comme
// lorsqu'on la choisit dans le menu (hors lecture du brouillon lui-même).
onAutoMerged(() => {
  void queryClient.invalidateQueries({ predicate: (q) => q.queryKey[0] !== 'draft' })
})
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
