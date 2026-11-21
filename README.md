# ossie-studio

Construction d'une couche sémantique (Apache Ossie) à partir de sources
extraites (Snowflake, dépôts Git) : collecte brute, curation des assets,
relations, ontologie.

Le backlog du projet vit dans docflow, workspace `ossie-studio`.

## Pile

- **Backend** : Python 3.12+, FastAPI, asyncpg (SQL brut, requêtes
  paramétrées), pydantic v2 (`extra="forbid"`), structlog, géré avec uv sous
  `backend/`. Migrations en **fichiers SQL numérotés immuables** joués par un
  runner maison (table de suivi, empreintes). Pas d'ORM.
- **Frontend** : React + Vite, TypeScript strict, TanStack Query, i18n
  (fr/en), Vitest, sous `frontend/`. Identité visuelle et règles d'écriture
  d'une page : [docs/identite-visuelle.md](docs/identite-visuelle.md).
- **Livraison** : `docker compose` (PostgreSQL, backend, frontend) et
  `dev-deploy.sh` idempotent.

## Deux volumes mappés

| Volume | Contenu | Si perdu |
|---|---|---|
| **travail** (`WORK_VOLUME_PATH`) | copies de travail des espaces Git, versionnées | seuls les commits non publiés sont perdus |
| **sources** (`SOURCES_VOLUME_PATH`) | code descendu des sources, jamais versionné | rien de définitif : tout se re-descend |

Les deux chemins sont obligatoires : sans eux, le backend refuse de démarrer.
Ils vivent hors de l'image et de la couche conteneur.

## Déploiement (dev/test)

Première fois sur la machine : `git clone -b dev https://github.com/gaelgael5/ossie-studio.git`.
Ensuite, toujours et uniquement :

```bash
./dev-deploy.sh            # branche dev
./dev-deploy.sh --reset-db # SUPPRIME la base puis redéploie (confirmation)
./dev-deploy.sh --foreground  # sans filet anti-raccrochage ni journal (CI)
```

