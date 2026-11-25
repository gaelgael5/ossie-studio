// Repris de Miles (`portail/frontend/src/dataModels/jsltKeys.ts`, commit aa88631) pour
// l'onglet Imports des référentiels (ticket cda9c43a). Toute correction est à reporter.
/**
 * Les raccourcis de l'éditeur de template, à part du composant.
 *
 * À part, et non dedans : le composant est doublé dans les tests, cette règle ne doit
 * pas l'être. La doublure et le vrai composant appellent les mêmes prédicats, donc ce
 * que les tests mesurent est ce que le navigateur exécute.
 *
 * Quand sa liste d'auto-complétion est ouverte, l'éditeur JSLT CAPTURE Tab, Entrée,
 * Échap et les flèches, et depuis urban-json-editor 0.2.0 cette liste s'ouvre dans bien
 * plus de situations (après `$`, `.`, `[`, `..`, `@.`). Ce que l'éditeur a déjà traité
 * ne se retraite donc pas côté hôte : on agirait deux fois pour une frappe.
 */
type Shortcut = {
  key: string;
  ctrlKey?: boolean;
  metaKey?: boolean;
  altKey?: boolean;
  defaultPrevented?: boolean;
};

/** Ctrl ou Commande : l'application se tient pareil sur PC et sur Mac (Gael, 31/10/2026). */
function commanded(event: Shortcut): boolean {
  return (event.ctrlKey === true || event.metaKey === true) && event.altKey !== true;
}

/**
 * Faut-il exécuter le template sur cette frappe ? Ctrl+F5 ou ⌘F5 (Gael, 31/10/2026).
 *
 * F5 SEUL ne suffit plus : c'est le rechargement de la page dans tous les navigateurs,
 * et le capturer sur un écran privait d'un geste réflexe là où il est le plus attendu —
 * un éditeur qu'on croit figé. Avec un modificateur, la frappe est délibérée.
 */
export function runsOn(event: Shortcut): boolean {
  // Entrée en plus de F5 (ticket 24397542, Gael : « Ctrl+F5 ne fonctionne pas sur
  // Mac ») : sur Mac, F5 est une touche système sans Fn, et ⌘F5 est le raccourci de
  // VoiceOver, pris par macOS avant le navigateur — aucune frappe n'atteint la page.
  // ⌘/Ctrl+Entrée est le geste « exécuter » des éditeurs (notebooks, consoles SQL).
  // Liste d'auto-complétion ouverte, l'éditeur prend Entrée et l'annule : la garde
  // `defaultPrevented` évite d'exécuter en plus d'accepter la suggestion.
  return event.defaultPrevented !== true && commanded(event) && (event.key === "F5" || event.key === "Enter");
}

/** Le raccourci d'exécution à afficher : « ⌘↩ » sur Mac, « Ctrl+Entrée » ailleurs. */
export function isMac(): boolean {
  return typeof navigator !== "undefined" && /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent);
}

/**
 * Faut-il ouvrir le document source sur cette frappe ? Ctrl+point ou ⌘point.
 *
 * Remplace un bouton, retiré à la demande de Gael (31/10/2026) : on cherche un chemin
 * sans quitter le clavier ni l'endroit où l'on écrit, comme une auto-complétion.
 *
 * L'Espace, posé le même jour, ne peut pas être tenu : macOS prend ⌘Espace pour
 * Spotlight AVANT le navigateur — la frappe n'atteint jamais la page, et aucun code de
 * l'hôte ne la rattrape —, et Ctrl+Espace y change la source de saisie dès qu'il y en a
 * deux. Le point le remplace (Gael, 01/11/2026) : c'est le geste « action rapide » des
 * éditeurs, et le système le laisse passer.
 *
 * Le `code` autant que la `key`, et ici pour une raison précise : sur un clavier AZERTY
 * le point demande Maj, et c'est la MÊME touche physique (`Period`) qui donne « ; »
 * sans Maj. L'accepter par son code sert les deux dispositions sans exiger une Maj qui
 * n'a de sens sur aucune des deux.
 */
export function opensSource(event: Shortcut & { code?: string }): boolean {
  if (event.defaultPrevented === true || !commanded(event)) {
    return false;
  }
  return event.key === "." || event.code === "Period";
}
