import type { SchemeInput } from './apiClient'

// Presets repris d'Entropy. Le `name` d'un niveau est canonique (stable, stocké
// dans les assets) ; le libellé d'affichage est choisi selon la langue au moment
// de l'import, puis éditable.
interface PresetLevel {
  name: string
  fr: string
  en: string
  short_name: string
  rank: number
  color: string
}
export interface Preset {
  id: string
  fr: string
  en: string
  multi_value: boolean
  levels: PresetLevel[]
}

export const CLASSIFICATION_PRESETS: Preset[] = [
  {
    id: 'classification',
    fr: 'Classification',
    en: 'Classification',
    multi_value: false,
    levels: [
      { name: 'Public', fr: 'Public', en: 'Public', short_name: 'PUB', rank: 1, color: '#2f9e44' },
      { name: 'Interne', fr: 'Interne', en: 'Internal', short_name: 'INT', rank: 2, color: '#1971c2' },
      { name: 'Confidentiel', fr: 'Confidentiel', en: 'Confidential', short_name: 'CONF', rank: 3, color: '#e8590c' },
      { name: 'Restreint', fr: 'Restreint', en: 'Restricted', short_name: 'RESTR', rank: 4, color: '#c92a2a' },
    ],
  },
  {
    id: 'rgpd',
    fr: 'RGPD',
    en: 'GDPR',
    multi_value: false,
    levels: [
      { name: 'Non personnelle', fr: 'Non personnelle', en: 'Non-personal', short_name: 'NP', rank: 1, color: '#2f9e44' },
      { name: 'Donnée personnelle', fr: 'Donnée personnelle', en: 'Personal data', short_name: 'DP', rank: 2, color: '#1971c2' },
      { name: 'Catégorie particulière', fr: 'Catégorie particulière', en: 'Special category', short_name: 'CP', rank: 3, color: '#e8590c' },
      { name: 'Infraction pénale', fr: 'Infraction pénale', en: 'Criminal offence', short_name: 'IP', rank: 4, color: '#c92a2a' },
    ],
  },
  {
    id: 'rgpd-categories-particulieres',
    fr: 'RGPD — catégories particulières',
    en: 'GDPR — special categories',
    multi_value: true,
    levels: [
      { name: 'Origine raciale ou ethnique', fr: 'Origine raciale ou ethnique', en: 'Racial or ethnic origin', short_name: 'RACE', rank: 1, color: '#9c36b5' },
      { name: 'Opinions politiques', fr: 'Opinions politiques', en: 'Political opinions', short_name: 'POL', rank: 2, color: '#9c36b5' },
      { name: 'Santé', fr: 'Santé', en: 'Health', short_name: 'SANTE', rank: 3, color: '#9c36b5' },
      { name: 'Données biométriques', fr: 'Données biométriques', en: 'Biometric data', short_name: 'BIO', rank: 4, color: '#9c36b5' },
    ],
  },
  {
    id: 'tlp',
    fr: 'TLP 2.0',
    en: 'TLP 2.0',
    multi_value: false,
    levels: [
      { name: 'CLEAR', fr: 'CLEAR', en: 'CLEAR', short_name: 'CLEAR', rank: 1, color: '#adb5bd' },
      { name: 'GREEN', fr: 'GREEN', en: 'GREEN', short_name: 'GREEN', rank: 2, color: '#2f9e44' },
      { name: 'AMBER', fr: 'AMBER', en: 'AMBER', short_name: 'AMBER', rank: 3, color: '#e8590c' },
      { name: 'AMBER+STRICT', fr: 'AMBER+STRICT', en: 'AMBER+STRICT', short_name: 'AMBER+', rank: 4, color: '#e8590c' },
      { name: 'RED', fr: 'RED', en: 'RED', short_name: 'RED', rank: 5, color: '#c92a2a' },
    ],
  },
]

export function presetToScheme(preset: Preset, lang: string | undefined): SchemeInput {
  const en = (lang ?? 'fr').startsWith('en')
  return {
    id: preset.id,
    name: en ? preset.en : preset.fr,
    description: '',
    badge_prefix: '',
    multi_value: preset.multi_value,
    is_primary: false,
    levels: preset.levels.map((l) => ({
      name: l.name,
      label: en ? l.en : l.fr,
      short_name: l.short_name,
      rank: l.rank,
      color: l.color,
    })),
  }
}
