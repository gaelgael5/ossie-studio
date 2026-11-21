#!/usr/bin/env bash
# Déploiement d'une instance de dev/test par un script unique, idempotent et
# auto-mis-à-jour. Chaque garde vient d'une panne réelle (skill
# « test-machine-deployment ») : n'en retirer aucune sans avoir lu la panne.
#
# Usage :
#   ./dev-deploy.sh [branche]            déploiement ordinaire (défaut : dev)
#   ./dev-deploy.sh --reset-db [branche] SUPPRIME la base puis redéploie
#                                        (confirmation demandée)
#   ./dev-deploy.sh --foreground ...     sans filet anti-raccrochage ni
#                                        journal fichier (CI, débogage)
#
# Prérequis assumés : réseau et accès au dépôt. Il n'existe pas de mode
# « déploie ce qui est déjà là » : le script se met toujours à jour d'abord.
set -euo pipefail

# --- Arguments ---------------------------------------------------------------
FOREGROUND=0
RESET_DB=0
BRANCH="dev"
for arg in "$@"; do
  case "${arg}" in
    --foreground) FOREGROUND=1 ;;
    --reset-db) RESET_DB=1 ;;
    -h|--help) sed -n '2,15p' "${BASH_SOURCE[0]}"; exit 0 ;;
    -*) echo "Option inconnue : ${arg}" >&2; exit 2 ;;
    *) BRANCH="${arg}" ;;
  esac
done
# Arguments transmis tels quels lors de la ré-exécution (étape 2).
ORIG_ARGS=("$@")

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${APP_DIR}/.env"
LOG_DIR="${APP_DIR}/logs"
STATE_DIR="${APP_DIR}/.deploy-state"
# Nom de projet compose fixe : la machine de test héberge plusieurs stacks, et
# un nom dérivé du dossier changerait selon l'endroit du clone (orphelins,
# volumes en double).
export COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-ossie-studio}"
HEALTH_TIMEOUT="${HEALTH_TIMEOUT:-180}"
RUNNING_TIMEOUT="${RUNNING_TIMEOUT:-90}"
PRUNE_INTERVAL_DAYS=7
TOTAL_STEPS=10

cd "${APP_DIR}"
mkdir -p "${LOG_DIR}" "${STATE_DIR}"

step() { echo; echo "[dev-deploy] [$1/${TOTAL_STEPS}] $2"; }
die() { echo "[dev-deploy] ÉCHEC : $*" >&2; exit 1; }
compose() { docker compose --env-file "${ENV_FILE}" "$@"; }

# --- 0. Survivre à la perte de la session ------------------------------------
# Le script arrête la stack qu'il pilote : si le terminal tombe entre l'arrêt et
# le redémarrage, le SIGHUP le tuerait et laisserait la stack à moitié debout.
# On ignore donc SIGHUP (hérité par docker compose) et on journalise tout dans
# un fichier ; tee continue d'écrire le fichier même si le terminal disparaît.
if [ "${FOREGROUND}" -eq 0 ] && [ -z "${DEV_DEPLOY_LOGGING:-}" ]; then
  trap '' HUP
  export DEV_DEPLOY_LOGGING=1
  DEV_DEPLOY_LOG="${LOG_DIR}/dev-deploy-$(date +%Y%m%d-%H%M%S).log"
  export DEV_DEPLOY_LOG
  exec > >(tee -a --output-error=warn-nopipe "${DEV_DEPLOY_LOG}") 2>&1
  echo "[dev-deploy] journal : ${DEV_DEPLOY_LOG}"
fi
[ -n "${DEV_DEPLOY_LOGGING:-}" ] && trap '' HUP

# --- 1. Prérequis -------------------------------------------------------------
step 1 "prérequis"
_need() {
  local bin="$1" hint="$2"
  command -v "${bin}" >/dev/null 2>&1 || die "« ${bin} » introuvable. Installation : ${hint}"
}
_need git "apt-get install -y git"
_need curl "apt-get install -y curl"
_need openssl "apt-get install -y openssl"
_need ss "apt-get install -y iproute2"
_need docker "https://docs.docker.com/engine/install/debian/"
docker compose version >/dev/null 2>&1 \
  || die "plugin « docker compose » (v2) absent. Installation : apt-get install -y docker-compose-plugin"
docker info >/dev/null 2>&1 \
  || die "le démon Docker est injoignable (droits ? lancer en root ou ajouter l'utilisateur au groupe docker)"

