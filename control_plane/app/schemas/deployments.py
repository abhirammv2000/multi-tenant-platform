from pydantic import BaseModel, Field
from datetime import datetime
from typing import Literal

DeploymentStatus=Literal["pending", "deploying", "running", "failed", "stopped"]

class DeploymentCreate(BaseModel):
    build_job_id: int
    #8 is the most that fits the tenant ResourceQuota (4 CPU of limits / 500m per replica). More would create a
    #Deployment whose last pods the quota rejects, so it would never finish rolling out.
    #tests/test_isolation_manifests.py checks this number against the quota.
    replica_count: int=Field(default=1, ge=1, le=8)

class DeploymentResponse(BaseModel):
    id: int
    tenant_id: int
    build_job_id: int
    k8s_deployment_name: str
    status: DeploymentStatus
    replica_count: int
    created_at: datetime
    updated_at: datetime

    model_config={"from_attributes":True}
