from fastapi import APIRouter, Depends, status, HTTPException, Query, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from shared.db import get_db
from control_plane.app.services.build_jobs import create_build_job_service, get_build_job_service, get_all_build_jobs_service, poll_build_until_done
from control_plane.app.schemas.build_jobs import BuildJobCreate, BuildJobResponse
from typing import List

router=APIRouter()

@router.post("/", response_model=BuildJobResponse, status_code=status.HTTP_201_CREATED)
async def create_build_job(tenant_id: int, build_data: BuildJobCreate, background_tasks: BackgroundTasks, session: AsyncSession=Depends(get_db)):
    build_job,k8s_namespace=await create_build_job_service(tenant_id, build_data, session)
    if k8s_namespace: #only schedule polling if the build actually started (status=="building")
        background_tasks.add_task(poll_build_until_done, build_job.id, k8s_namespace)
    return build_job

@router.get("/{build_job_id}", response_model=BuildJobResponse)
async def get_build_job(tenant_id: int, build_job_id: int, session: AsyncSession=Depends(get_db)):
    build_job=await get_build_job_service(tenant_id, build_job_id, session)
    if not build_job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,detail="Build job not found")
    return build_job

@router.get("/",response_model=List[BuildJobResponse])
async def get_all_build_jobs(tenant_id: int, session: AsyncSession=Depends(get_db), limit: int|None=Query(default=None,ge=1), offset: int=Query(default=0, ge=0)):
    return await get_all_build_jobs_service(tenant_id, session, limit, offset)
