// Pictogramme d'un type ArchiMate (enabler 0e088eaa) : SVG d'un trait en
// `currentColor`, servi par le catalogue d'Ossie (fichiers livrés avec
// l'application, jamais saisis par quelqu'un). Inséré tel quel pour prendre la
// couleur du texte ; une balise <img> le rendrait noir.
export function ArchimateIcon({ svg, className = '' }: { svg: string; className?: string }) {
  return <span className={`archimate-icon ${className}`.trim()} aria-hidden="true" dangerouslySetInnerHTML={{ __html: svg }} />
}
