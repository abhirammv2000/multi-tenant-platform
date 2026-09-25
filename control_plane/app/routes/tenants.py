from fastapi import APIRouter, Depends, status, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from shared.db import get_db
from control_plane.app.services.tenants import create_tenant_service, get_tenant_service, get_all_tenants_service, delete_tenant_service, update_tenant_service
from control_plane.app.schemas.tenants import TenantCreate, TenantUpdate, TenantResponse, TenantCreatedResponse
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from typing import List
from control_plane.app.dependencies import verify_admin

router=APIRouter(dependencies=[Depends(verify_admin)])

@router.post("/", response_model=TenantCreatedResponse, status_code=status.HTTP_201_CREATED)
async def create_tenant(tenant_data: TenantCreate, session: AsyncSession=Depends(get_db)):
    try:
        tenant,raw_api_key=await create_tenant_service(tenant_data, session)
    except IntegrityError:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,detail="Tenant could not be created because of a database constraint violation.")
    except SQLAlchemyError:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,detail="Database error while creating tenant.")

    base_response=TenantResponse.model_validate(tenant)
    return TenantCreatedResponse(**base_response.model_dump(),api_key=raw_api_key)

@router.get("/{tenant_id}", response_model=TenantResponse)
async def get_tenant(tenant_id: int, session: AsyncSession=Depends(get_db)):
    tenant=await get_tenant_service(tenant_id, session)
    if not tenant:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,detail="Tenant not found")
    return tenant

@router.get("/",response_model=List[TenantResponse])
async def get_all_tenants(session: AsyncSession=Depends(get_db)):
    return await get_all_tenants_service(session)

@router.delete("/{tenant_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_tenant(tenant_id: int, session: AsyncSession=Depends(get_db)):
    deleted_count=await delete_tenant_service(tenant_id, session)
    if deleted_count==0:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,detail="Tenant not found")

@router.put("/{tenant_id}", response_model=TenantResponse, status_code=status.HTTP_200_OK)
async def update_tenant(tenant_id: int, tenant_data: TenantUpdate, session: AsyncSession=Depends(get_db)):
    try:
        tenant=await update_tenant_service(tenant_id, tenant_data, session)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    if not tenant:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,detail="Tenant not found")
    return tenant
