from control_plane.app.models.tenants import Tenant
from control_plane.app.models.api_keys import APIKey
from control_plane.app.schemas.tenants import TenantCreate, TenantUpdate
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy import delete, select
from shared.config import API_KEY_SECRET
from shared.utils import generate_secure_key, hash_api_key

async def create_tenant_service(tenant_data: TenantCreate, session: AsyncSession):
    tenant=Tenant(name=tenant_data.name, k8s_namespace="pending") #placeholder, needs tenant.id, set below after flush

    try:
        session.add(tenant)
        await session.flush() #assigns tenant.id without committing yet

        tenant.k8s_namespace=f"tenant-{tenant.id}" #deterministic, unique by construction (tenant.id is a PK)

        api_key_dict={"name":"primary", "tenant_id": tenant.id}
        raw_api_key=generate_secure_key()
        key_hash=hash_api_key(raw_api_key, API_KEY_SECRET)
        api_key_dict["key_prefix"]=raw_api_key[:16]
        api_key_dict["key_hash"]=key_hash
        api_key=APIKey(**api_key_dict)

        session.add(api_key)
        await session.commit()
        await session.refresh(tenant)
        return tenant, raw_api_key

    except SQLAlchemyError:
        await session.rollback()
        raise

async def get_tenant_service(tenant_id: int, session: AsyncSession):
    return await session.get(Tenant, tenant_id)

async def get_all_tenants_service(session: AsyncSession, limit: int|None=None, offset: int=0):
    statement=select(Tenant).order_by(Tenant.id).offset(offset)
    if limit is not None:
        statement=statement.limit(limit)
    result=await session.execute(statement)
    return result.scalars().all()

async def delete_tenant_service(tenant_id: int, session: AsyncSession):
    try:
        result=await session.execute(delete(Tenant).where(Tenant.id==tenant_id))
        await session.commit()
        return result.rowcount
    except SQLAlchemyError:
        await session.rollback()
        raise

async def update_tenant_service(tenant_id: int, tenant_data: TenantUpdate, session: AsyncSession):
    tenant=await session.get(Tenant, tenant_id)
    if not tenant:
        return None

    update_dict=tenant_data.model_dump(exclude_unset=True)
    if not update_dict:
        raise ValueError("No fields were provided for update.")

    try:
        for key,value in update_dict.items():
            setattr(tenant,key,value)
        await session.commit()
        await session.refresh(tenant)
        return tenant
    except SQLAlchemyError:
        await session.rollback()
        raise
