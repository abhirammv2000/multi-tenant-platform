"""create_deployment_service must never let a tenant deploy an image that hasn't finished
building, or deploy using another tenant's build job. Both checks happen before the tenant
lookup or any Kubernetes call, so they're tested directly against a mocked session.get,
without needing to mock k8s_resources. The success path is covered in
test_deployments_k8s_service.py instead, since it needs those mocked.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from control_plane.app.services.deployments import create_deployment_service, BuildJobNotReadyError
from control_plane.app.schemas.deployments import DeploymentCreate


def make_build_job(id=1, tenant_id=1, status="succeeded"):
    return SimpleNamespace(id=id, tenant_id=tenant_id, status=status)


def make_session(build_job):
    session = AsyncMock()
    session.get = AsyncMock(return_value=build_job)
    session.add = Mock() #session.add() is synchronous in real SQLAlchemy, unlike execute/commit/etc.
    session.commit = AsyncMock()
    session.refresh = AsyncMock()
    session.rollback = AsyncMock()
    return session


async def test_rejects_a_build_job_belonging_to_another_tenant():
    session = make_session(make_build_job(tenant_id=999))

    with pytest.raises(ValueError):
        await create_deployment_service(tenant_id=1, deploy_data=DeploymentCreate(build_job_id=1), session=session)


async def test_rejects_a_build_job_that_has_not_succeeded():
    session = make_session(make_build_job(tenant_id=1, status="building"))

    with pytest.raises(BuildJobNotReadyError):
        await create_deployment_service(tenant_id=1, deploy_data=DeploymentCreate(build_job_id=1), session=session)


async def test_rejects_when_the_build_job_does_not_exist():
    session = make_session(None)

    with pytest.raises(ValueError):
        await create_deployment_service(tenant_id=1, deploy_data=DeploymentCreate(build_job_id=999), session=session)
