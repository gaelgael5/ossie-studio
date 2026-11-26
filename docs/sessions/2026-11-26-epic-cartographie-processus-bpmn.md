# Session du 26 novembre 2026 — Epic « Cartographie — processus BPMN » (développement)

Epic docflow **f97e7afe** (workspace `ossie-studio`, bloc `backlog`) : décrire en BPMN
le détail d'un processus métier, en rattachant ses participants, couloirs et données aux
objets de la cartographie. Composant d'édition : `urban-bpmn` (dépôt et backlog à part,
tous ses tickets livrés).

> **Règle (Gael)** : on pousse **uniquement sur sa branche**. C'est **toujours Gael** qui décide quand fusionner sur `dev`.

## Décisions de Gael (26 novembre)

- On travaille le backlog d'**Ossie** ; tickets 1 et 2 sans attendre le composant.
- **Publication d'`urban-bpmn`** : Gael publie (l'étiquette `v.0.1.1` portait un point en
  trop : la publication automatique comparait « .0.1.1 » à « 0.1.1 » et s'arrêtait).
- Vérification d'un fichier côté serveur : **de structure** (XML lisible, BPMN 2.0,
  dessin, références) ; le message détaillé de bpmn-moddle vient du composant à
  l'ouverture (bpmn-moddle n'existe qu'en JavaScript).

## Tickets

| Ticket | Sujet | Statut |
|---|---|---|
| 1d35290d | Format et rangement d'un processus BPMN | en revue |
| 7e061c3e | Processus : liste, création, import | à faire |
| 48335b93 | Ouvrir et enregistrer un processus | attend `urban-bpmn` sur PyPI |
| 883ca50f | Vérification d'intention (avec Gael) | à faire |

## Notes techniques (format, 1d35290d)

- `cartographie/process.py`. Lecture `defusedxml` ; écriture par retouches ciblées du
  texte (pas de réécriture par ElementTree, qui perdrait préfixes et commentaires ; pas de
  `lxml` dans le projet).
- Balise de l'extension : celle du contrat d'urban-bpmn (`urban:reference`, `src/moddle/
  urban.json`), et non `urb:ref` / `urb:detailDe` de l'exemple du ticket (latitude :
  « à condition qu'ils soient ceux du contrat »). Sur `bpmn:process`, la même balise dit
  le processus métier détaillé.
- Les noms à jour sont appliqués par le composant (`LoadOptions.references`) : Ossie ne
  réécrit pas le XML à l'ouverture.
- À l'import, l'`id` du document devient la clé (seule retouche) : ticket 1 (« la clé est
  aussi l'id du bpmn:definitions ») et ticket 2 (« gardé tel quel ») concilient ainsi.
