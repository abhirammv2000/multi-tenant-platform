"""The isolation a tenant gets is built from Kubernetes objects that this code creates. Nothing else
checks them without a cluster, so these tests record every object sent to the (fake) API and check
the properties the isolation story rests on: the namespace is labelled for the `restricted` Pod
Security Standard, the quota and the default-deny network policy exist and say what they should, a
tenant pod meets every `restricted` rule, and the largest allowed replica count fits the quota.

They check what the code asks the API to create. That the cluster then enforces it was checked by
hand on real clusters (see the README's phases); nothing here replaces that.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from kubernetes import client as k8s
from kubernetes.client.exceptions import ApiException
from kubernetes.utils import parse_quantity

from control_plane.app import k8s_resources as kr
from control_plane.app.schemas.deployments import DeploymentCreate

DNS_IP = "10.96.0.10"


class FakeCluster:
    """Records the body of every create call, keyed by what kind of object it was."""

    def __init__(self, dns_ip=DNS_IP, existing=()):
        self.created = {}
        self.existing = set(existing)  # kinds that answer 409, as when the object already exists
        self.core, self.networking, self.apps, self.rbac = MagicMock(), MagicMock(), MagicMock(), MagicMock()
        if dns_ip:
            self.core.read_namespaced_service.return_value = SimpleNamespace(spec=SimpleNamespace(cluster_ip=dns_ip))
        else:
            self.core.read_namespaced_service.side_effect = ApiException(status=404)
        self._record(self.core, "create_namespace", "Namespace", positional=True)
        for method, kind in [
            ("create_namespaced_resource_quota", "ResourceQuota"),
            ("create_namespaced_limit_range", "LimitRange"),
            ("create_namespaced_secret", "Secret"),
            ("create_namespaced_service_account", "ServiceAccount"),
            ("create_namespaced_service", "Service"),
        ]:
            self._record(self.core, method, kind)
        self._record(self.networking, "create_namespaced_network_policy", "NetworkPolicy")
        self._record(self.apps, "create_namespaced_deployment", "Deployment")
        self._record(self.rbac, "create_namespaced_role", "Role")
        self._record(self.rbac, "create_namespaced_role_binding", "RoleBinding")

    def _record(self, api, method, kind, positional=False):
        def create(*args, **kwargs):
            body = args[0] if positional else kwargs["body"]
            self.created.setdefault(kind, []).append(body)
            if kind in self.existing:
                raise ApiException(status=409)

        getattr(api, method).side_effect = create

    def one(self, kind):
        assert len(self.created.get(kind, [])) == 1, f"expected exactly one {kind}, got {self.created.get(kind)}"
        return self.created[kind][0]

    def patch_k8s(self):
        names = "control_plane.app.k8s_resources."
        stack = [
            patch(names + "get_core_v1_api", return_value=self.core),
            patch(names + "get_networking_v1_api", return_value=self.networking),
            patch(names + "get_apps_v1_api", return_value=self.apps),
            patch(names + "get_rbac_v1_api", return_value=self.rbac),
        ]
        return _All(stack)


class _All:
    def __init__(self, patches):
        self.patches = patches

    def __enter__(self):
        for p in self.patches:
            p.start()

    def __exit__(self, *exc):
        for p in self.patches:
            p.stop()


@pytest.fixture
def cluster():
    fake = FakeCluster()
    with fake.patch_k8s():
        yield fake


def tenant_pod_spec(cluster_):
    kr.create_tenant_deployment("tenant-7", "registry/app:tag", replica_count=1)
    return cluster_.one("Deployment").spec.template.spec


# ---- the namespace -------------------------------------------------------------------------------


def test_the_namespace_enforces_the_restricted_pod_security_standard(cluster):
    kr.ensure_tenant_namespace("tenant-7")

    namespace = cluster.one("Namespace")
    assert namespace.metadata.name == "tenant-7"
    assert namespace.metadata.labels["pod-security.kubernetes.io/enforce"] == "restricted"


def test_the_quota_caps_cpu_memory_and_pod_count(cluster):
    kr.ensure_tenant_namespace("tenant-7")

    hard = cluster.one("ResourceQuota").spec.hard
    for key in ("requests.cpu", "requests.memory", "limits.cpu", "limits.memory", "pods"):
        assert key in hard, f"the quota does not limit {key}"


def test_containers_without_a_declared_size_get_defaults(cluster):
    kr.ensure_tenant_namespace("tenant-7")

    item = cluster.one("LimitRange").spec.limits[0]
    assert item.type == "Container"
    assert set(item.default) == {"cpu", "memory"} and set(item.default_request) == {"cpu", "memory"}


def test_the_network_policy_denies_everything_except_dns(cluster):
    kr.ensure_tenant_namespace("tenant-7")

    spec = cluster.one("NetworkPolicy").spec
    assert spec.pod_selector.match_labels is None and spec.pod_selector.match_expressions is None  # selects every pod
    assert set(spec.policy_types) == {"Ingress", "Egress"}
    assert spec.ingress == []
    assert len(spec.egress) == 1
    rule = spec.egress[0]
    assert [peer.ip_block.cidr for peer in rule.to] == [f"{DNS_IP}/32"]
    assert {(p.protocol, p.port) for p in rule.ports} == {("UDP", 53), ("TCP", 53)}


def test_if_the_dns_service_cannot_be_found_the_policy_allows_no_egress_at_all():
    fake = FakeCluster(dns_ip=None)
    with fake.patch_k8s():
        kr.ensure_tenant_namespace("tenant-7")

    spec = fake.one("NetworkPolicy").spec
    assert spec.egress == [] and spec.ingress == []


def test_nothing_in_the_namespace_setup_opens_the_internet(cluster):
    kr.ensure_tenant_namespace("tenant-7")

    text = str(cluster.one("NetworkPolicy"))
    assert "0.0.0.0/0" not in text and "::/0" not in text


def test_running_it_again_when_everything_exists_is_not_an_error():
    fake = FakeCluster(existing={"Namespace", "ResourceQuota", "LimitRange", "NetworkPolicy", "Secret", "ServiceAccount"})
    with fake.patch_k8s():
        kr.ensure_tenant_namespace("tenant-7")

    for kind in ("Namespace", "ResourceQuota", "LimitRange", "NetworkPolicy", "Secret", "ServiceAccount"):
        assert len(fake.created[kind]) == 1, f"{kind} was not attempted"


def test_an_error_that_is_not_already_exists_is_not_swallowed(cluster):
    cluster.core.create_namespaced_resource_quota.side_effect = ApiException(status=500)

    with pytest.raises(ApiException):
        kr.ensure_tenant_namespace("tenant-7")


# ---- the tenant pod ------------------------------------------------------------------------------

ALLOWED_VOLUME_TYPES = {
    "configMap", "csi", "downwardAPI", "emptyDir", "ephemeral", "persistentVolumeClaim", "projected", "secret",
}


def restricted_violations(pod_spec):
    """The rules of the `restricted` Pod Security Standard that apply to a pod spec, written out from the
    Kubernetes documentation. Returns a list of what is broken, empty if the pod is compliant."""
    broken = []
    pod_ctx = pod_spec.security_context
    if pod_spec.host_network or pod_spec.host_pid or pod_spec.host_ipc:
        broken.append("shares a host namespace")
    for volume in pod_spec.volumes or []:
        kind = next((name for name in volume.attribute_map if name != "name" and getattr(volume, name, None) is not None), None)
        if kind and kind not in ALLOWED_VOLUME_TYPES:
            broken.append(f"volume type {kind}")
    if pod_ctx and pod_ctx.run_as_user == 0:
        broken.append("pod runs as uid 0")
    if pod_ctx and pod_ctx.sysctls:
        broken.append("sets sysctls")
    for c in (pod_spec.init_containers or []) + pod_spec.containers:
        ctx = c.security_context
        if ctx is None:
            broken.append(f"{c.name}: no securityContext")
            continue
        if ctx.privileged:
            broken.append(f"{c.name}: privileged")
        if ctx.allow_privilege_escalation is not False:
            broken.append(f"{c.name}: allowPrivilegeEscalation is not false")
        if not (ctx.capabilities and ctx.capabilities.drop and "ALL" in ctx.capabilities.drop):
            broken.append(f"{c.name}: does not drop ALL capabilities")
        if ctx.capabilities and set(ctx.capabilities.add or []) - {"NET_BIND_SERVICE"}:
            broken.append(f"{c.name}: adds capabilities")
        non_root = ctx.run_as_non_root if ctx.run_as_non_root is not None else (pod_ctx.run_as_non_root if pod_ctx else None)
        if non_root is not True:
            broken.append(f"{c.name}: runAsNonRoot is not true")
        if ctx.run_as_user == 0:
            broken.append(f"{c.name}: runs as uid 0")
        seccomp = ctx.seccomp_profile or (pod_ctx.seccomp_profile if pod_ctx else None)
        if seccomp is None or seccomp.type not in ("RuntimeDefault", "Localhost"):
            broken.append(f"{c.name}: no seccomp profile")
        if ctx.se_linux_options and (ctx.se_linux_options.type not in (None, "container_t", "container_init_t", "container_kvm_t") or ctx.se_linux_options.user or ctx.se_linux_options.role):
            broken.append(f"{c.name}: custom SELinux options")
        for port in c.ports or []:
            if port.host_port:
                broken.append(f"{c.name}: uses a hostPort")
    return broken


def test_the_tenant_pod_meets_every_restricted_rule(cluster):
    assert restricted_violations(tenant_pod_spec(cluster)) == []


def test_the_tenant_pod_cannot_call_the_kubernetes_api_with_a_mounted_token(cluster):
    assert tenant_pod_spec(cluster).automount_service_account_token is False


def test_the_tenant_pod_runs_with_resource_limits(cluster):
    container = tenant_pod_spec(cluster).containers[0]
    assert set(container.resources.limits) >= {"cpu", "memory"}
    assert set(container.resources.requests) >= {"cpu", "memory"}


def test_the_tenant_pod_does_not_mount_host_paths_or_share_host_namespaces(cluster):
    pod = tenant_pod_spec(cluster)
    assert not pod.volumes
    assert not (pod.host_network or pod.host_pid or pod.host_ipc)


def test_the_checker_itself_notices_each_violation():
    """A compliance test that cannot fail proves nothing, so feed the checker pods that are wrong."""

    def pod(**container_overrides):
        base = dict(
            run_as_non_root=True, allow_privilege_escalation=False, capabilities=k8s.V1Capabilities(drop=["ALL"]),
            seccomp_profile=k8s.V1SeccompProfile(type="RuntimeDefault"),
        )
        base.update(container_overrides)
        return k8s.V1PodSpec(containers=[k8s.V1Container(name="app", security_context=k8s.V1SecurityContext(**base))])

    assert restricted_violations(pod()) == []
    cases = {
        "privileged": pod(privileged=True),
        "escalation": pod(allow_privilege_escalation=True),
        "escalation unset": pod(allow_privilege_escalation=None),
        "capabilities kept": pod(capabilities=k8s.V1Capabilities(drop=["NET_RAW"])),
        "capability added": pod(capabilities=k8s.V1Capabilities(drop=["ALL"], add=["SYS_ADMIN"])),
        "root allowed": pod(run_as_non_root=False),
        "uid 0": pod(run_as_user=0),
        "no seccomp": pod(seccomp_profile=None),
        "unconfined seccomp": pod(seccomp_profile=k8s.V1SeccompProfile(type="Unconfined")),
    }
    for name, spec in cases.items():
        assert restricted_violations(spec), f"the checker missed: {name}"
    host = pod()
    host.host_network = True
    assert restricted_violations(host)
    mounted = pod()
    mounted.volumes = [k8s.V1Volume(name="v", host_path=k8s.V1HostPathVolumeSource(path="/"))]
    assert restricted_violations(mounted)
    bare = k8s.V1PodSpec(containers=[k8s.V1Container(name="app")])
    assert restricted_violations(bare)


# ---- the quota and the largest deployment --------------------------------------------------------


def _cpu_and_memory(resources):
    return parse_quantity(resources["cpu"]), parse_quantity(resources["memory"])


def test_the_largest_allowed_replica_count_fits_the_quota():
    limit_cpu, limit_mem = _cpu_and_memory(kr.TENANT_CONTAINER_LIMITS)
    request_cpu, request_mem = _cpu_and_memory(kr.TENANT_CONTAINER_REQUESTS)
    hard = kr.TENANT_QUOTA_HARD
    largest_that_fits = int(min(
        parse_quantity(hard["limits.cpu"]) // limit_cpu,
        parse_quantity(hard["limits.memory"]) // limit_mem,
        parse_quantity(hard["requests.cpu"]) // request_cpu,
        parse_quantity(hard["requests.memory"]) // request_mem,
        parse_quantity(hard["pods"]),
    ))

    schema_maximum = DeploymentCreate.model_fields["replica_count"].metadata
    maximum = next(m.le for m in schema_maximum if hasattr(m, "le"))

    assert maximum == largest_that_fits, (
        f"the API allows {maximum} replicas but the quota fits {largest_that_fits}; "
        "pods beyond that are rejected by the quota and the rollout never finishes"
    )


def test_a_request_for_more_replicas_than_fit_is_refused_before_anything_is_created():
    with pytest.raises(ValueError):
        DeploymentCreate(build_job_id=1, replica_count=9)
    assert DeploymentCreate(build_job_id=1, replica_count=8).replica_count == 8


# ---- the viewer credentials a tenant can download ------------------------------------------------


def test_the_tenant_role_is_read_only_on_pods_and_logs_in_its_own_namespace(cluster):
    kr.ensure_tenant_rbac("tenant-7")

    role = cluster.one("Role")
    assert role.metadata.namespace == "tenant-7"
    assert len(role.rules) == 1
    rule = role.rules[0]
    assert set(rule.resources) == {"pods", "pods/log"}
    assert set(rule.verbs) == {"get", "list"}
    assert rule.api_groups == [""]


def test_the_tenant_role_grants_no_exec_secrets_or_wildcards(cluster):
    kr.ensure_tenant_rbac("tenant-7")

    rule = cluster.one("Role").rules[0]
    forbidden = {"*", "pods/exec", "pods/attach", "pods/portforward", "secrets", "serviceaccounts/token"}
    assert not forbidden & set(rule.resources)
    assert not {"*", "create", "update", "patch", "delete", "deletecollection", "watch"} & set(rule.verbs)


def test_the_binding_is_a_namespaced_rolebinding_to_the_viewer_account_only(cluster):
    kr.ensure_tenant_rbac("tenant-7")

    binding = cluster.one("RoleBinding")
    assert binding.role_ref.kind == "Role"  # not a ClusterRole
    assert [(s.kind, s.name, s.namespace) for s in binding.subjects] == [
        ("ServiceAccount", kr.TENANT_VIEWER_SERVICE_ACCOUNT_NAME, "tenant-7")
    ]
