from pydantic import BaseModel, Field
from datetime import datetime
from typing import Optional

class APIKeyCreate(BaseModel):
    name: str=Field(min_length=1, max_length=50)

class APIKeyResponse(BaseModel):
    id: int
    tenant_id: int
    name: str
    key_prefix: str
    created_at: datetime
    revoked_at: Optional[datetime]

    model_config={"from_attributes":True}

class APIKeyCreatedResponse(APIKeyResponse):
    api_key: str
