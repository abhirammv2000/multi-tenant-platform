from pydantic import BaseModel, Field
from datetime import datetime
from typing import Optional

class TenantCreate(BaseModel):
    name: str=Field(min_length=1, max_length=255)

class TenantUpdate(BaseModel):
    name: Optional[str]=Field(None, min_length=1, max_length=255)
    is_active: Optional[bool]=None

class TenantResponse(BaseModel):
    id: int
    name: str
    k8s_namespace: str
    created_at: datetime
    is_active: bool

    model_config={"from_attributes":True}

class TenantCreatedResponse(TenantResponse):
    api_key: str #full raw key, shown only once
