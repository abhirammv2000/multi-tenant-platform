import asyncio
from control_plane.app.models.build_jobs import BuildJob
from control_plane.app.models.tenants import Tenant
from control_plane.app.schemas.build_jobs import BuildJobCreate
from control_plane.app.k8s_resources import ensure_tenant_namespace, create_kpack_image, get_build_status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy import select
from shared.db import async_session
from shared.utils import now_naive
from shared.observability import get_logger
from shared.webhook_dispatcher import dispatch_tenant_webhooks

log=get_logger(__name__)

POLL_INTERVAL_SECONDS=5
POLL_TIMEOUT_SECONDS=900 #15 minutes, generous for a Node.js buildpack build, matches Phase 0's build times


def _image_name(build_job_id: int) -> str:
    return f"build-{build_job_id}"


def _image_tag(tenant_id: int, build_job_id: int) -> str:
    #hyphen, not slash: ECR treats a slash as a nested repository path, which doesn't
    #auto-create the way a Docker Hub namespace does. Every build's image lives in the
    #same IMAGE_REGISTRY repo, distinguished only by this tag.
    return f"tenant-{tenant_id}-build-{build_job_id}"


async def create_build_job_service(tenant_id: int, build_data: BuildJobCreate, session: AsyncSession):
    tenant=await session.get(Tenant, tenant_id)
    if not tenant:
        raise ValueError(f"tenant {tenant_id} does not exist")

    build_job=BuildJob(tenant_id=tenant_id, git_url=build_data.git_url, git_revision=build_data.git_revision, git_sub_path=build_data.git_sub_path, status="queued")
    try:
        session.add(build_job)
        await session.commit()
        await session.refresh(build_job)
    except SQLAlchemyError:
        await session.rollback()
        raise

    #the actual cluster calls happen after the DB row exists and is committed, so a build
    #that fails to even start still has a queryable row explaining why (status="failed",
    #error_message set) instead of silently vanishing.
    try:
        ensure_tenant_namespace(tenant.k8s_namespace)
        create_kpack_image(
            k8s_namespace=tenant.k8s_namespace,
            image_name=_image_name(build_job.id),
            git_url=build_job.git_url,
            git_revision=build_job.git_revision,
            git_sub_path=build_job.git_sub_path,
            image_tag=_image_tag(tenant_id, build_job.id),
        )
        build_job.status="building"
        build_job.started_at=now_naive()
        await session.commit()
        await session.refresh(build_job)
    except Exception as e:
        log.error("kpack_image_creation_failed", build_job_id=build_job.id, error=str(e))
        build_job.status="failed"
        build_job.error_message=f"Failed to start build: {e}"
        build_job.ended_at=now_naive()
        await session.commit()
        await session.refresh(build_job)

    return build_job, tenant.k8s_namespace if build_job.status=="building" else None


async def poll_build_until_done(build_job_id: int, k8s_namespace: str):
    """Runs as a FastAPI BackgroundTask, outside the request/response cycle, so it opens its
    own DB session (the request's session closes once the response is sent). Polls kpack's
    Image status until it resolves or POLL_TIMEOUT_SECONDS elapses."""
    image_name=_image_name(build_job_id)
    elapsed=0

    while elapsed<POLL_TIMEOUT_SECONDS:
        await asyncio.sleep(POLL_INTERVAL_SECONDS)
        elapsed+=POLL_INTERVAL_SECONDS

        status=get_build_status(k8s_namespace, image_name)
        if status["ready"] is None:
            continue #still building, or the Image resource hasn't reported a condition yet

        async with async_session() as session:
            build_job=await session.get(BuildJob, build_job_id)
            if not build_job:
                return
            build_job.ended_at=now_naive()
            if status["ready"]:
                build_job.status="succeeded"
                build_job.image_tag=status.get("latest_image")
                log.info("build_succeeded", build_job_id=build_job_id, image_tag=build_job.image_tag)
            else:
                build_job.status="failed"
                build_job.error_message=status.get("error_message", "build failed")
                log.info("build_failed", build_job_id=build_job_id, error=build_job.error_message)
            await session.commit()

            await dispatch_tenant_webhooks(
                tenant_id=build_job.tenant_id,
                event=f"build.{build_job.status}",
                payload={
                    "build_job_id": build_job.id, "tenant_id": build_job.tenant_id,
                    "status": build_job.status, "image_tag": build_job.image_tag,
                    "error_message": build_job.error_message,
                },
                session=session,
            )
        return

    async with async_session() as session:
        build_job=await session.get(BuildJob, build_job_id)
        if build_job and build_job.status=="building":
            build_job.status="failed"
            build_job.error_message=f"Build did not complete within {POLL_TIMEOUT_SECONDS}s"
            build_job.ended_at=now_naive()
            await session.commit()
            log.error("build_timed_out", build_job_id=build_job_id)


async def get_build_job_service(tenant_id: int, build_job_id: int, session: AsyncSession):
    statement=select(BuildJob).where(BuildJob.id==build_job_id, BuildJob.tenant_id==tenant_id)
    result=await session.execute(statement)
    return result.scalar_one_or_none()

async def get_all_build_jobs_service(tenant_id: int, session: AsyncSession, limit: int|None=None, offset: int=0):
    statement=select(BuildJob).where(BuildJob.tenant_id==tenant_id).order_by(BuildJob.id.desc()).offset(offset)
    if limit is not None:
        statement=statement.limit(limit)
    result=await session.execute(statement)
    return result.scalars().all()
