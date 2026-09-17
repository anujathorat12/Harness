"""
Central configuration, loaded from environment variables (12-factor style).

Nothing security-relevant should ever be hardcoded elsewhere in the codebase;
if a component needs a limit, timeout, or secret, it should come from here.
"""
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="HARNESS_", env_file=".env", extra="ignore")

    # --- Service identity ---
    service_name: str = "byoa-harness"
    environment: str = "local"  # local | staging | production
    api_version: str = "v1"

    # --- Persistence ---
    # Defaults to a local SQLite file so the platform runs with zero external
    # dependencies. Point this at a Postgres DSN in real deployments, e.g.
    # postgresql+asyncpg://user:pass@host/db -- the SQLAlchemy models are
    # written to be database-agnostic.
    database_url: str = "sqlite+aiosqlite:///./data/harness.db"

    # --- Runtime / sandbox defaults (overridable per-agent at registration) ---
    default_cpu_seconds: int = 5
    default_memory_mb: int = 256
    default_timeout_seconds: int = 30
    default_pid_limit: int = 32
    default_output_bytes_limit: int = 1_000_000
    runtime_workdir_root: str = "/tmp/harness-sandboxes"

    # --- Concurrency ---
    max_concurrent_sessions_per_agent: int = 3
    max_concurrent_sessions_total: int = 50

    # --- Approval ---
    approval_default_timeout_seconds: int = 600  # 10 minutes

    # --- Audit ---
    audit_redacted_keys: tuple[str, ...] = (
        "password", "token", "secret", "api_key", "apikey",
        "authorization", "access_token", "refresh_token", "private_key",
    )

    # --- HTTP ---
    host: str = "0.0.0.0"
    port: int = 8000

    # --- Container runtime (used only when an agent version declares
    # runtime.kind == "container"; requires a Docker daemon on the host
    # running the harness) ---
    container_runtime_image_pull: bool = True
    container_runtime_network: str = "none"  # sandboxes get no network by default


settings = Settings()
