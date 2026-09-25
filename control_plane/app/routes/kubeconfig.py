from fastapi import APIRouter, Depends, status, HTTPException
from fastapi.responses import PlainTextResponse
from sqlalchemy.ext.asyncio import AsyncSession
from shared.db import get_db
from control_plane.app.services.kubeconfig import get_tenant_kubeconfig_service

router=APIRouter()

@router.get("/", response_class=PlainTextResponse)
async def get_kubeconfig(tenant_id: int, session: AsyncSession=Depends(get_db)):
    try:
        return await get_tenant_kubeconfig_service(tenant_id, session)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
