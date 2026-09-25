from fastapi import APIRouter, Depends, status, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from shared.db import get_db
from control_plane.app.services.api_keys import create_api_key_service, get_api_key_service, get_all_api_keys_service, revoke_api_key_service
from control_plane.app.schemas.api_keys import APIKeyCreate, APIKeyResponse, APIKeyCreatedResponse
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from typing import List

router=APIRouter()

@router.post("/", response_model=APIKeyCreatedResponse, status_code=status.HTTP_201_CREATED)
async def create_api_key(tenant_id: int, key_data: APIKeyCreate, session: AsyncSession=Depends(get_db)):
    try:
        api_key,raw_api_key=await create_api_key_service(tenant_id, key_data, session)
    except IntegrityError:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,detail="A key with that name already exists for this tenant.")
    except SQLAlchemyError:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,detail="Database error while creating API key.")

    base_response=APIKeyResponse.model_validate(api_key)
    return APIKeyCreatedResponse(**base_response.model_dump(),api_key=raw_api_key)

@router.get("/{key_id}", response_model=APIKeyResponse)
async def get_api_key(tenant_id: int, key_id: int, session: AsyncSession=Depends(get_db)):
    api_key=await get_api_key_service(tenant_id, key_id, session)
    if not api_key:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,detail="API key not found")
    return api_key

@router.get("/",response_model=List[APIKeyResponse])
async def get_all_api_keys(tenant_id: int, session: AsyncSession=Depends(get_db)):
    return await get_all_api_keys_service(tenant_id, session)

@router.post("/{key_id}/revoke", response_model=APIKeyResponse, status_code=status.HTTP_200_OK)
async def revoke_api_key(tenant_id: int, key_id: int, session: AsyncSession=Depends(get_db)):
    api_key=await revoke_api_key_service(tenant_id, key_id, session)
    if not api_key:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,detail="API key not found")
    return api_key
