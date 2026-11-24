import '@testing-library/jest-dom/vitest'
import { createElement } from 'react'
import { vi } from 'vitest'
import i18n from './i18n'

// Les tests asservissent les libellés au français, quelle que soit la locale
// détectée par jsdom.
void i18n.changeLanguage('fr')

// Monaco ne tourne pas sous jsdom (canvas, workers) : la visionneuse de code
// devient un simple <pre> dans les tests (ticket a29fd1ec).
vi.mock('./components/CodeViewerMonaco', () => ({
  default: ({ value }: { value: string }) => createElement('pre', { className: 'raw-json' }, value),
}))
