from shared.db import Base
from sqlalchemy.orm import Mapped, mapped_column, relationship
from datetime import datetime
from sqlalchemy import String, func

class Tenant(Base):
    __tablename__="tenants"

    id: Mapped[int]=mapped_column(primary_key=True)
    name: Mapped[str]=mapped_column(String(255),nullable=False)
    #the tenant's isolation boundary: one Kubernetes namespace per tenant, created by the
    #control plane on tenant signup (Phase 3). Deterministic from the tenant's own id, but
    #stored explicitly since namespace creation is a fallible operation with its own
    #lifecycle (created / not yet created).
    k8s_namespace: Mapped[str]=mapped_column(String(63), nullable=False, unique=True) #63 is Kubernetes' own namespace name length limit
    created_at: Mapped[datetime]=mapped_column(server_default=func.now())
    is_active: Mapped[bool]=mapped_column(default=True, nullable=False)

    api_keys=relationship("APIKey", back_populates="tenant", cascade="all, delete-orphan")
    build_jobs=relationship("BuildJob", back_populates="tenant", cascade="all, delete-orphan")
    deployments=relationship("Deployment", back_populates="tenant", cascade="all, delete-orphan")
    webhook_registrations=relationship("WebhookRegistration", back_populates="tenant", cascade="all, delete-orphan")
