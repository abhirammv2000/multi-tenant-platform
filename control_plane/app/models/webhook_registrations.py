from shared.db import Base
from sqlalchemy.orm import Mapped, mapped_column, relationship
from datetime import datetime
from sqlalchemy import ForeignKey, String, func

class WebhookRegistration(Base):
    __tablename__="webhook_registrations"

    id: Mapped[int]=mapped_column(primary_key=True)
    tenant_id: Mapped[int]=mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)

    callback_url: Mapped[str]=mapped_column(String(2048), nullable=False)
    #unlike an API key, this secret must be retained in a form the control plane can read
    #back: it signs outgoing requests (shared/webhook_dispatcher.py) rather than just
    #verifying a presented value, so hashing it like api_keys.key_hash won't work. Stored
    #in plaintext here; production would keep this in a secrets manager (AWS Secrets
    #Manager/KMS) and store only a reference to it in this column.
    signing_secret: Mapped[str]=mapped_column(String(255), nullable=False)

    created_at: Mapped[datetime]=mapped_column(server_default=func.now())
    is_active: Mapped[bool]=mapped_column(default=True, nullable=False)

    tenant=relationship("Tenant", back_populates="webhook_registrations")
