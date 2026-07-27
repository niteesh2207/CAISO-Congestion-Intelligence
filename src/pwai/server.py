from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Header
from fastapi.responses import FileResponse
from pydantic import BaseModel

from .engine import GridStudioEngine
from .resource_utils import project_or_package_resource
from .platform.catalog import capability_catalog, answer_profiles


MODE = os.getenv("PWAI_MODE", "demo").lower()
engine = GridStudioEngine(MODE)
engine.start()

app = FastAPI(
    title="PowerWorld AI Grid Studio",
    version="2.0.0-rc1",
)


class AskRequest(BaseModel):
    question: str
    confirm_changes: bool = False


class CaseRequest(BaseModel):
    path: str


@app.get("/")
def home():
    return FileResponse(project_or_package_resource("web", "index.html"))




def _require_api_key(x_pwai_api_key: str | None) -> None:
    configured=os.getenv("PWAI_API_KEY")
    if configured and x_pwai_api_key != configured:
        raise HTTPException(status_code=401, detail="Invalid or missing Grid Studio API key.")


@app.get("/api/capabilities/catalog")
def capabilities_catalog():
    return capability_catalog()


@app.get("/api/answer-profiles")
def profiles():
    return answer_profiles()

@app.get("/api/status")
def status():
    return engine.status()


@app.get("/api/network")
def network():
    return engine.network()


@app.post("/api/case/load")
def load_case(req: CaseRequest, x_pwai_api_key: str | None = Header(default=None)):
    _require_api_key(x_pwai_api_key)
    if MODE == "demo":
        raise HTTPException(status_code=400, detail="Case loading is unavailable in demo mode.")
    engine.start(req.path)
    return engine.status()


@app.post("/api/ask")
def ask(req: AskRequest, x_pwai_api_key: str | None = Header(default=None)):
    _require_api_key(x_pwai_api_key)
    try:
        return engine.ask(
            req.question,
            confirm_changes=req.confirm_changes,
        ).model_dump(mode="json")
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
