#!/usr/bin/env bash
# On-server deploy for redis-data-downloader.
# Run after: git clone, then cp .env.example .env (and fill secrets).
#
# Usage (on EC2, from repo root):
#   chmod +x deploy/deploy.sh
#   ./deploy/deploy.sh
#   ./deploy/deploy.sh deploy/deploy-config.json   # optional overrides
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CONFIG_FILE="${1:-$ROOT/deploy/deploy-config.json}"

parse_json() {
  local key=$1
  [[ -f "$CONFIG_FILE" ]] || return 0
  grep -o "\"$key\"[[:space:]]*:[[:space:]]*\"[^\"]*\"" "$CONFIG_FILE" 2>/dev/null \
    | head -1 | cut -d'"' -f4 || true
}

ensure_node() {
  if command -v npm >/dev/null 2>&1 && command -v node >/dev/null 2>&1; then
    echo "    node $(node -v) / npm $(npm -v)"
    return 0
  fi

  echo "==> Installing Node.js 20 (system packages, no nvm)"
  if ! command -v curl >/dev/null 2>&1; then
    sudo apt-get update -y
    sudo apt-get install -y curl ca-certificates
  fi
  curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
  sudo apt-get install -y nodejs
  echo "    node $(node -v) / npm $(npm -v)"
}

SERVICE_NAME=$(parse_json "service_name")
APP_PORT=$(parse_json "port")
SYSTEMD_USER=$(parse_json "systemd_user")

SERVICE_NAME=${SERVICE_NAME:-redis-data-downloader}
APP_PORT=${APP_PORT:-8080}
SYSTEMD_USER=${SYSTEMD_USER:-$(whoami)}

if [[ ! -f "$ROOT/.env" ]]; then
  echo "Error: $ROOT/.env not found."
  echo "  cp .env.example .env   # or: cp backend/.env.example .env"
  echo "  then edit APP_PASSWORD, APP_JWT_SECRET, DB, and Redis values."
  exit 1
fi

mkdir -p "$ROOT/output"

echo "==> Building frontend"
ensure_node

cd "$ROOT/frontend"
if [[ -f package-lock.json ]]; then
  npm ci
else
  npm install
fi
npm run build

echo "==> Python venv + dependencies"
cd "$ROOT"
python3 -m venv .venv
# shellcheck disable=SC1091
. .venv/bin/activate
pip install -U pip
pip install -r backend/requirements.txt

echo "==> Installing systemd unit (${SERVICE_NAME})"
UNIT_SRC="$ROOT/deploy/redis-data-downloader.service"
UNIT_DST="/tmp/${SERVICE_NAME}.service"
sed -e "s|__APP_DIR__|${ROOT}|g" \
    -e "s|__PORT__|${APP_PORT}|g" \
    -e "s|__USER__|${SYSTEMD_USER}|g" \
    "$UNIT_SRC" > "$UNIT_DST"

sudo cp "$UNIT_DST" "/etc/systemd/system/${SERVICE_NAME}.service"
sudo systemctl daemon-reload
sudo systemctl enable "${SERVICE_NAME}"
sudo systemctl restart "${SERVICE_NAME}"
sudo systemctl --no-pager --full status "${SERVICE_NAME}" || true

echo "==> Deploy complete"
echo "    App dir:  $ROOT"
echo "    Service:  ${SERVICE_NAME}"
echo "    Listen:   0.0.0.0:${APP_PORT}"
echo "    Open:     http://<ec2-public-ip>:${APP_PORT}"
