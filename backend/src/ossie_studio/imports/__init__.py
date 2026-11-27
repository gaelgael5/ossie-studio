"""Module commun d'import de fichiers (enabler 4f32c8c6).

Gael : « On reprend le même système que pour les référentiels. L'idéal serait
de factoriser ce qu'on a fait. » Tout ce qui ne dépend pas d'un domaine vit ici :
déclaration d'un import et ses trois fichiers (`declaration`, `files`), lecture
des fichiers, chemins détectés, essai, transformation ligne à ligne, compte rendu
et tout ou rien (`engine`), moteur JSLT et son profil (`jslt_engine`,
`jslt_profile`, repris de Miles : toute correction reste à reporter des deux
côtés), journal (`journal`).

Ce qui dépend du domaine passe par une **cible** (`target.ImportTarget`) : le
schéma qui valide chaque élément produit, la clé d'un élément, son libellé. Les
référentiels sont la première cible (`referentiels/import_target.py`) ; la
cartographie la deuxième.
"""
