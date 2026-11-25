import type { ButtonHTMLAttributes, ReactNode } from 'react'

// Bouton d'une action longue (épreuve git, vérification auprès d'une forge…) :
// pendant l'attente, il est désactivé, montre l'anneau et un texte d'attente,
// et le curseur passe en « progress ». Sans cela, plusieurs secondes sans
// retour font croire que rien ne se passe — et cliquer de nouveau.
export function BusyButton({
  busy,
  busyText,
  children,
  className = 'btn',
  disabled,
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & { busy: boolean; busyText: string; children: ReactNode }) {
  return (
    <button
      {...rest}
      className={busy ? `${className} btn-busy` : className}
      disabled={disabled || busy}
      aria-busy={busy}
    >
      {busy ? (
        <>
          <span className="spinner spinner-sm" aria-hidden="true" />
          {busyText}
        </>
      ) : (
        children
      )}
    </button>
  )
}
