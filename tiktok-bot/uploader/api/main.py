"""FastAPI application entry point.

Exposes CRUD and upload endpoints backed by SQLite. OpenAPI docs at /docs
render the Pydantic schemas defined in api.schemas — this is what satisfies
"all schemas are defined and presented in the API".
"""
from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.db import init_db
from api.routers import accounts, login, schedules, uploads, videos
from tiktok_uploader.Config import Config


def create_app() -> FastAPI:
    # Load config.txt the same way cli.py does, so paths stay consistent when
    # the api container cwd is /app.
    config_path = os.getenv("TIKTOK_CONFIG_PATH", "./config.txt")
    if os.path.exists(config_path):
        Config.load(config_path)

    app = FastAPI(
        title="TiktokAutoUploader API",
        version="1.0.0",
        description=(
            "REST API for managing TikTok accounts, uploading videos "
            "(local file or YouTube URL), and scheduling uploads. "
            "Backs the React web UI; the legacy CLI also remains supported."
        ),
    )

    # Local-first tool — webapp and api are on the same compose network and
    # the api port binds to 127.0.0.1. CORS is permissive for dev convenience.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
        allow_credentials=False,
    )

    @app.on_event("startup")
    def _startup() -> None:
        # Ensure CookiesDir and VideosDirPath exist on the shared volume.
        for d in (Config.get().cookies_dir, Config.get().videos_dir):
            os.makedirs(os.path.join(os.getcwd(), d), exist_ok=True)
        init_db()

    @app.get("/health", tags=["meta"])
    def health():
        return {"status": "ok"}

    app.include_router(accounts.router)
    app.include_router(videos.router)
    app.include_router(uploads.router)
    app.include_router(schedules.router)
    app.include_router(login.router)
    return app


app = create_app()
