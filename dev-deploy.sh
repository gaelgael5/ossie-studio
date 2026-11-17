#!/usr/bin/env bash
# Déploiement d'une instance de dev/test par un script unique idempotent et
# auto-mis-à-jour, conforme au STANDARD « Instance de dev/test » (globals).
#
# Étapes : repo (pull + ré-exécution si le dépôt a changé) → complétion du .env
# (secrets manquants générés, existants préservés) → build + up → migrations
# (jouées au démarrage du backend par le runner maison) → smoke /api/health.
#
# Usage : ./dev-deploy.sh [branche]
set -euo pipefail

BRANCH="${1:-dev}"
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${APP_DIR}/.env"
DATA_ROOT="${DATA_ROOT:-${APP_DIR}/data}"
BACKEND_DEV_PORT="${BACKEND_DEV_PORT:-8000}"
HEALTH_TIMEOUT="${HEALTH_TIMEOUT:-120}"

cd "${APP_DIR}"

# 1. Repo : se mettre à jour, puis se ré-exécuter dans la version fraîche (une
#    seule fois, garde anti-boucle) pour que le script, le compose et les
#    Dockerfile tournent toujours dans leur version courante.
if [ -z "${DEV_DEPLOY_REEXEC:-}" ]; then
  git fetch origin "${BRANCH}"
  BEFORE="$(git rev-parse HEAD)"
  git checkout "${BRANCH}"
  git pull --ff-only origin "${BRANCH}"
  AFTER="$(git rev-parse HEAD)"
  if [ "${BEFORE}" != "${AFTER}" ]; then
    echo "[dev-deploy] dépôt mis à jour (${BEFORE:0:8} → ${AFTER:0:8}), ré-exécution"
    DEV_DEPLOY_REEXEC=1 exec "${APP_DIR}/dev-deploy.sh" "${BRANCH}"
  fi
fi

# 2. Volumes hôte : créés s'ils manquent, jamais vidés par une ré-exécution.
mkdir -p "${DATA_ROOT}/work" "${DATA_ROOT}/sources"
# L'utilisateur du conteneur backend est uid 1000 (non root) : il doit pouvoir
# écrire dans les volumes.
chown -R 1000:1000 "${DATA_ROOT}/work" "${DATA_ROOT}/sources" 2>/dev/null || true

# 3. .env : générer seulement les secrets manquants, ne jamais écraser un existant.
touch "${ENV_FILE}"
_ensure_secret() {
  local key="$1" value="$2"
  if ! grep -q "^${key}=" "${ENV_FILE}"; then
    echo "${key}=${value}" >> "${ENV_FILE}"
    echo "[dev-deploy] ${key} généré"
  fi
}
_ensure_secret POSTGRES_PASSWORD "$(openssl rand -hex 24)"
_ensure_secret SESSION_SECRET "$(openssl rand -hex 32)"
_ensure_secret SECRETS_MASTER_KEY "$(openssl rand -hex 32)"
# Normalise d'éventuelles fins de ligne CRLF.
sed -i 's/\r$//' "${ENV_FILE}"

# 4. Build + up. Les migrations sont jouées par le backend à son démarrage
#    (runner maison, chaque migration dans sa transaction).
docker compose --env-file "${ENV_FILE}" up -d --build

# 5. Smoke : attente active de /api/health (plafond de temps). Un échec = un
#    déploiement raté, jamais un 200 de complaisance.
echo "[dev-deploy] attente de /api/health (max ${HEALTH_TIMEOUT}s)"
deadline=$(( $(date +%s) + HEALTH_TIMEOUT ))
until curl -fsS "http://localhost:${BACKEND_DEV_PORT}/api/health" >/dev/null 2>&1; do
  if [ "$(date +%s)" -ge "${deadline}" ]; then
    echo "[dev-deploy] ÉCHEC : /api/health n'a pas répondu 200 dans le délai" >&2
    docker compose --env-file "${ENV_FILE}" logs --tail 50 backend >&2 || true
    exit 1
  fi
  sleep 3
done
echo "[dev-deploy] OK : instance en ligne sur le port ${BACKEND_DEV_PORT}"
