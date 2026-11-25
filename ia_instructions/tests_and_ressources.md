# Ressources de test attribuées

Mémoire des ressources disponibles ici et maintenant (les règles d'usage sont
dans la skill `test-machine-deployment`). Mettre à jour à chaque attribution
ou reprise.

| Alias SSH | Nom d'hôte réel | OS | Usage | Attribuée le |
|---|---|---|---|---|
| `test1` | `host-test-37` | Debian 12 (git 2.39.5, HTTP/1.1 forcé vers GitHub via `/etc/git/http2-github.inc`) | machine partagée avec d'autres stacks ; ossie-studio cloné dans `/root/ossie-studio`, projet compose `ossie-studio` | 2026-11-21 |

Ports déjà pris sur `test1` à l'attribution : 1883, 3000, 5355, 5445, 8030,
8080–8082, 8090, 8095, 8100, 8777 — `dev-deploy.sh` choisit des ports libres.
