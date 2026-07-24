# redis-data-downloader

Export battery/IMEI Redis telemetry (+ Postgres center fields) to Excel via a FastAPI backend and React UI.

## Layout

```
redis-data-downloader/
  backend/     FastAPI app (auth, fields, export, history)
  frontend/    Vite + React UI
  deploy/      One EC2 deploy script + systemd unit
  output/      Generated XLSX + run_history.xlsx
```

Exports are saved as `output/{username}_{YYYYMMDD_HHMMSS}.xlsx` — a new file every run (never overwrites).

## Auth

Shared password in env (`APP_PASSWORD`). Username is free-text and is logged on each export run. JWT is issued with `APP_JWT_SECRET`.

## Local setup

### Backend

```bash
cd redis-data-downloader
python3 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt
cp backend/.env.example .env   # fill APP_PASSWORD, APP_JWT_SECRET, DB, Redis
```

Run API (serves `frontend/dist` when built):

```bash
source .venv/bin/activate
cd backend
uvicorn app.main:app --reload --host 0.0.0.0 --port 8080
```

### Frontend (dev)

```bash
export NVM_DIR="$HOME/.nvm" && . "$NVM_DIR/nvm.sh" && nvm use 20
cd frontend
npm install
npm run dev
```

Vite proxies `/api` to `http://127.0.0.1:8080`.

### Production-style local (one port)

```bash
nvm use 20
cd frontend && npm run build && cd ..
source .venv/bin/activate
cd backend && uvicorn app.main:app --host 0.0.0.0 --port 8080
```

Open `http://127.0.0.1:8080`.

## UI flow

1. Sign in (username + password)
2. Select Redis/Postgres fields (hardcoded schema)
3. All IMEIs or paste specific IMEIs
4. Download XLSX (`data` + `run_info` sheets) named `{username}_{timestamp}.xlsx`
5. Run history shows username and start time

## Deploy on EC2 (clone → env → systemd)

On the server:

```bash
git clone <your-repo-url> redis-data-downloader
cd redis-data-downloader

cp .env.example .env
# edit .env: APP_PASSWORD, APP_JWT_SECRET, DB_POSTGRES_*, REDIS_*

# optional: override port / service user
cp deploy/deploy-config.example.json deploy/deploy-config.json
# edit port / systemd_user if needed

chmod +x deploy/deploy.sh
./deploy/deploy.sh
```

Requires on the instance: sudo for systemd. If `node`/`npm` are missing, installs **Node.js 20**. For Python: uses 3.11–3.13 if present, else tries `python3.13` via apt, else installs **uv** and provisions **Python 3.12** (needed on Ubuntu 26.04 / Resolute where system `python3` is 3.14 and breaks pydantic).

The script builds the frontend, creates `.venv`, installs deps, installs/enables `redis-data-downloader.service`, and restarts it. App listens on `0.0.0.0:8080` by default (open the security group).

Re-deploy after `git pull`:

```bash
cd redis-data-downloader
git pull
./deploy/deploy.sh
```

## Env vars

| Var | Purpose |
|---|---|
| `APP_PASSWORD` | Shared login password |
| `APP_JWT_SECRET` | JWT signing secret |
| `DB_POSTGRES_*` | Same as heyev-backend getFleetSum |
| `REDIS_URL` / `REDIS_PASS` | Redis `host:port` |
| `REDIS_BATCH_SIZE` | Optional (default 50) |
| `OUTPUT_DIR` | Optional; systemd sets this to `<repo>/output` |
