"""create_deployment_service should never leave a Deployment row stuck when the Kubernetes
calls fail, same requirement as build_jobs.py's equivalent test. An ImagePullBackOff and an
unconfigured-client error both happened during Phase 3 development. K8s calls are mocked
here; the real cluster interaction was verified live, see README's Phase 3 section.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from control_plane.app.services.deployments import create_deployment_service, BuildJobNotReadyError
from control_plane.app.schemas.deployments import DeploymentCreate


def make_build_job(id=5, tenant_id=1, status="succeeded", image_tag="registry:tag"):
    return SimpleNamespace(id=id, tenant_id=tenant_id, status=status, image_tag=image_tag)


def make_tenant(id=1, k8s_namespace="tenant-1"):
    return SimpleNamespace(id=id, k8s_namespace=k8s_namespace)


def make_session(build_job, tenant):
    session = AsyncMock()
    #.get() is called twice: once for BuildJob, once for Tenant, return whichever matches
    async def get(model, _id):
        return build_job if model.__name__ == "BuildJob" else tenant
    session.get = AsyncMock(side_effect=get)
    session.add = Mock()
    session.commit = AsyncMock()
    session.refresh = AsyncMock()
    session.rollback = AsyncMock()
    return session


@patch("control_plane.app.services.deployments.create_tenant_deployment")
async def test_marks_the_deployment_failed_when_the_k8s_call_raises(mock_create_deployment):
    mock_create_deployment.side_effect = RuntimeError("no basic auth credentials")
    session = make_session(make_build_job(), make_tenant())

    deployment, namespace = await create_deployment_service(tenant_id=1, deploy_data=DeploymentCreate(build_job_id=5), session=session)

    assert deployment.status == "failed"
    assert namespace is None


@patch("control_plane.app.services.deployments.create_tenant_deployment")
async def test_marks_the_deployment_deploying_when_the_k8s_call_succeeds(mock_create_deployment):
    session = make_session(make_build_job(id=5, tenant_id=1, image_tag="registry:tenant-1-build-5"), make_tenant(id=1, k8s_namespace="tenant-1"))

    deployment, namespace = await create_deployment_service(tenant_id=1, deploy_data=DeploymentCreate(build_job_id=5, replica_count=2), session=session)

    assert deployment.status == "deploying"
    assert namespace == "tenant-1"
    #a stable name per tenant, not build-specific, see the comment in
    #create_deployment_service: it gives Phase 5's reverse proxy a fixed address to route
    #to regardless of which build is currently live.
    assert deployment.k8s_deployment_name == "tenant-app"
    mock_create_deployment.assert_called_once_with(k8s_namespace="tenant-1", image="registry:tenant-1-build-5", replica_count=2)


async def test_still_rejects_a_not_yet_succeeded_build_before_touching_k8s_at_all():
    session = make_session(make_build_job(status="building"), make_tenant())

    with pytest.raises(BuildJobNotReadyError):
        await create_deployment_service(tenant_id=1, deploy_data=DeploymentCreate(build_job_id=5), session=session)
