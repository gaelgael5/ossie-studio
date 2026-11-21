// Règle des slugs côté interface — miroir de `backend/src/ossie_studio/slugs.py`
// et des motifs validés par le backend : sources `^[a-z0-9]+(-[a-z0-9]+)*$`,
// secrets `^[a-z0-9][a-z0-9_-]{0,63}$`. On ne produit que l'intersection
// (minuscules, chiffres, tirets simples, 64 caractères au plus) : un slug
// proposé par l'interface est ainsi accepté par TOUS les formulaires.

export const SLUG_MAX_LENGTH = 64

function asciiLower(value: string): string {
  return value
    .normalize('NFD')
    .replace(/[̀-ͯ]/g, '')
    .toLowerCase()
}

/** Slug final dérivé d'un libellé : « Clé GitHub (prod) » → « cle-github-prod ». */
export function slugify(label: string): string {
  return asciiLower(label)
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+/, '')
    .slice(0, SLUG_MAX_LENGTH)
    .replace(/-+$/, '')
}

/**
 * Nettoyage PENDANT la saisie manuelle du slug. Contrairement à `slugify`, on
 * garde un tiret final : sinon l'utilisateur ne pourrait jamais taper
 * « mon-slug » (le tiret disparaîtrait à chaque frappe). Les bords sont
 * nettoyés par `finalizeSlug` au moment de l'envoi.
 */
export function sanitizeSlugInput(value: string): string {
  return asciiLower(value)
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+/, '')
    .slice(0, SLUG_MAX_LENGTH)
}

/** Forme envoyée au serveur : sans tiret en bord. */
export function finalizeSlug(value: string): string {
  return value.replace(/^-+|-+$/g, '')
}
