# Session du 28 novembre 2026 — Clés uniques par type, relations « type/CLÉ » (ticket 421832ee)

Ticket docflow **421832ee** (workspace `ossie-studio`, bloc `backlog`). Gael (27/11) :
« On ne peut pas garantir dans les imports que les clefs que nous définissons sont
uniques à travers tous les types. On assure l'unicité uniquement dans un type. »

> **Règle (Gael)** : on pousse **uniquement sur sa branche**. C'est **toujours Gael** qui
> décide quand fusionner sur `dev`. Pas de PR pour ce ticket sans sa demande.

## Décisions de Gael (« 1. oui 2. oui 3. oui 4. oui »)

1. Fiche d'un objet : `/cartographie/objets/<type>/<CLÉ>` (écran et API).
2. Clé seule ambiguë dans une relation : `relation_endpoint_ambiguous`, signalé sur la
   relation, sans bloquer le reste de l'espace.
3. Pas de réécriture en masse : une relation passe en `type/CLÉ` à sa prochaine écriture.
4. Modèles : format inchangé (standard urban-archimate) ; une clé de modèle se lit par
   ses `documents` ; un modèle qui retiendrait deux objets de même clé est refusé
   (`model_key_ambiguous`).

## Ce qui change

- **Identité** `(type, clé)`, écrite `type/CLÉ` : `Cartographie.objects` est indexé ainsi.
  `Cartographie.resolve` lit une désignation `type/CLÉ` ou une clé seule (si elle n'existe
  que dans un type) ; `find`, `ends`, `users`, `end_problem`. Toute modification de
  l'index passe par `Cartographie.put`.
- **Écritures** (`save_object`, `save_objects`, `check_batch`) : clé unique dans son type
  (`object_key_taken` porte le type) ; extrémités écrites `type/CLÉ`. `object_type_frozen`
  ne sert plus (un autre type est un autre objet). `delete_object(root, "type/CLÉ")`.
- **Clé en double** (`object_key_duplicate`) : seulement dans un même type.
- **Modèles** : la boîte à outils porte `type/CLÉ` (le composant le rend tel quel à la
  pose) ; le composant reçoit les extrémités d'une relation en clé seule
  (`diagram.model_document`) ; une relation tracée est écrite en `type/CLÉ`, lue par les
  documents du modèle rendu. L'écran traduit la clé d'un nœud en `type/CLÉ` pour
  « relations permises » (documents hydratés + objets posés).
- **Processus BPMN** : une référence se lit par type et clé, à défaut par la clé seule
  (`process.resolve_reference`) — les fichiers venus d'un autre espace gardent un type
  périmé. Outil en clé seule (le composant la recopie dans `cle`), `type/CLÉ` seulement
  si elle est ambiguë ; l'enregistrement remet alors la clé seule.
- **Imports** : unicité dans le type importé ; comparaison des extrémités après
  résolution (une relation en clé seule, inchangée, reste « inchangée »).
- **MCP** : filtres `source`, `cible`, `contains` acceptent `type/CLÉ` ; messages
  « unique dans son type » et « désigner par type/CLÉ » ; standard `carto-objet`.
- **Écran** : adresse de la fiche, liens, « + Relier… » (source et cible en `type/CLÉ`,
  clé proposée libre dans le type), choix du processus métier détaillé (`type/CLÉ`).
