#!/usr/bin/env bash
# Deploy redis-data-downloader (frontend build + backend + systemd) to EC2.
# Usage: ./deploy/deploy.sh [deploy-config.json]
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CONFIG_FILE="${1:-$ROOT/deploy/deploy-config.json}"

if [[ ! -f "$CONFIG_FILE" ]]; then
  echo "Error: config not found: $CONFIG_FILE"
  echo "Copy deploy/deploy-config.example.json to deploy/deploy-config.json and fill it in."
  exit 1
fi

parse_json() {
  local key=$1
  grep -o "\"$key\"[[:space:]]*:[[:space:]]*\"[^\"]*\"" "$CONFIG_FILE" | head -1 | cut -d'"' -f4
}

KEY_PATH=$(parse_json "key_path")
REMOTE_USER=$(parse_json "user")
REMOTE_HOST=$(parse_json "host")
REMOTE_DIR=$(parse_json "remote_dir")
SERVICE_NAME=$(parse_json "service_name")
APP_PORT=$(parse_json "port")
SYSTEMD_USER=$(parse_json "systemd_user")

KEY_PATH="${KEY_PATH/#\~/$HOME}"
SERVICE_NAME=${SERVICE_NAME:-redis-data-downloader}
APP_PORT=${APP_PORT:-8080}
SYSTEMD_USER=${SYSTEMD_USER:-$REMOTE_USER}

if [[ -z "$KEY_PATH" || -z "$REMOTE_USER" || -z "$REMOTE_HOST" || -z "$REMOTE_DIR" ]]; then
  echo "Error: key_path, user, host, remote_dir are required in $CONFIG_FILE"
  exit 1
fi

echo "==> Building frontend (nvm use 20)"
export NVM_DIR="${NVM_DIR:-$HOME/.nvm}"
# shellcheck disable=SC1091
[[ -s "$NVM_DIR/nvm.sh" ]] && . "$NVM_DIR/nvm.sh"
nvm use 20
cd "$ROOT/frontend"
if [[ -f package-lock.json ]]; then
  npm ci
else
  npm install
fi
npm run build

echo "==> Syncing to ${REMOTE_USER}@${REMOTE_HOST}:${REMOTE_DIR}"
ssh -i "$KEY_PATH" -o StrictHostKeyChecking=accept-new "${REMOTE_USER}@${REMOTE_HOST}" \
  "mkdir -p '${REMOTE_DIR}/backend' '${REMOTE_DIR}/frontend' '${REMOTE_DIR}/output' '${REMOTE_DIR}/deploy'"

rsync -avz --delete \
  -e "ssh -i ${KEY_PATH}" \
  --exclude '__pycache__' \
  --exclude '.venv' \
  --exclude '*.pyc' \
  "$ROOT/backend/" "${REMOTE_USER}@${REMOTE_HOST}:${REMOTE_DIR}/backend/"

rsync -avz --delete \
  -e "ssh -i ${KEY_PATH}" \
  "$ROOT/frontend/dist/" "${REMOTE_USER}@${REMOTE_HOST}:${REMOTE_DIR}/frontend/dist/"

if [[ -f "$ROOT/.env" ]]; then
  rsync -avz -e "ssh -i ${KEY_PATH}" \
    "$ROOT/.env" "${REMOTE_USER}@${REMOTE_HOST}:${REMOTE_DIR}/.env"
fi

rsync -avz -e "ssh -i ${KEY_PATH}" \
  "$ROOT/deploy/redis-data-downloader.service" \
  "${REMOTE_USER}@${REMOTE_HOST}:${REMOTE_DIR}/deploy/redis-data-downloader.service"

echo "==> Remote venv + systemd"
ssh -i "$KEY_PATH" "${REMOTE_USER}@${REMOTE_HOST}" bash -s <<EOF
set -euo pipefail
cd "${REMOTE_DIR}"
python3 -m venv .venv
. .venv/bin/activate
pip install -U pip
pip install -r backend/requirements.txt

UNIT_SRC="${REMOTE_DIR}/deploy/redis-data-downloader.service"
UNIT_DST="/tmp/${SERVICE_NAME}.service"
sed -e "s|__REMOTE_DIR__|${REMOTE_DIR}|g" \\
    -e "s|__PORT__|${APP_PORT}|g" \\
    -e "s|__USER__|${SYSTEMD_USER}|g" \\
    "\$UNIT_SRC" > "\$UNIT_DST"

sudo cp "\$UNIT_DST" "/etc/systemd/system/${SERVICE_NAME}.service"
sudo systemctl daemon-reload
sudo systemctl enable "${SERVICE_NAME}"
sudo systemctl restart "${SERVICE_NAME}"
sudo systemctl --no-pager --full status "${SERVICE_NAME}" || true
EOF

echo "==> Deploy complete. Service ${SERVICE_NAME} on port ${APP_PORT}"
