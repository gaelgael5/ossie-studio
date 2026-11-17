import '@testing-library/jest-dom/vitest'
import i18n from './i18n'

// Les tests asservissent les libellés au français, quelle que soit la locale
// détectée par jsdom.
void i18n.changeLanguage('fr')
