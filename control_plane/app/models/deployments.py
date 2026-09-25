from shared.db import Base
from sqlalchemy.orm import Mapped, mapped_column, relationship
from datetime import datetime
from sqlalchemy import ForeignKey, String, func

class Deployment(Base):
    __tablename__="deployments"

    id: Mapped[int]=mapped_column(primary_key=True)
    tenant_id: Mapped[int]=mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    build_job_id: Mapped[int]=mapped_column(ForeignKey("build_jobs.id", ondelete="RESTRICT"), index=True) #a deployment always comes from a specific successful build

    #the Kubernetes Deployment's name inside the tenant's own namespace (Phase 3). Not
    #globally unique, scoped by tenant_id + k8s_namespace, same as any other tenant resource.
    k8s_deployment_name: Mapped[str]=mapped_column(String(253), nullable=False) #253 is Kubernetes' object name length limit

    #pending -> deploying -> running | failed | stopped
    status: Mapped[str]=mapped_column(String(20), nullable=False, default="pending")
    replica_count: Mapped[int]=mapped_column(default=1, nullable=False)

    created_at: Mapped[datetime]=mapped_column(server_default=func.now())
    updated_at: Mapped[datetime]=mapped_column(server_default=func.now(), onupdate=func.now())

    tenant=relationship("Tenant", back_populates="deployments")
    build_job=relationship("BuildJob", back_populates="deployments")
