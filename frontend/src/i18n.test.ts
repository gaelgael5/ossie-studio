import { expect, test } from 'vitest'
import { fr } from './locales/fr'
import { en } from './locales/en'
import errorCodes from './errorCodes.json'

function flatKeys(obj: Record<string, unknown>, prefix = ''): string[] {
  return Object.entries(obj).flatMap(([k, v]) =>
    typeof v === 'object' && v !== null
      ? flatKeys(v as Record<string, unknown>, `${prefix}${k}.`)
      : [`${prefix}${k}`],
  )
}

test('les clefs FR et EN sont strictement les mêmes', () => {
  const frKeys = flatKeys(fr.translation).sort()
  const enKeys = flatKeys(en.translation).sort()
  expect(frKeys).toEqual(enKeys)
})

test('chaque code d’erreur a une traduction FR et EN', () => {
  const frErrors = fr.translation.errors as Record<string, string>
  const enErrors = en.translation.errors as Record<string, string>
  for (const code of errorCodes as string[]) {
    expect(frErrors[code], `FR manque pour ${code}`).toBeTruthy()
    expect(enErrors[code], `EN manque pour ${code}`).toBeTruthy()
  }
})
