"""get_tenant_kubeconfig_service just needs to raise cleanly for an unknown tenant and
otherwise hand off to k8s_resources. The actual RBAC scoping (does the minted kubeconfig
only grant get/list on pods in the tenant's own namespace) was verified live against a
cluster, not mocked: a downloaded kubeconfig could `kubectl get pods` in its own namespace
but got `Forbidden` for another namespace, cluster nodes, and `pods/exec`. See README's
Phase 4 section.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from control_plane.app.services.kubeconfig import get_tenant_kubeconfig_service


async def test_raises_for_an_unknown_tenant():
    session = AsyncMock()
    session.get = AsyncMock(return_value=None)

    with pytest.raises(ValueError):
        await get_tenant_kubeconfig_service(tenant_id=999, session=session)


@patch("control_plane.app.services.kubeconfig.mint_tenant_kubeconfig")
@patch("control_plane.app.services.kubeconfig.ensure_tenant_rbac")
@patch("control_plane.app.services.kubeconfig.ensure_tenant_namespace")
async def test_ensures_namespace_and_rbac_before_minting_for_a_known_tenant(mock_ensure_ns, mock_ensure_rbac, mock_mint):
    mock_mint.return_value = "apiVersion: v1\nkind: Config\n"
    session = AsyncMock()
    session.get = AsyncMock(return_value=SimpleNamespace(id=1, k8s_namespace="tenant-1"))

    result = await get_tenant_kubeconfig_service(tenant_id=1, session=session)

    mock_ensure_ns.assert_called_once_with("tenant-1")
    mock_ensure_rbac.assert_called_once_with("tenant-1")
    assert result.startswith("apiVersion: v1")
