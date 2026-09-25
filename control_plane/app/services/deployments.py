import asyncio
from control_plane.app.models.deployments import Deployment
from control_plane.app.models.build_jobs import BuildJob
from control_plane.app.models.tenants import Tenant
from control_plane.app.schemas.deployments import DeploymentCreate
from control_plane.app.k8s_resources import create_tenant_deployment, get_deployment_status, TENANT_APP_NAME
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy import select
from shared.db import async_session
from shared.utils import now_naive
from shared.observability import get_logger
from shared.webhook_dispatcher import dispatch_tenant_webhooks

log=get_logger(__name__)

POLL_INTERVAL_SECONDS=5
POLL_TIMEOUT_SECONDS=180 #a Deployment rollout is much faster than a build, the image is already built and pushed

class BuildJobNotReadyError(Exception):
    pass

async def create_deployment_service(tenant_id: int, deploy_data: DeploymentCreate, session: AsyncSession):
    build_job=await session.get(BuildJob, deploy_data.build_job_id)
    if not build_job or build_job.tenant_id!=tenant_id:
        raise ValueError("build_job_id does not belong to this tenant")
    if build_job.status!="succeeded":
        raise BuildJobNotReadyError(f"build_job {build_job.id} is '{build_job.status}', not 'succeeded'")

    tenant=await session.get(Tenant, tenant_id)

    #a stable name per tenant, not tied to this build. A redeploy updates the same
    #Deployment/Service in place (create_tenant_deployment patches on 409) rather than
    #accumulating one per build, giving Phase 5's reverse proxy one fixed, discoverable
    #address per tenant regardless of which build is currently live.
    deployment=Deployment(
        tenant_id=tenant_id,
        build_job_id=build_job.id,
        k8s_deployment_name=TENANT_APP_NAME,
        status="pending",
        replica_count=deploy_data.replica_count,
    )
    try:
        session.add(deployment)
        await session.commit()
        await session.refresh(deployment)
    except SQLAlchemyError:
        await session.rollback()
        raise

    try:
        create_tenant_deployment(
            k8s_namespace=tenant.k8s_namespace,
            image=build_job.image_tag,
            replica_count=deployment.replica_count,
        )
        deployment.status="deploying"
        await session.commit()
        await session.refresh(deployment)
    except Exception as e:
        log.error("k8s_deployment_creation_failed", deployment_id=deployment.id, error=str(e))
        deployment.status="failed"
        await session.commit()
        await session.refresh(deployment)

    return deployment, tenant.k8s_namespace if deployment.status=="deploying" else None


async def poll_deployment_until_running(deployment_id: int, k8s_namespace: str, deployment_name: str):
    """Same shape as build_jobs.poll_build_until_done: a FastAPI BackgroundTask with its
    own DB session, polling the Deployment's rollout status until it settles."""
    elapsed=0
    while elapsed<POLL_TIMEOUT_SECONDS:
        await asyncio.sleep(POLL_INTERVAL_SECONDS)
        elapsed+=POLL_INTERVAL_SECONDS

        status=get_deployment_status(k8s_namespace, deployment_name)
        if status["running"] is None:
            continue

        async with async_session() as session:
            deployment=await session.get(Deployment, deployment_id)
            if not deployment:
                return
            deployment.status="running" if status["running"] else "failed"
            await session.commit()
            log.info("deployment_status_resolved", deployment_id=deployment_id, status=deployment.status)

            await dispatch_tenant_webhooks(
                tenant_id=deployment.tenant_id,
                event=f"deployment.{deployment.status}",
                payload={
                    "deployment_id": deployment.id, "tenant_id": deployment.tenant_id,
                    "status": deployment.status, "k8s_deployment_name": deployment.k8s_deployment_name,
                },
                session=session,
            )
        return

    async with async_session() as session:
        deployment=await session.get(Deployment, deployment_id)
        if deployment and deployment.status=="deploying":
            deployment.status="failed"
            await session.commit()
            log.error("deployment_timed_out", deployment_id=deployment_id)


async def get_deployment_service(tenant_id: int, deployment_id: int, session: AsyncSession):
    statement=select(Deployment).where(Deployment.id==deployment_id, Deployment.tenant_id==tenant_id)
    result=await session.execute(statement)
    return result.scalar_one_or_none()

async def get_all_deployments_service(tenant_id: int, session: AsyncSession, limit: int|None=None, offset: int=0):
    statement=select(Deployment).where(Deployment.tenant_id==tenant_id).order_by(Deployment.id.desc()).offset(offset)
    if limit is not None:
        statement=statement.limit(limit)
    result=await session.execute(statement)
    return result.scalars().all()
