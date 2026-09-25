from control_plane.app.models.api_keys import APIKey
from control_plane.app.schemas.api_keys import APIKeyCreate
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy import select
from shared.config import API_KEY_SECRET
from shared.utils import generate_secure_key, hash_api_key, now_naive

async def create_api_key_service(tenant_id: int, key_data: APIKeyCreate, session: AsyncSession):
    raw_api_key=generate_secure_key()
    key_hash=hash_api_key(raw_api_key, API_KEY_SECRET)

    api_key=APIKey(tenant_id=tenant_id, name=key_data.name, key_prefix=raw_api_key[:16], key_hash=key_hash)
    try:
        session.add(api_key)
        await session.commit()
        await session.refresh(api_key)
        return api_key, raw_api_key
    except SQLAlchemyError:
        await session.rollback()
        raise

async def get_api_key_service(tenant_id: int, key_id: int, session: AsyncSession):
    statement=select(APIKey).where(APIKey.id==key_id, APIKey.tenant_id==tenant_id)
    result=await session.execute(statement)
    return result.scalar_one_or_none()

async def get_all_api_keys_service(tenant_id: int, session: AsyncSession):
    statement=select(APIKey).where(APIKey.tenant_id==tenant_id).order_by(APIKey.id)
    result=await session.execute(statement)
    return result.scalars().all()

async def revoke_api_key_service(tenant_id: int, key_id: int, session: AsyncSession):
    api_key=await get_api_key_service(tenant_id, key_id, session)
    if not api_key:
        return None
    try:
        api_key.revoked_at=now_naive()
        await session.commit()
        await session.refresh(api_key)
        return api_key
    except SQLAlchemyError:
        await session.rollback()
        raise
