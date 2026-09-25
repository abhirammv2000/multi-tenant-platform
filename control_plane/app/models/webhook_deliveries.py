from shared.db import Base
from sqlalchemy.orm import Mapped, mapped_column, relationship
from datetime import datetime
from sqlalchemy import ForeignKey, String, JSON, func

class WebhookDelivery(Base):
    """Audit trail for every webhook attempt, same purpose as
    self-healing-data-platform's WebhookCallback model, extended with the signature and
    timestamp sent so a delivery's signing inputs are reconstructable later without the
    (never-stored) tenant secret."""
    __tablename__="webhook_deliveries"

    id: Mapped[int]=mapped_column(primary_key=True)
    webhook_registration_id: Mapped[int]=mapped_column(ForeignKey("webhook_registrations.id", ondelete="CASCADE"), index=True)
    tenant_id: Mapped[int]=mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)

    event: Mapped[str]=mapped_column(String(50), nullable=False) #"build.succeeded" | "build.failed" | "deployment.running" | "deployment.failed"
    payload: Mapped[dict]=mapped_column(JSON, nullable=False)
    signature: Mapped[str]=mapped_column(String(64), nullable=False) #hex HMAC-SHA256 digest actually sent
    timestamp: Mapped[int]=mapped_column(nullable=False) #unix seconds, the same value signed over, lets replay-rejection be audited later

    status: Mapped[str]=mapped_column(String(20), nullable=False) #"success" | "failed"
    http_status_code: Mapped[int|None]=mapped_column(nullable=True)
    error_message: Mapped[str|None]=mapped_column(nullable=True)

    created_at: Mapped[datetime]=mapped_column(server_default=func.now())

    webhook_registration=relationship("WebhookRegistration")
    tenant=relationship("Tenant")
