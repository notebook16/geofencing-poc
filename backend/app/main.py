"""FastAPI app: auth, fields, export, history, and static frontend."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated, Optional

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .auth import LoginRequest, LoginResponse, authenticate, current_user
from .export import run_export
from .history import append_run, list_runs
from .schema import default_field_ids, list_fields

BACKEND_DIR = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_DIR.parent

# Load .env from project root, then backend (backend wins if both exist)
load_dotenv(PROJECT_ROOT / ".env")
load_dotenv(BACKEND_DIR / ".env")

app = FastAPI(title="redis-data-downloader", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ORIGINS", "*").split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ExportRequest(BaseModel):
    fields: list[str] = Field(default_factory=list)
    imeis: Optional[list[str]] = None


@app.get("/api/health")
def health():
    return {"ok": True}


@app.post("/api/login", response_model=LoginResponse)
def login(body: LoginRequest):
    token = authenticate(body.username, body.password)
    return LoginResponse(access_token=token, username=body.username.strip())


@app.get("/api/fields")
def fields(_: Annotated[str, Depends(current_user)]):
    return {
        "fields": list_fields(),
        "defaults": default_field_ids(),
    }


@app.get("/api/history")
def history(_: Annotated[str, Depends(current_user)]):
    return {"runs": list_runs()}


@app.post("/api/export")
def export(body: ExportRequest, username: Annotated[str, Depends(current_user)]):
    field_ids = body.fields or default_field_ids()
    try:
        result = run_export(field_ids=field_ids, imeis=body.imeis, username=username)
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Export failed: {exc}") from exc

    append_run(
        username=username,
        started_at=result.started_at,
        scope=result.scope,
        row_count=result.row_count,
        fields=result.fields,
        filename=result.filename,
    )

    return Response(
        content=result.content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f'attachment; filename="{result.filename}"',
            "X-Row-Count": str(result.row_count),
        },
    )


# Serve built React UI from frontend/dist when present
FRONTEND_DIST = PROJECT_ROOT / "frontend" / "dist"
if FRONTEND_DIST.is_dir():
    assets = FRONTEND_DIST / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/{full_path:path}")
    def spa_fallback(full_path: str):
        if full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Not found")
        candidate = FRONTEND_DIST / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(FRONTEND_DIST / "index.html")
