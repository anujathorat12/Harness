from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from harness.api.v1 import agents, approvals, audit, auth, dashboard, health, policies, system, tasks
from harness.core.config import settings
from harness.core.database import init_db
from harness.core.middleware import RequestIDLogFilter, RequestIDMiddleware, get_request_id

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s [req=%(request_id)s] %(message)s"
)
logging.getLogger().handlers[0].addFilter(RequestIDLogFilter())

_DEV_DEFAULT_KEYS = {
    settings.api_key_admin: "HARNESS_API_KEY_ADMIN",
    settings.api_key_operator: "HARNESS_API_KEY_OPERATOR",
    settings.api_key_auditor: "HARNESS_API_KEY_AUDITOR",
    settings.api_key_client: "HARNESS_API_KEY_CLIENT",
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    if settings.environment != "local":
        still_default = [
            var for key, var in _DEV_DEFAULT_KEYS.items() if key.endswith("***CHANGE-ME***")
        ]
        if still_default:
            logging.getLogger("harness.security").warning(
                "environment=%r but these API keys are still at their dev-only default: %s -- "
                "set real values before exposing this deployment",
                settings.environment,
                ", ".join(still_default),
            )
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

app.add_middleware(RequestIDMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_allow_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logging.getLogger("harness.api").exception("unhandled exception")
    return JSONResponse(
        status_code=500,
        content={"error": "internal_error", "detail": str(exc), "request_id": get_request_id()},
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": "request_error", "detail": exc.detail, "request_id": get_request_id()},
    )


app.include_router(agents.router, prefix=f"/api/{settings.api_version}")
app.include_router(policies.router, prefix=f"/api/{settings.api_version}")
app.include_router(tasks.router, prefix=f"/api/{settings.api_version}")
app.include_router(approvals.router, prefix=f"/api/{settings.api_version}")
app.include_router(audit.router, prefix=f"/api/{settings.api_version}")
app.include_router(auth.router, prefix=f"/api/{settings.api_version}")
app.include_router(dashboard.router, prefix=f"/api/{settings.api_version}")
app.include_router(system.router, prefix=f"/api/{settings.api_version}")
app.include_router(health.router)


@app.get("/")
async def root():
    return {
        "service": settings.service_name,
        "api_version": settings.api_version,
        "docs": "/docs",
        "openapi": "/openapi.json",
    }
