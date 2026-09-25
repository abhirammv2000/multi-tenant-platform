from fastapi import APIRouter, Depends, status, HTTPException, Query, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from shared.db import get_db
from control_plane.app.services.deployments import create_deployment_service, get_deployment_service, get_all_deployments_service, poll_deployment_until_running, BuildJobNotReadyError
from control_plane.app.schemas.deployments import DeploymentCreate, DeploymentResponse
from typing import List

router=APIRouter()

@router.post("/", response_model=DeploymentResponse, status_code=status.HTTP_201_CREATED)
async def create_deployment(tenant_id: int, deploy_data: DeploymentCreate, background_tasks: BackgroundTasks, session: AsyncSession=Depends(get_db)):
    try:
        deployment,k8s_namespace=await create_deployment_service(tenant_id, deploy_data, session)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except BuildJobNotReadyError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    if k8s_namespace:
        background_tasks.add_task(poll_deployment_until_running, deployment.id, k8s_namespace, deployment.k8s_deployment_name)
    return deployment

@router.get("/{deployment_id}", response_model=DeploymentResponse)
async def get_deployment(tenant_id: int, deployment_id: int, session: AsyncSession=Depends(get_db)):
    deployment=await get_deployment_service(tenant_id, deployment_id, session)
    if not deployment:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,detail="Deployment not found")
    return deployment

@router.get("/",response_model=List[DeploymentResponse])
async def get_all_deployments(tenant_id: int, session: AsyncSession=Depends(get_db), limit: int|None=Query(default=None,ge=1), offset: int=Query(default=0, ge=0)):
    return await get_all_deployments_service(tenant_id, session, limit, offset)
