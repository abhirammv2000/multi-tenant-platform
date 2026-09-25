"""verify_tenant is the boundary that keeps tenant A from touching tenant B's resources
through the API, even with a valid API key. Tests the three rejection paths; the accept
path was already verified live during Phase 1 development.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from control_plane.app.dependencies import verify_tenant


def make_session(row):
    session = AsyncMock()
    session.execute = AsyncMock(return_value=SimpleNamespace(one_or_none=lambda: row))
    return session


async def test_rejects_an_unknown_api_key():
    session = make_session(None)

    with pytest.raises(HTTPException) as exc_info:
        await verify_tenant(tenant_id=1, x_api_key="nonexistent", session=session)
    assert exc_info.value.status_code == 401


async def test_rejects_an_api_key_scoped_to_a_different_tenant():
    api_key = SimpleNamespace(tenant_id=2)
    tenant = SimpleNamespace(is_active=True)
    session = make_session((api_key, tenant))

    with pytest.raises(HTTPException) as exc_info:
        await verify_tenant(tenant_id=1, x_api_key="valid-but-wrong-tenant", session=session)
    assert exc_info.value.status_code == 403


async def test_rejects_a_valid_key_for_an_inactive_tenant():
    api_key = SimpleNamespace(tenant_id=1)
    tenant = SimpleNamespace(is_active=False)
    session = make_session((api_key, tenant))

    with pytest.raises(HTTPException) as exc_info:
        await verify_tenant(tenant_id=1, x_api_key="valid-but-suspended", session=session)
    assert exc_info.value.status_code == 403
