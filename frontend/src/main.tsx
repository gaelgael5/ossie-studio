import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { RouterProvider, createBrowserRouter } from 'react-router-dom'
import './i18n'
import './styles.css'
import { App } from './App'
import { onAutoMerged } from './draftView'

const queryClient = new QueryClient()
// Un onglet ouvert avant un redéploiement réclame des morceaux de code dont le
// nom haché n'existe plus : on recharge la page une fois pour prendre la
// nouvelle version (bug f5f5bfa9). L'horodatage évite une boucle si le
// morceau manque vraiment : dans ce cas, l'erreur s'affiche comme avant.
window.addEventListener('vite:preloadError', (event) => {
  const KEY = 'ossie-preload-reload'
  let last = 0
  try {
    last = Number(sessionStorage.getItem(KEY) ?? 0)
    if (Date.now() - last < 10_000) return
    sessionStorage.setItem(KEY, String(Date.now()))
  } catch {
    return
  }
  event.preventDefault()
  window.location.reload()
})
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
