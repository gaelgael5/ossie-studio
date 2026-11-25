import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { finalizeSlug, sanitizeSlugInput, slugify } from '../slug'

// RÈGLE (docs/regles-interface.md) : partout où l'on saisit un libellé et un
// slug, le libellé vient d'abord, le slug se déduit du libellé à chaque frappe,
// et Tab mène du libellé au slug, que l'utilisateur peut corriger. Ce composant
// est le seul point qui l'implémente : aucun formulaire ne recode la paire.

export interface LabelSlug {
  label: string
  /** Slug affiché (dérivé du libellé tant qu'il n'a pas été corrigé). */
  slug: string
  /** Slug à envoyer au serveur (sans tiret en bord). */
  finalSlug: string
  setLabel: (value: string) => void
  setSlug: (value: string) => void
  /** Sortie du champ slug : vide, il se remet à suivre le libellé. */
  releaseIfEmpty: () => void
  /** Charge un objet existant (édition) : son libellé et son slug figé. */
  load: (label: string, slug: string) => void
  reset: () => void
}

/**
 * État de la paire libellé/slug. Le slug est DÉRIVÉ au rendu tant que
 * l'utilisateur ne l'a pas corrigé — pas recopié dans un effet, qui ferait
 * écraser une correction en cours. Un slug vidé reste vide tant qu'on est dans
 * le champ (sinon on ne pourrait pas l'effacer pour le retaper) ; vide en
 * sortant du champ, il se remet à suivre le libellé.
 */
export function useLabelSlug(initial?: { label?: string; slug?: string }): LabelSlug {
  const [label, setLabel] = useState(initial?.label ?? '')
  // null = pas de correction manuelle : le slug suit le libellé. Un slug
  // existant (édition) est une valeur fixée, jamais recalculée.
  const [manualSlug, setManualSlug] = useState<string | null>(initial?.slug || null)
  const slug = manualSlug ?? slugify(label)
  return {
    label,
    slug,
    finalSlug: finalizeSlug(slug),
    setLabel,
    setSlug: (value) => setManualSlug(sanitizeSlugInput(value)),
    releaseIfEmpty: () => setManualSlug((current) => (current === '' ? null : current)),
    load: (nextLabel, nextSlug) => {
      setLabel(nextLabel)
      setManualSlug(nextSlug || null)
    },
    reset: () => {
      setLabel('')
      setManualSlug(null)
    },
  }
}

interface Props {
  /** Préfixe des id HTML (plusieurs paires peuvent cohabiter dans une page). */
  idPrefix: string
  state: LabelSlug
  labelText: string
  slugText: string
  /** Slug figé (édition d'un objet existant). */
  slugLocked?: boolean
}

/** Les deux champs, dans l'ordre imposé : libellé, puis slug. */
export function LabelSlugFields({ idPrefix, state, labelText, slugText, slugLocked = false }: Props) {
  const { t } = useTranslation()
  const labelId = `${idPrefix}-label`
  const slugId = `${idPrefix}-slug`
  return (
    <>
      <div className="field">
        <label className="field-label" htmlFor={labelId}>
          {labelText}
        </label>
        <input
          id={labelId}
          className="input"
          value={state.label}
          onChange={(e) => state.setLabel(e.target.value)}
        />
      </div>
      <div className="field">
        <label className="field-label" htmlFor={slugId}>
          {slugText}
        </label>
        <input
          id={slugId}
          className="input mono"
          value={state.slug}
          disabled={slugLocked}
          onChange={(e) => state.setSlug(e.target.value)}
          onBlur={state.releaseIfEmpty}
        />
        {slugLocked ? null : <span className="field-hint">{t('slugDerivedHint')}</span>}
      </div>
    </>
  )
}
