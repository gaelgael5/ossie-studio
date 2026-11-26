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

// jsdom ne fournit pas Blob.text(), que les navigateurs ont : l'import d'un fichier
// BPMN le lit ainsi (ticket 7e061c3e).
if (typeof Blob.prototype.text !== 'function') {
  Blob.prototype.text = function text(this: Blob) {
    return new Promise<string>((resolve, reject) => {
      const reader = new FileReader()
      reader.onload = () => resolve(String(reader.result))
      reader.onerror = () => reject(reader.error)
      reader.readAsText(this)
    })
  }
}
