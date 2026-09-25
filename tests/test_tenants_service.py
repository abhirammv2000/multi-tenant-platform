"""create_tenant_service has a two-step DB-identity dependency worth locking down:
k8s_namespace can't be computed until tenant.id exists, which only happens after a flush.
The function inserts a placeholder, flushes, then overwrites k8s_namespace with the real
value before committing. Get the ordering wrong and every tenant ends up with the same
"pending" namespace, breaking per-tenant isolation.

session.add/flush/commit/refresh are mocked here, with flush's side effect assigning
tenant.id the way a Postgres round-trip would. Checked by hand against a local Postgres
before this test was written (Phase 1 development): a tenant created via the running API
got k8s_namespace "tenant-1", matching its assigned id.
"""
from unittest.mock import AsyncMock, Mock

from control_plane.app.services.tenants import create_tenant_service
from control_plane.app.schemas.tenants import TenantCreate


def make_session(assigned_id=7):
    session = AsyncMock()
    state = {"tenant": None}

    def add(obj):
        if obj.__class__.__name__ == "Tenant":
            state["tenant"] = obj

    async def flush():
        #simulates what a real Postgres INSERT ... RETURNING id does during flush()
        if state["tenant"] is not None:
            state["tenant"].id = assigned_id

    session.add = Mock(side_effect=add)
    session.flush = AsyncMock(side_effect=flush)
    session.commit = AsyncMock()
    session.refresh = AsyncMock()
    session.rollback = AsyncMock()
    return session


async def test_k8s_namespace_is_derived_from_the_post_flush_id():
    session = make_session(assigned_id=7)

    tenant, raw_api_key = await create_tenant_service(TenantCreate(name="acme"), session)

    assert tenant.id == 7
    assert tenant.k8s_namespace == "tenant-7"


async def test_raw_api_key_is_never_persisted_only_returned():
    session = make_session(assigned_id=3)

    tenant, raw_api_key = await create_tenant_service(TenantCreate(name="acme"), session)

    assert raw_api_key.startswith("mtp_")
    #the api key added to the session must carry a hash, never the raw key itself
    added_calls = [c.args[0] for c in session.add.call_args_list if hasattr(c.args[0], "key_hash")]
    assert len(added_calls) == 1
    assert added_calls[0].key_hash != raw_api_key
    assert added_calls[0].tenant_id == 3
