"""
Authentication / authorization.

Deliberately simple: one static API key per role, sent as the `X-API-Key`
header. There is no user database, no password hashing, no token issuance
or expiry -- this is NOT an enterprise IAM system, by design (see
SECURITY.md and ARCHITECTURE.md for the reasoning).

What this module DOES guarantee, and what actually matters: every privileged
route is gated by `require_role(...)` as a FastAPI dependency, enforced
server-side on every request. The frontend may also hide buttons a role
can't use, but that is UX convenience only -- it is never the security
boundary. Removing every line of frontend code would not change what an
API caller can or cannot do.

Roles, from the assignment's own permission table:
  ADMIN    -- manage agents, manage policies, approve/deny actions, view audit
  OPERATOR -- view agents, view tasks/sessions, approve/deny actions, view audit
  AUDITOR  -- read-only: audit events, tasks/sessions
  CLIENT   -- submit tasks, check/read only ITS OWN tasks and sessions
"""
from __future__ import annotations

from dataclasses import dataclass

from fastapi import HTTPException, Security
from fastapi.security import APIKeyHeader

from harness.core.config import settings

ADMIN = "ADMIN"
OPERATOR = "OPERATOR"
AUDITOR = "AUDITOR"
CLIENT = "CLIENT"
ROLES = (ADMIN, OPERATOR, AUDITOR, CLIENT)


@dataclass(frozen=True)
class Principal:
    role: str
    label: str  # human-readable identity, used as audit `actor` / task `created_by`


_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def _key_to_role_map() -> dict[str, tuple[str, str]]:
    """key -> (role, label). Rebuilt per call (not cached at import time) so
    tests that monkeypatch `settings` after import still see current keys."""
    return {
        settings.api_key_admin: (ADMIN, "admin-key"),
        settings.api_key_operator: (OPERATOR, "operator-key"),
        settings.api_key_auditor: (AUDITOR, "auditor-key"),
        settings.api_key_client: (CLIENT, "client-key"),
    }


async def get_current_principal(api_key: str | None = Security(_api_key_header)) -> Principal:
    if not api_key:
        raise HTTPException(status_code=401, detail="missing X-API-Key header")
    match = _key_to_role_map().get(api_key)
    if match is None:
        raise HTTPException(status_code=401, detail="invalid API key")
    role, label = match
    return Principal(role=role, label=label)


def require_role(*allowed_roles: str):
    """Dependency factory: `Depends(require_role(ADMIN, OPERATOR))`. Raises
    401 if unauthenticated, 403 if authenticated but the wrong role -- these
    are kept distinct on purpose (401 = "who are you", 403 = "I know who you
    are, and the answer is still no")."""

    async def _dependency(principal: Principal = Security(get_current_principal)) -> Principal:
        if principal.role not in allowed_roles:
            raise HTTPException(
                status_code=403,
                detail=f"role {principal.role!r} is not permitted to perform this operation "
                f"(requires one of: {', '.join(allowed_roles)})",
            )
        return principal

    return _dependency
