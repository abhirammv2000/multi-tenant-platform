from shared.db import Base
from sqlalchemy.orm import Mapped, mapped_column, relationship
from datetime import datetime
from sqlalchemy import ForeignKey, String, func

class BuildJob(Base):
    __tablename__="build_jobs"

    id: Mapped[int]=mapped_column(primary_key=True)
    tenant_id: Mapped[int]=mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)

    #the source the tenant submitted, a public or credentialed git repo. Phase 2 turns
    #this into a kpack Image custom resource.
    git_url: Mapped[str]=mapped_column(String(2048), nullable=False)
    git_revision: Mapped[str]=mapped_column(String(255), nullable=False, default="main")
    git_sub_path: Mapped[str|None]=mapped_column(String(1024), nullable=True) #for monorepos, maps to kpack's Image.spec.source.subPath

    #queued -> building -> succeeded | failed. Plain string, not a DB enum, validated at the
    #Pydantic schema layer, matches how kpack's own Build resource reports status.
    status: Mapped[str]=mapped_column(String(20), nullable=False, default="queued")

    #set once the build succeeds: the resolved, pushed image reference (registry/repo@sha256:...)
    image_tag: Mapped[str|None]=mapped_column(String(512), nullable=True)
    error_message: Mapped[str|None]=mapped_column(nullable=True)

    created_at: Mapped[datetime]=mapped_column(server_default=func.now())
    started_at: Mapped[datetime|None]=mapped_column(nullable=True)
    ended_at: Mapped[datetime|None]=mapped_column(nullable=True)

    tenant=relationship("Tenant", back_populates="build_jobs")
    deployments=relationship("Deployment", back_populates="build_job")