# --- 2. Mise à jour depuis le dépôt, puis ré-exécution ------------------------
# Sans ré-exécution, on déploierait du code neuf avec un script, un compose et
# des Dockerfile périmés. Garde anti-boucle : une seule ré-exécution.
step 2 "mise à jour du dépôt (branche ${BRANCH})"
if [ -z "${DEV_DEPLOY_REEXEC:-}" ]; then
  git fetch origin "${BRANCH}"
  BEFORE="$(git rev-parse HEAD)"
  git checkout "${BRANCH}"
  git pull --ff-only origin "${BRANCH}"
  AFTER="$(git rev-parse HEAD)"
  if [ "${BEFORE}" != "${AFTER}" ]; then
    echo "[dev-deploy] dépôt mis à jour (${BEFORE:0:8} → ${AFTER:0:8}), ré-exécution"
    DEV_DEPLOY_REEXEC=1 exec "${APP_DIR}/dev-deploy.sh" "${ORIG_ARGS[@]}"
  fi
fi
echo "[dev-deploy] version déployée : $(git rev-parse --short HEAD) ($(git log -1 --format=%s))"

# --- Mode destructif isolé : remise à zéro de la base -------------------------
# Jamais un effet de bord d'un passage ordinaire : drapeau explicite et
# confirmation tapée. Après la remise à zéro, le déploiement normal reprend.
if [ "${RESET_DB}" -eq 1 ]; then
  echo "[dev-deploy] --reset-db : la base du projet « ${COMPOSE_PROJECT_NAME} » va être SUPPRIMÉE."
  read -r -p "Taper le nom du projet pour confirmer : " answer </dev/tty || answer=""
  [ "${answer}" = "${COMPOSE_PROJECT_NAME}" ] || die "confirmation refusée, rien n'a été supprimé"
  compose down --remove-orphans || true
  docker volume rm "${COMPOSE_PROJECT_NAME}_dbdata" \
    || die "suppression du volume ${COMPOSE_PROJECT_NAME}_dbdata impossible"
  echo "[dev-deploy] base supprimée"
fi

# --- 3. État idempotent : volumes hôte ----------------------------------------
step 3 "volumes hôte"
touch "${ENV_FILE}"
sed -i 's/\r$//' "${ENV_FILE}"
_env_get() { grep -E "^$1=" "${ENV_FILE}" | tail -1 | cut -d= -f2- || true; }
_env_set() {
  if grep -qE "^$1=" "${ENV_FILE}"; then
    sed -i "s|^$1=.*|$1=$2|" "${ENV_FILE}"
  else
    echo "$1=$2" >> "${ENV_FILE}"
  fi
}
DATA_ROOT="$(_env_get DATA_ROOT)"
DATA_ROOT="${DATA_ROOT:-${APP_DIR}/data}"
# Créés s'ils manquent, jamais vidés : ils portent les copies de travail.
mkdir -p "${DATA_ROOT}/work" "${DATA_ROOT}/sources"
# Le backend tourne en uid 1000 (non root) et doit pouvoir écrire.
chown -R 1000:1000 "${DATA_ROOT}/work" "${DATA_ROOT}/sources" 2>/dev/null || true

# --- 4. Configuration : compléter sans écraser --------------------------------
# Un secret régénéré à chaque passage casserait la base (mot de passe) et
# rendrait illisibles les secrets chiffrés (clef maîtresse).
step 4 ".env (secrets manquants uniquement)"
_ensure_secret() {
  local key="$1" value="$2"
  if [ -z "$(_env_get "${key}")" ]; then
    _env_set "${key}" "${value}"
    echo "[dev-deploy] ${key} généré"
  fi
}
_ensure_secret POSTGRES_PASSWORD "$(openssl rand -hex 24)"
_ensure_secret SESSION_SECRET "$(openssl rand -hex 32)"
_ensure_secret SECRETS_MASTER_KEY "$(openssl rand -hex 32)"

# --- 5. Construire, arrêter (orphelins compris), [ports], relancer ------------
step 5 "construction des images"
# La construction se fait depuis la copie du dépôt : c'est elle qui valide que
# l'image se bâtit (fichier non versionné, dépendance manquante…). Un échec
# ici est un défaut du dépôt, pas de la machine. Jamais d'image réutilisée.
compose build

step 6 "arrêt de la stack précédente, puis contrôle des ports"
compose down --remove-orphans

# Sondes APRÈS l'arrêt : avant, notre propre service passerait pour un
# conflit et un port de repli serait persisté à tort.
_port_busy() { ss -ltnH "( sport = :$1 )" | grep -q .; }
_resolve_port() {
  local key="$1" default="$2" port
  port="$(_env_get "${key}")"
  port="${port:-${default}}"
  if _port_busy "${port}"; then
    local candidate=$((port + 1))
    while _port_busy "${candidate}"; do candidate=$((candidate + 1)); done
    echo "[dev-deploy] port ${port} (${key}) pris par un autre service → ${candidate}" >&2
    port="${candidate}"
  fi
  _env_set "${key}" "${port}"
  echo "${port}"
}
BACKEND_DEV_PORT="$(_resolve_port BACKEND_DEV_PORT 8000)"
FRONTEND_DEV_PORT="$(_resolve_port FRONTEND_DEV_PORT 8080)"
POSTGRES_DEV_PORT="$(_resolve_port POSTGRES_DEV_PORT 5432)"
echo "[dev-deploy] ports : backend ${BACKEND_DEV_PORT}, frontend ${FRONTEND_DEV_PORT}, postgres ${POSTGRES_DEV_PORT}"

