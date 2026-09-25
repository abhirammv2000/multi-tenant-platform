"""create_build_job_service should mark a BuildJob "failed" with an error_message when the
Kubernetes calls (ensure_tenant_namespace / create_kpack_image) blow up, instead of leaving
it stuck at "queued" forever. This happened repeatedly during Phase 2 development (ECR
NAME_UNKNOWN, a stale kpack reconcile, webhook timeouts). k8s_resources calls are mocked
here since this tests the function's own error handling, not Kubernetes or kpack
themselves (those were verified live in Phase 2, see the README).
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from control_plane.app.services.build_jobs import create_build_job_service, _image_name, _image_tag
from control_plane.app.schemas.build_jobs import BuildJobCreate


def make_tenant(id=1, k8s_namespace="tenant-1"):
    return SimpleNamespace(id=id, k8s_namespace=k8s_namespace)


def make_session(tenant):
    session = AsyncMock()
    session.get = AsyncMock(return_value=tenant)
    session.add = Mock()
    session.commit = AsyncMock()
    session.refresh = AsyncMock()
    session.rollback = AsyncMock()
    return session


def test_image_naming_is_deterministic_and_collision_free_across_tenants():
    assert _image_name(42) == "build-42"
    assert _image_tag(tenant_id=1, build_job_id=5) != _image_tag(tenant_id=2, build_job_id=5)


async def test_raises_when_the_tenant_does_not_exist():
    session = make_session(tenant=None)

    with pytest.raises(ValueError):
        await create_build_job_service(tenant_id=999, build_data=BuildJobCreate(git_url="https://example.com/repo"), session=session)


@patch("control_plane.app.services.build_jobs.create_kpack_image")
@patch("control_plane.app.services.build_jobs.ensure_tenant_namespace")
async def test_marks_the_build_failed_when_the_k8s_call_raises(mock_ensure_ns, mock_create_image):
    mock_create_image.side_effect = RuntimeError("NAME_UNKNOWN: repository does not exist")
    session = make_session(make_tenant())

    build_job, namespace = await create_build_job_service(
        tenant_id=1, build_data=BuildJobCreate(git_url="https://example.com/repo"), session=session
    )

    assert build_job.status == "failed"
    assert "NAME_UNKNOWN" in build_job.error_message
    assert build_job.ended_at is not None
    assert namespace is None #signals the route: don't schedule a background poll for a build that never started


@patch("control_plane.app.services.build_jobs.create_kpack_image")
@patch("control_plane.app.services.build_jobs.ensure_tenant_namespace")
async def test_marks_the_build_building_when_the_k8s_calls_succeed(mock_ensure_ns, mock_create_image):
    session = make_session(make_tenant(id=1, k8s_namespace="tenant-1"))

    build_job, namespace = await create_build_job_service(
        tenant_id=1, build_data=BuildJobCreate(git_url="https://example.com/repo", git_sub_path="apps/api"), session=session
    )

    assert build_job.status == "building"
    assert build_job.started_at is not None
    assert namespace == "tenant-1"
    mock_create_image.assert_called_once()
    assert mock_create_image.call_args.kwargs["git_sub_path"] == "apps/api"
