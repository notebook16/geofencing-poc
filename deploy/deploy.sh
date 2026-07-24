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

ensure_uv() {
  export PATH="${HOME}/.local/bin:${PATH}"
  if command -v uv >/dev/null 2>&1; then
    return 0
  fi
  echo "==> Installing uv (to provision Python 3.12)"
  if ! command -v curl >/dev/null 2>&1; then
    sudo apt-get update -y
    sudo apt-get install -y curl ca-certificates
  fi
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="${HOME}/.local/bin:${PATH}"
  command -v uv >/dev/null 2>&1
}

# Drop venvs built with Python >= 3.14 (pydantic-core / PyO3 break there).
drop_incompatible_venv() {
  if [[ ! -d "$ROOT/.venv" ]]; then
    return 0
  fi
  local vpy="$ROOT/.venv/bin/python"
  if [[ ! -x "$vpy" ]]; then
    rm -rf "$ROOT/.venv"
    return 0
  fi
  local major minor
  major="$("$vpy" -c 'import sys; print(sys.version_info.major)')"
  minor="$("$vpy" -c 'import sys; print(sys.version_info.minor)')"
  if [[ "$major" -gt 3 || ( "$major" -eq 3 && "$minor" -ge 14 ) ]]; then
    echo "    removing incompatible .venv (Python ${major}.${minor})"
    rm -rf "$ROOT/.venv"
  fi
}

# Ubuntu Resolute (26.04) only ships 3.14; deadsnakes may have 3.13 but not 3.12.
# Prefer any 3.11–3.13 on PATH, else try apt 3.13, else uv-managed 3.12.
ensure_python_venv() {
  drop_incompatible_venv

  local candidate
  for candidate in python3.13 python3.12 python3.11; do
    if command -v "$candidate" >/dev/null 2>&1; then
      echo "    using $($candidate --version)"
      if [[ ! -d "$ROOT/.venv" ]]; then
        "$candidate" -m venv "$ROOT/.venv"
      fi
      return 0
    fi
  done

  echo "==> Trying apt python3.13 (deadsnakes / distro)"
  sudo apt-get update -y >&2 || true
  if sudo apt-get install -y python3.13 python3.13-venv python3.13-dev >&2; then
    echo "    using $(python3.13 --version)"
    python3.13 -m venv "$ROOT/.venv"
    return 0
  fi

  echo "==> Falling back to uv-managed Python 3.12"
  ensure_uv
  uv python install 3.12
  rm -rf "$ROOT/.venv"
  uv venv --python 3.12 "$ROOT/.venv"
  echo "    using $($ROOT/.venv/bin/python --version)"
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
ensure_python_venv
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