compose up -d

# --- 7. Conteneur backend réellement en marche --------------------------------
# Juste après le lancement il peut redémarrer en boucle (migration en échec,
# config invalide). Les migrations sont jouées par le backend à son démarrage.
step 7 "attente d'un backend stable (max ${RUNNING_TIMEOUT}s)"
deadline=$(( $(date +%s) + RUNNING_TIMEOUT ))
while :; do
  cid="$(compose ps -q backend)"
  state="$( [ -n "${cid}" ] && docker inspect -f '{{.State.Status}} {{.RestartCount}}' "${cid}" 2>/dev/null || echo "absent 0")"
  # « running » à un instant ne suffit pas : un processus qui meurt au
  # démarrage est vu running une fraction de seconde. On exige qu'il le soit
  # encore 5 s plus tard, sans redémarrage entre-temps.
  if [ "${state%% *}" = "running" ]; then
    sleep 5
    again="$(docker inspect -f '{{.State.Status}} {{.RestartCount}}' "${cid}" 2>/dev/null || echo "absent 0")"
    [ "${again}" = "${state}" ] && break
    state="${again}"
  fi
  if [ "${state%% *}" = "exited" ] || [ "$(date +%s)" -ge "${deadline}" ]; then
    compose logs --tail 80 backend >&2 || true
    die "le backend n'est pas en marche (état : ${state}). Diagnostic : docker compose -p ${COMPOSE_PROJECT_NAME} logs backend"
  fi
  sleep 2
done

# --- 8 + 9. Schéma à jour et santé, via /api/health ---------------------------
# /api/health ne répond 200 que si la base est joignable, le schéma à la
# dernière version de migration ET les deux volumes inscriptibles ; sinon 503
# avec le détail. On vérifie le résultat, pas un code de retour de commande.
step 8 "schéma et santé : /api/health sur le port ${BACKEND_DEV_PORT} (max ${HEALTH_TIMEOUT}s)"
deadline=$(( $(date +%s) + HEALTH_TIMEOUT ))
body=""
until body="$(curl -fsS "http://localhost:${BACKEND_DEV_PORT}/api/health" 2>/dev/null)"; do
  if [ "$(date +%s)" -ge "${deadline}" ]; then
    echo "[dev-deploy] dernière réponse : $(curl -sS "http://localhost:${BACKEND_DEV_PORT}/api/health" 2>&1 || true)" >&2
    compose logs --tail 80 backend >&2 || true
    die "/api/health n'a pas répondu 200 dans le délai (schéma en retard, base ou volume en défaut)"
  fi
  sleep 3
done
echo "[dev-deploy] santé : ${body}"

step 9 "frontend sur le port ${FRONTEND_DEV_PORT}"
# Le frontend relaie /api vers le backend : on vérifie la chaîne complète.
curl -fsS "http://localhost:${FRONTEND_DEV_PORT}/api/health" >/dev/null \
  || die "le frontend ne relaie pas /api/health (port ${FRONTEND_DEV_PORT}). Diagnostic : docker compose -p ${COMPOSE_PROJECT_NAME} logs frontend"
echo "[dev-deploy] OK : instance en ligne — frontend :${FRONTEND_DEV_PORT}, backend :${BACKEND_DEV_PORT}"

# --- 10. Récupération d'espace disque, au plus une fois par semaine -----------
# Chaque déploiement laisse des couches détaggées que rien ne récupère. Bornes :
# fréquence (témoin horodaté), position (après la santé), portée (cache de
# build + images détaggées, jamais les volumes ni une purge globale), et un
# échec de purge ne fait jamais échouer le déploiement.
step 10 "purge disque hebdomadaire"
STAMP="${STATE_DIR}/last-prune"
if [ -f "${STAMP}" ] && [ -n "$(find "${STAMP}" -mtime -"${PRUNE_INTERVAL_DAYS}" 2>/dev/null)" ]; then
  echo "[dev-deploy] dernière purge le $(date -r "${STAMP}" '+%F %T') : rien à faire"
else
  {
    echo "[dev-deploy] cache de construction : $(docker builder prune -f 2>&1 | tail -1)"
    echo "[dev-deploy] images détaggées : $(docker image prune -f 2>&1 | tail -1)"
    touch "${STAMP}"
  } || echo "[dev-deploy] purge en échec (ignoré, le déploiement reste bon)"
fi

echo
echo "[dev-deploy] terminé."
