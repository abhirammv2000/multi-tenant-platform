from pydantic import BaseModel, Field
from datetime import datetime

class WebhookRegistrationCreate(BaseModel):
    callback_url: str=Field(min_length=1, max_length=2048)

class WebhookRegistrationResponse(BaseModel):
    id: int
    tenant_id: int
    callback_url: str
    created_at: datetime
    is_active: bool

    model_config={"from_attributes":True}

class WebhookRegistrationCreatedResponse(WebhookRegistrationResponse):
    signing_secret: str #full raw secret, shown only once
