// Dates de la cartographie : tirées de l'historique Git (ISO 8601), affichées
// courtes (« 24/11 ») sur les vignettes, complètes sur la fiche.

export function shortDate(iso: string, language: string | undefined): string {
  return new Intl.DateTimeFormat(language, { day: '2-digit', month: '2-digit' }).format(new Date(iso))
}

export function longDate(iso: string, language: string | undefined): string {
  return new Intl.DateTimeFormat(language, { dateStyle: 'medium' }).format(new Date(iso))
}
