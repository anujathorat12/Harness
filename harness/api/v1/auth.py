from __future__ import annotations

from fastapi import APIRouter, Security

from harness.api.v1 import schemas
from harness.core.security import Principal, get_current_principal

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/me", response_model=schemas.PrincipalOut)
async def me(principal: Principal = Security(get_current_principal)):
    """Any authenticated caller can ask 'who am I' -- this is what the admin
    frontend calls right after the operator pastes an API key, purely to
    decide what to show in the UI. It grants no additional access itself."""
    return schemas.PrincipalOut(role=principal.role, label=principal.label)
