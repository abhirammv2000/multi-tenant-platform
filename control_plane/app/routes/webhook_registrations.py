from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession
from shared.db import get_db
from control_plane.app.services.webhook_registrations import create_webhook_registration_service, get_all_webhook_registrations_service
from control_plane.app.schemas.webhook_registrations import WebhookRegistrationCreate, WebhookRegistrationResponse, WebhookRegistrationCreatedResponse
from typing import List

router=APIRouter()

@router.post("/", response_model=WebhookRegistrationCreatedResponse, status_code=status.HTTP_201_CREATED)
async def create_webhook_registration(tenant_id: int, reg_data: WebhookRegistrationCreate, session: AsyncSession=Depends(get_db)):
    registration,signing_secret=await create_webhook_registration_service(tenant_id, reg_data, session)
    base_response=WebhookRegistrationResponse.model_validate(registration)
    return WebhookRegistrationCreatedResponse(**base_response.model_dump(),signing_secret=signing_secret)

@router.get("/",response_model=List[WebhookRegistrationResponse])
async def get_all_webhook_registrations(tenant_id: int, session: AsyncSession=Depends(get_db)):
    return await get_all_webhook_registrations_service(tenant_id, session)
