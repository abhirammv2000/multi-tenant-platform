from control_plane.app.models.tenants import Tenant
from control_plane.app.k8s_resources import ensure_tenant_namespace, ensure_tenant_rbac, mint_tenant_kubeconfig
from sqlalchemy.ext.asyncio import AsyncSession

async def get_tenant_kubeconfig_service(tenant_id: int, session: AsyncSession) -> str:
    tenant=await session.get(Tenant, tenant_id)
    if not tenant:
        raise ValueError(f"tenant {tenant_id} does not exist")

    #both idempotent, safe even if this is the tenant's very first Kubernetes-touching
    #request (kubeconfig download before any build submitted). ensure_tenant_namespace
    #guarantees the namespace itself exists before RBAC resources are created inside it.
    ensure_tenant_namespace(tenant.k8s_namespace)
    ensure_tenant_rbac(tenant.k8s_namespace)
    return mint_tenant_kubeconfig(tenant.k8s_namespace)
