from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from harness.api.v1 import agents, approvals, audit, health, policies, tasks
from harness.core.config import settings
from harness.core.database import init_db

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield


app = FastAPI(
    title="BYOA Agent Harness Platform",
    description=(
        "A control plane for hosting third-party agents, sandboxing their execution, "
        "and enforcing a per-action, declarative policy layer with human-in-the-loop "
        "approval and a full audit trail."
    ),
    version="1.0.0",
    lifespan=lifespan,
)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logging.getLogger("harness.api").exception("unhandled exception")
    return JSONResponse(status_code=500, content={"error": "internal_error", "detail": str(exc)})


app.include_router(agents.router, prefix=f"/api/{settings.api_version}")
app.include_router(policies.router, prefix=f"/api/{settings.api_version}")
app.include_router(tasks.router, prefix=f"/api/{settings.api_version}")
app.include_router(approvals.router, prefix=f"/api/{settings.api_version}")
app.include_router(audit.router, prefix=f"/api/{settings.api_version}")
app.include_router(health.router)


@app.get("/")
async def root():
    return {
        "service": settings.service_name,
        "api_version": settings.api_version,
        "docs": "/docs",
        "openapi": "/openapi.json",
    }
