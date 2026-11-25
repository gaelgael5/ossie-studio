import { expect, test } from 'vitest'
import { loginWithNext, safeNext } from './safeNext'

test('garde un chemin local, refuse toute autre origine', () => {
  expect(safeNext('/oauth/consent?request=abc')).toBe('/oauth/consent?request=abc')
  for (const bad of [null, '', 'https://evil.example.org', '//evil.example.org', '/\\evil', '/a\nb']) {
    expect(safeNext(bad)).toBeNull()
  }
})

test('construit l’adresse de connexion avec retour', () => {
  expect(loginWithNext('/oauth/consent?request=a&b=1')).toBe('/login?next=%2Foauth%2Fconsent%3Frequest%3Da%26b%3D1')
})
