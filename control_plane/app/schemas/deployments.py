from pydantic import BaseModel, Field
from datetime import datetime
from typing import Literal

DeploymentStatus=Literal["pending", "deploying", "running", "failed", "stopped"]

class DeploymentCreate(BaseModel):
    build_job_id: int
    replica_count: int=Field(default=1, ge=1, le=10) #capped for a demo-scale project, a production platform would size this per tenant plan/quota

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
