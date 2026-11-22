import type { ReactNode } from 'react'
import { useTranslation } from 'react-i18next'

// Règle d'interface : un formulaire de création n'est jamais affiché en
// permanence. Un bouton « + Créer… » en haut à droite (actions de PageHeader)
// l'ouvre dans ce panneau ; « Annuler » ou une création réussie le referme.

/** Bouton d'ouverture, masqué pendant que le panneau est ouvert. */
export function CreateButton({ open, onOpen, children }: { open: boolean; onOpen: () => void; children: ReactNode }) {
  if (open) return null
  return (
    <button type="button" className="btn btn-primary" onClick={onOpen}>
      {children}
    </button>
  )
}

/** Panneau du formulaire, avec son titre et « Annuler ». */
export function CreatePanel({
  title,
  onCancel,
  children,
}: {
  title: string
  onCancel: () => void
  children: ReactNode
}) {
  const { t } = useTranslation()
  return (
    <section className="panel panel-form stack" role="region" aria-label={title}>
      <div className="panel-head">
        <h3 className="panel-title">{title}</h3>
        <button type="button" className="btn btn-sm" onClick={onCancel}>
          {t('cancel')}
        </button>
      </div>
      {children}
    </section>
  )
}
