from control_plane.app.models.webhook_registrations import WebhookRegistration
from control_plane.app.schemas.webhook_registrations import WebhookRegistrationCreate
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy import select
import secrets

async def create_webhook_registration_service(tenant_id: int, reg_data: WebhookRegistrationCreate, session: AsyncSession):
    signing_secret=secrets.token_urlsafe(32) #shown once at creation, then used internally to sign every callback, see shared/webhook_dispatcher.py

    registration=WebhookRegistration(tenant_id=tenant_id, callback_url=reg_data.callback_url, signing_secret=signing_secret)
    try:
        session.add(registration)
        await session.commit()
        await session.refresh(registration)
        return registration, signing_secret
    except SQLAlchemyError:
        await session.rollback()
        raise

async def get_all_webhook_registrations_service(tenant_id: int, session: AsyncSession):
    statement=select(WebhookRegistration).where(WebhookRegistration.tenant_id==tenant_id).order_by(WebhookRegistration.id)
    result=await session.execute(statement)
    return result.scalars().all()
