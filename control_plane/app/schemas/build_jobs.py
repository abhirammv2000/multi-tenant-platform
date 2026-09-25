from pydantic import BaseModel, Field
from datetime import datetime
from typing import Optional, Literal

BuildStatus=Literal["queued", "building", "succeeded", "failed"]

class BuildJobCreate(BaseModel):
    git_url: str=Field(min_length=1, max_length=2048)
    git_revision: str=Field(default="main", max_length=255)
    git_sub_path: Optional[str]=Field(default=None, max_length=1024) #for monorepos, the app's location within the repo, e.g. "apps/api"

class BuildJobResponse(BaseModel):
    id: int
    tenant_id: int
    git_url: str
    git_revision: str
    git_sub_path: Optional[str]
    status: BuildStatus
    image_tag: Optional[str]
    error_message: Optional[str]
    created_at: datetime
    started_at: Optional[datetime]
    ended_at: Optional[datetime]

    model_config={"from_attributes":True}
