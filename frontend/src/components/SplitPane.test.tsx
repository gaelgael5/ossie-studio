import { fireEvent, render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { SplitPane, clampWidth } from './SplitPane'

// Ticket f74c8e74 : barre de séparation entre une liste et son détail.

// jsdom n'a pas PointerEvent : sans lui, clientX et button sont perdus.
if (typeof window.PointerEvent === 'undefined') {
  class FakePointerEvent extends MouseEvent {
    pointerId: number
    constructor(type: string, init: PointerEventInit = {}) {
      super(type, init)
      this.pointerId = init.pointerId ?? 0
    }
  }
  window.PointerEvent = FakePointerEvent as unknown as typeof PointerEvent
}

function mount(width: number, id = 'ecran') {
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue({
    x: 0, y: 0, top: 0, left: 0, right: width, bottom: 400, width, height: 400, toJSON: () => ({}),
  } as DOMRect)
  vi.stubGlobal('fetch', vi.fn(() => Promise.resolve({ ok: true, status: 200, text: () => Promise.resolve(JSON.stringify({ authenticated: true, bootstrap_required: false, user: { email: 'a@b.c', role: 'admin' } })) })))
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <SplitPane id={id} left={<p>liste</p>} right={<p>détail</p>} />
    </QueryClientProvider>,
  )
}

const columns = (id = 'ecran') => (screen.getByTestId(`split-${id}`) as HTMLElement).style.gridTemplateColumns

beforeEach(() => localStorage.clear())
afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

describe('clampWidth', () => {
  it('aucun côté sous 260 px (critère 3)', () => {
    expect(clampWidth(100, 1000)).toBe(260)
    expect(clampWidth(900, 1000)).toBe(1000 - 12 - 260)
    expect(clampWidth(500, 1000)).toBe(500)
  })
})

describe('SplitPane', () => {
  it('séparateur réglable annoncé, à la moitié par défaut (critère 7)', () => {
    mount(1000)
    const bar = screen.getByRole('separator', { name: 'Largeur de la liste et du détail' })
    expect(bar).toHaveAttribute('aria-orientation', 'vertical')
    expect(bar).toHaveAttribute('tabindex', '0')
    expect(columns()).toMatch(/^500px 12px/)
  })

  it('flèches ±24 px, retenu pour l’écran, double-clic : largeur par défaut (critères 2, 4, 5)', () => {
    mount(1000)
    const bar = screen.getByRole('separator')
    fireEvent.keyDown(bar, { key: 'ArrowRight' })
    expect(columns()).toMatch(/^524px/)
    fireEvent.keyDown(bar, { key: 'ArrowLeft' })
    fireEvent.keyDown(bar, { key: 'ArrowLeft' })
    expect(columns()).toMatch(/^476px/)
    expect(Object.entries(localStorage).find(([k]) => k.startsWith('ossie.split.ecran.'))?.[1]).toBe('476')
    fireEvent.doubleClick(bar)
    expect(columns()).toMatch(/^500px/)
    expect(Object.keys(localStorage).some((k) => k.startsWith('ossie.split.ecran.'))).toBe(false)
  })

  it('glisser déplace la barre, borné à 260 px de chaque côté (critères 1, 3)', () => {
    mount(1000)
    const bar = screen.getByRole('separator')
    bar.setPointerCapture = vi.fn()
    bar.releasePointerCapture = vi.fn()
    fireEvent.pointerDown(bar, { button: 0, pointerId: 1, clientX: 506 })
    expect(document.body).toHaveClass('split-dragging')
    fireEvent.pointerMove(bar, { pointerId: 1, clientX: 706 })
    expect(columns()).toMatch(/^700px/)
    fireEvent.pointerMove(bar, { pointerId: 1, clientX: 990 })
    expect(columns()).toMatch(/^728px/)
    fireEvent.pointerUp(bar, { pointerId: 1, clientX: 990 })
    expect(document.body).not.toHaveClass('split-dragging')
  })

  it('largeur retenue relue, ramenée dans les bornes d’une fenêtre plus étroite (critère 4)', () => {
    localStorage.setItem('ossie.split.ecran.anonyme', '900')
    mount(800)
    expect(columns()).toMatch(/^528px/)
  })

  it('fenêtre trop étroite : le détail passe sous la liste, sans barre (critère 6)', () => {
    mount(400)
    expect(screen.queryByRole('separator')).not.toBeInTheDocument()
    expect(screen.getByTestId('split-ecran')).toHaveClass('split-pane-stacked')
    expect(screen.getByText('liste')).toBeInTheDocument()
    expect(screen.getByText('détail')).toBeInTheDocument()
  })
})
