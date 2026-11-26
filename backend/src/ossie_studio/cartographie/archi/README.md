# Règles ArchiMate 3.2 reprises d'Archi

Fichiers copiés **tels quels** depuis [Archi](https://github.com/archimatetool/archi),
l'outil de modélisation ArchiMate de référence, co-maintenu avec The Open Group.
Licence MIT (`LICENSE.txt`, à conserver avec les fichiers).

- Dépôt : `archimatetool/archi`, dossier `com.archimatetool.model/`
- Commit : `9826e95b764aa1072ddb54b0e5b36835ec5a9bef` (récupéré le 26 novembre 2026)
- `relationships.xml` (`model/relationships.xml`, version 3.2) : pour chaque couple
  (concept source, concept cible), les relations permises par l'annexe des relations
  de la spécification ArchiMate 3.2. Une lettre par relation : a accès, c composition,
  f flux, g agrégation, i affectation, n influence, o association, r réalisation,
  s spécialisation, t déclenchement, v sert. Le concept `Relationship` désigne une
  relation prise comme source ou cible.
- `viewpoints.xml` (`model/viewpoints.xml`) : les points de vue de la
  spécification et les éléments qu'ils autorisent. `$BusinessElements$` (etc.) désigne
  tous les éléments d'une couche ; un point de vue sans élément les autorise tous.
  Dans Archi, la jonction, le regroupement et toutes les relations sont permis dans
  tous les points de vue.

**Ne pas modifier à la main.** Pour mettre à jour : recopier les trois fichiers depuis
un commit plus récent d'Archi, changer le commit ci-dessus, relancer
`tests/test_cartographie_catalog.py`. Lus par `cartographie/rules.py`.