Le script est idempotent et se relance sans danger après un échec partiel :
mise à jour du dépôt puis ré-exécution dans sa version fraîche, secrets
manquants générés dans `.env` (jamais ceux déjà présents), construction des
images, arrêt avec retrait des orphelins, choix de ports libres (sondés après
l'arrêt, persistés dans `.env`), attente d'un backend stable, puis
`GET /api/health` — qui n'est vert que si le schéma est à la dernière
migration et les volumes inscriptibles — et relais `/api` du frontend. Purge
disque (cache de build, images détaggées) au plus une fois par semaine.
Journal de chaque passage : `logs/dev-deploy-*.log` ; le déploiement va au
bout même si la session SSH tombe.

## Développement local (sans Docker)

Prérequis : [uv](https://docs.astral.sh/uv/), Node 22, un PostgreSQL joignable.

```bash
cd backend && cp .env.example .env
# renseigner SESSION_SECRET et SECRETS_MASTER_KEY : openssl rand -hex 32
```

```bash
cd backend && uv run uvicorn ossie_studio.app:create_app --factory --reload
```

```bash
cd frontend && npm install && npm run dev
```

## Internationalisation (i18n)

L'interface s'affiche en **français** ou en **anglais** (i18next +
react-i18next, traductions embarquées). La langue se résout dans l'ordre :
choix mémorisé (localStorage) → langue du navigateur → anglais. Un sélecteur
de langue est présent sur la page, change la langue sans recharger. Les
dates suivent la langue courante (`Intl`).

Le **backend ne rédige aucun message affiché** : il renvoie des codes
d'erreur stables (`{"error": "<code>", "error_args": {...}}`) que l'interface
traduit. La liste des codes est partagée (`frontend/src/errorCodes.json`),
et des tests garantissent que chaque code a une traduction FR et EN.

Deux garde-fous CI : une chaîne visible écrite en dur dans un composant fait
échouer `eslint` (`i18next/no-literal-string`), et une clé présente dans une
langue mais absente de l'autre fait échouer les tests.

## Contrôle de santé

`GET /api/health` interroge réellement la base (et la version de schéma) et
les deux volumes : **200** quand tout répond et que les volumes sont
inscriptibles, **503** sinon, en nommant ce qui manque. Jamais de 200 de
complaisance.

## Architecture

Le backend est une **API JSON** (`/api/*`) sans rendu de pages ; toute
l'interface est une **SPA React** (sous `frontend/`) qui la consomme, plus
les redirections du flux OIDC (`/auth/oidc/*`). Le navigateur ne voit
qu'une origine : nginx (ou le proxy Vite en dev) relaie `/api` et `/auth`
vers le backend.

## Accès et administration

Le SPA route selon `GET /api/session` (`bootstrap_required`,
`authenticated`, `user`). Au premier démarrage sur une base vide, il mène à
l'écran de création du **premier administrateur**. Dès que ce compte existe,
cet écran n'est plus accessible. L'administrateur gère ensuite les
**secrets** (chiffrés AES-256-GCM), les **connecteurs OIDC** et les
**forges et connexions** (GitHub, Azure DevOps, GitLab) depuis l'interface.
Tous les écrans passent par i18n ; les erreurs viennent de l'API sous forme
de codes traduits côté interface.

### Profils et capacités

L'accès aux fonctions passe par des **profils**. Un profil est un **jeu de
capacités** (cases indépendantes, **jamais une échelle** : on peut publier sans
pouvoir modifier). Le **catalogue des capacités** est déclaré dans le code,
groupé par domaine (Secrets, Forges, OIDC, Espaces Git, Gouvernance, Sources,
Curation, Profils, Utilisateurs), chaque capacité avec un identifiant stable et
un libellé traduit. Chaque utilisateur a **un seul profil**. Le profil
**Administrateur** est livré d'office, a **toutes** les capacités (y compris
celles ajoutées plus tard), et ne se supprime ni ne se modifie ; le premier
administrateur le reçoit. Un profil est **marqué par défaut** (un seul à la
fois) : tout compte créé à la première connexion OIDC le reçoit. À
l'installation, c'est un profil **« Sans accès »** (aucune capacité, jamais
l'Administrateur) — un annuaire ouvert ne donne ainsi aucun droit d'office ;
l'administrateur choisit ensuite. Changer le profil par défaut ne touche pas
les comptes existants, et le profil marqué ne se supprime pas.

La page **Utilisateurs** (capacité « Utilisateurs : consulter ») liste les
comptes **déjà connus** (ceux qui se sont connectés, plus le premier
administrateur ; l'application n'interroge jamais l'annuaire de l'IdP). On
**cherche par nom ou email** (insensible à la casse et aux accents, via une clé
de recherche normalisée, sans extension Postgres), on **filtre par profil**
(lien depuis la page Profils), et on **assigne un profil** (capacité
« Utilisateurs : modifier ») — effectif dès la requête suivante de la personne.
Le nom d'affichage et la date de dernière connexion viennent des connexions
(nom capté des claims OIDC). **Anti-verrouillage** : retirer le profil
Administrateur au dernier administrateur actif est refusé. Chaque assignation
est journalisée (auteur, utilisateur, ancien et nouveau profil).

**Rôles OIDC → profil.** Chaque connecteur OIDC peut déclarer le **chemin du
claim** portant les rôles (`groups`, `realm_access.roles`) et une liste
**ordonnée** de correspondances *valeur de rôle → profil*. À chaque connexion,
la **première correspondance qui s'applique** choisit le profil (comparaison
**exacte** ; les rôles du jeton **choisissent** un profil, ils n'accordent jamais
une capacité). La lecture du claim est un point d'adaptation isolé qui accepte
deux formes — **tableau de chaînes** (Keycloak) et **objet à clés** (Zitadel) ;
toute autre forme ne donne aucun rôle (avertissement journalisé, jamais de
plantage). Un profil **fixé à la main** depuis la page Utilisateurs n'est plus
remplacé par les correspondances (la page permet de retirer la marque), et une
correspondance ne peut jamais retirer l'Administrateur au dernier administrateur
actif. Supprimer un profil visé par une correspondance est refusé.

**Le serveur fait foi** : chaque route vérifie la capacité qu'elle demande
(403 sinon), les droits **relus à chaque requête** (un changement de profil vaut
dès la requête suivante, sans reconnexion). L'écran masque ce qu'on ne peut pas
faire (nav et routes selon les capacités de la session), mais ce masquage n'est
jamais la protection. La page **Profils** (capacité « Profils : modifier »)
liste, crée, duplique, modifie et supprime les profils via une grille de cases
par domaine ; supprimer un profil assigné est refusé (avec le nombre
d'utilisateurs). Dans les autres écrans, « réservé à l'administrateur » se lit
désormais « réservé aux profils qui ont la capacité correspondante ».

### Forges et connexions

Une connexion à une forge Git (par jeton d'accès) est vérifiée **avant**
d'être enregistrée (appel réel à la forge) ; le jeton est stocké comme
secret chiffré (catégorie = forge), la connexion n'en garde que la
référence. Chaque forge est une **stratégie** derrière un même contrat
(`ForgeStrategy`) : vérifier l'identité, lister les dépôts lisibles (avec
pagination). Ajouter une forge ou un mode OAuth plus tard ne touche ni la
page ni les consommateurs.

### Espaces Git

Un **espace Git** rattache un dépôt (sous une connexion) où Ossie Studio
pourra écrire, pousser et taguer. La création **éprouve réellement les cinq
droits** par un essai git — lire/récupérer, créer une branche, pousser un
commit, poser un tag, supprimer la branche — avec une branche et un tag de
vérification `ossie-verify-<aléatoire>`, puis nettoie. Sur un dépôt vide,
`main` est posée (seul effet durable, annoncé). Un droit en échec nomme le
droit et sa cause ; un nettoyage raté affiche le nom exact à supprimer à la
main. Le jeton est remis à git par l'environnement (`GIT_ASKPASS`), jamais
dans l'URL, un argument ou un fichier. Supprimer une connexion utilisée par
un espace est refusé. La sonde est injectable, testée contre des dépôts bare
locaux ; l'épreuve contre les vraies forges (ou une Gitea jetable) est une
dépendance humaine (dépôts et jetons fournis hors conversation).

**Branches de travail** : chaque espace a un écran qui gère ses branches. La
**racine du dépôt reste en lecture seule** ; Ossie Studio n'écrit que dans
des branches `work/<slug>`. La **base** se tire de la racine (avec une
arborescence initiale `sources/` + description si absente) ; une **fille** se
tire de la tête de la base (pointeur, sans commit). Les copies de travail
sont persistantes, un répertoire par (espace, branche) sur le volume de
travail, réalignées sur la tête distante à chaque geste, avec un verrou par
répertoire. La **branche regardée est retenue par compte**.

**Commits et publication** : écrire dans une branche de travail produit un
**commit local** (auteur = le compte déclencheur, message préfixé `ossie: `) —
jamais de commit vide, sans pousser. La copie **ne se réaligne sur la forge que
s'il n'y a aucun commit en attente** : les commits non publiés ne sont jamais
écrasés. L'écran liste les **commits non publiés** de la branche regardée et
propose de les **publier** (push sans force). Si la branche a avancé sur la
forge entre-temps, la publication est **refusée (« la branche a changé »)** et
rien n'est perdu localement : on actualise puis on republie.

**Versions** : une version fige l'état **publié** d'une branche en un **tag
annoté `version/<n>`** posé sur sa tête (libellé, auteur, date ; aucun commit
ajouté). Les **numéros sont uniques pour tout le dépôt** ; chaque branche liste
et restaure les versions de **sa lignée**. L'action est **explicite**, jamais
automatique : refus s'il reste des commits non publiés (« publication
requise »), si rien n'a changé depuis la dernière version (« rien à figer »),
ou si le numéro a été pris entre-temps (**tag poussé sans force**, pose
atomique). Les tags `version/*` posés à la main sont lus s'ils respectent le
format, ignorés sinon ; le numéro suivant suit le plus grand du dépôt.
**Revenir** à une version crée un **nouveau commit** (« Retour à la version N »)
qui remet son arbre, à publier : l'historique et les versions suivantes
restent, rien n'est réécrit.

**Diagramme « réseau ferroviaire »** : une vue plan-de-métro de l'histoire du
dépôt — chaque branche est une **ligne** (couleur **et** forme, lisible en
daltonisme), les commits des **arrêts**, les versions des **gares** (numéro +
libellé), la création d'une branche un **aiguillage**, une fusion une
**jonction**. Le **placement des voies est calculé côté serveur** (module pur,
déterministe, testé sans navigateur) à partir des données git ; le navigateur
ne fait que dessiner un SVG. Seules les branches de travail de l'espace et la
racine sont tracées ; une branche supprimée sur la forge n'est plus une ligne,
mais ses commits restés accessibles demeurent visibles. Survol pour le détail,
clic sur une gare pour sélectionner la version, filtre « versions seulement »,
arrêts non publiés en pointillé, et chargement « plus ancien » (≈ 200 commits à
la fois). La liste des versions sert d'équivalent accessible (clavier, lecteur
d'écran).

## Gouvernance des données

Le principe est repris d'Entropy Data (tags, certifications, classifications).

### Tags — vocabulaire gouverné

La page **Gouvernance › Tags** tient un **vocabulaire de tags gérés** :
chaque tag a un **id** hiérarchique (séparateur `/`, les niveaux parents n'ont
pas besoin d'exister), une description, des liens nommés, des propriétés et une
**équipe propriétaire informative** (elle ne donne aucun droit). L'id est
**insensible à la casse**, normalisé en minuscules (`Gouvernance/PII` et
`gouvernance/pii` sont le même tag). Le vocabulaire **s'exporte et s'importe en
YAML**. Chaque création, modification et suppression est journalisée avec son
auteur. Les définitions vivent en base de l'installation.

**Pour l'instant**, seule la gestion du **vocabulaire** est livrée, réservée à
l'administrateur. La **pose des tags sur les assets** (écriture de `x-tags`,
vue « tags non gérés », usages, refus de suppression d'un tag utilisé) arrivera
avec les **assets** (epic « Curation »), qui n'existent pas encore ; de même,
les **capacités** fines (« Gouvernance : modifier ») remplaceront la garde
administrateur quand l'epic « Profils » sera là.

### Certifications — niveaux de qualité

La page **Gouvernance › Certifications** définit des **niveaux** (Or, Argent,
Bronze…). Chaque niveau a un **rang** (le plus bas en premier, Or = 1), une
description, des **critères** en Markdown, une **icône**, une option
**restreinte**, et un **tag** (ex. `certification/or`) qui le reliera aux
assets. Un bouton crée les **trois niveaux par défaut** (idempotent),
modifiables ensuite.

**Pour l'instant**, seule la **gestion des niveaux** est livrée (admin).
L'**assignation d'un niveau à un asset** (écriture du tag de certification dans
`x-tags`, remplacement du niveau précédent, badge, filtre, refus de suppression
d'un niveau assigné, détection d'incohérence) arrivera avec les **assets** ;
l'assignation d'un niveau **restreint** demandera la capacité « Gouvernance :
certifier » (epic « Profils »).

### Classifications — schémas de sensibilité

La page **Gouvernance › Classifications** gère des **schémas** de niveaux de
sensibilité. Un schéma a un **id dérivé de son nom et immuable** (clé dans les
assets), un préfixe de badge, l'option **multi-valeurs** (un ensemble de
valeurs, jamais principal) et peut être le **schéma principal** (un seul, garanti
par un index unique partiel). Un **niveau** porte un **nom canonique** (valeur
stable stockée dans les assets, unique, sans virgule), un **libellé d'affichage**
traduisible, un nom court (≤ 12), un rang (le plus élevé l'emporte à la
dérivation), des critères Markdown, une couleur et une icône. On crée un schéma
vide, **depuis un preset** (Classification, RGPD, RGPD art. 9 multi-valeurs,
TLP 2.0 — libellés résolus selon la langue à l'import) ou **par import YAML**, et
on **exporte** un schéma en YAML. Sur une **installation neuve**, le schéma
« Classification » existe déjà, principal, avec ses quatre niveaux.

**Pour l'instant**, seule la **gestion des schémas et niveaux** est livrée
(admin). La **pose des classifications sur les champs/assets** et la
**dérivation** (réécriture des assets au renommage d'un niveau, invalidation à
la suppression d'un niveau utilisé, compteurs d'usage) arriveront avec les
**assets** (epic « Curation »).

## Tests et qualité

```bash
cd backend && uv run pytest -v        # nécessite un PostgreSQL (base jetable par test)
cd backend && uv run ruff check src/ tests/ && uv run mypy
cd frontend && npm run typecheck && npm run lint && npm test
```

Les tests backend créent une base jetable par test ; régler
`OSSIE_TEST_ADMIN_DSN` pour pointer un PostgreSQL d'admin (défaut :
`postgresql://ossie:ossie@127.0.0.1:5432/postgres`).
