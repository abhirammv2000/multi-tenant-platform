"""Everything here talks to the Kubernetes API directly, no ORM, no DB session. Kept
separate from control_plane/app/services/ (which only talks to Postgres): a service
function decides what should happen and persists it, these functions make it happen
against the cluster.
"""
import base64
import json
from kubernetes import client as k8s
from kubernetes.client.exceptions import ApiException
from shared.k8s_client import get_core_v1_api, get_custom_objects_api, get_apps_v1_api, get_networking_v1_api, get_rbac_v1_api, KPACK_GROUP, KPACK_VERSION
from shared.config import IMAGE_REGISTRY, REGISTRY_SERVER, REGISTRY_USERNAME, REGISTRY_PASSWORD, PLATFORM_CLUSTER_BUILDER_NAME
from shared.observability import get_logger

log=get_logger(__name__)

REGISTRY_SECRET_NAME="tenant-registry-credentials"
TENANT_SERVICE_ACCOUNT_NAME="tenant-build-sa"


def _docker_config_json() -> str:
    #same format `kubectl create secret docker-registry` produces, a base64-in-JSON
    #.dockerconfigjson blob, which is what kpack's build pods expect for registry auth.
    auth=base64.b64encode(f"{REGISTRY_USERNAME}:{REGISTRY_PASSWORD}".encode()).decode()
    config={"auths":{REGISTRY_SERVER:{"username":REGISTRY_USERNAME,"password":REGISTRY_PASSWORD,"auth":auth}}}
    return json.dumps(config)


def _cluster_dns_ip() -> str|None:
    #ClusterIP of the cluster's DNS Service. Every cluster exposes this under the same name
    #regardless of what implements DNS underneath (CoreDNS pods on minikube, a
    #not-directly-visible resolver on EKS Auto Mode). Reading the Service's ClusterIP is
    #more portable than AWS's "derive from the Service CIDR's .10 address" trick, which is
    #Auto-Mode-specific.
    core_v1=get_core_v1_api()
    try:
        svc=core_v1.read_namespaced_service("kube-dns", "kube-system")
        return svc.spec.cluster_ip
    except ApiException as e:
        log.warning("cluster_dns_service_lookup_failed", error=str(e))
        return None


def ensure_tenant_namespace(k8s_namespace: str):
    """Idempotent: creates the tenant's namespace (labeled for Pod Security Admission
    `restricted`, with a ResourceQuota/LimitRange and a default-deny NetworkPolicy applied
    at creation time), its registry-push Secret, and the ServiceAccount kpack Image
    resources will use, all if they don't already exist. Safe to call on every build
    request, not just once at tenant signup."""
    core_v1=get_core_v1_api()

    try:
        namespace=k8s.V1Namespace(
            metadata=k8s.V1ObjectMeta(
                name=k8s_namespace,
                #enforce (not warn/audit) restricted PSA: Phase 0 confirmed a busybox pod
                #without a compliant securityContext gets rejected under this label.
                labels={"pod-security.kubernetes.io/enforce": "restricted"},
            )
        )
        core_v1.create_namespace(namespace)
        log.info("tenant_namespace_created", namespace=k8s_namespace)
    except ApiException as e:
        if e.status!=409: #409 = already exists, fine. anything else is a real error
            raise

    try:
        quota=k8s.V1ResourceQuota(
            metadata=k8s.V1ObjectMeta(name="tenant-quota", namespace=k8s_namespace),
            spec=k8s.V1ResourceQuotaSpec(hard={
                "requests.cpu": "2", "requests.memory": "4Gi",
                "limits.cpu": "4", "limits.memory": "8Gi",
                "pods": "20",
            }),
        )
        core_v1.create_namespaced_resource_quota(namespace=k8s_namespace, body=quota)
        log.info("tenant_resource_quota_created", namespace=k8s_namespace)
    except ApiException as e:
        if e.status!=409:
            raise

    try:
        limit_range=k8s.V1LimitRange(
            metadata=k8s.V1ObjectMeta(name="tenant-limit-range", namespace=k8s_namespace),
            spec=k8s.V1LimitRangeSpec(limits=[k8s.V1LimitRangeItem(
                type="Container",
                default={"cpu": "500m", "memory": "512Mi"},
                default_request={"cpu": "100m", "memory": "128Mi"},
            )]),
        )
        core_v1.create_namespaced_limit_range(namespace=k8s_namespace, body=limit_range)
        log.info("tenant_limit_range_created", namespace=k8s_namespace)
    except ApiException as e:
        if e.status!=409:
            raise

    try:
        networking_v1=get_networking_v1_api()
        dns_ip=_cluster_dns_ip()
        egress_rules=[]
        if dns_ip: #same gap found in Phase 0: a namespaceSelector rule doesn't reliably
            #reach cluster DNS everywhere (EKS Auto Mode's CoreDNS isn't a normal pod), so
            #this targets the DNS Service's ClusterIP directly via ipBlock.
            egress_rules.append(k8s.V1NetworkPolicyEgressRule(
                to=[k8s.V1NetworkPolicyPeer(ip_block=k8s.V1IPBlock(cidr=f"{dns_ip}/32"))],
                ports=[k8s.V1NetworkPolicyPort(protocol="UDP", port=53), k8s.V1NetworkPolicyPort(protocol="TCP", port=53)],
            ))
        policy=k8s.V1NetworkPolicy(
            metadata=k8s.V1ObjectMeta(name="tenant-default-deny", namespace=k8s_namespace),
            spec=k8s.V1NetworkPolicySpec(
                pod_selector=k8s.V1LabelSelector(),
                policy_types=["Ingress", "Egress"],
                ingress=[], #no ingress from anywhere, Phase 5's reverse proxy adds its own rule explicitly
                egress=egress_rules,
            ),
        )
        networking_v1.create_namespaced_network_policy(namespace=k8s_namespace, body=policy)
        log.info("tenant_network_policy_created", namespace=k8s_namespace, dns_ip=dns_ip)
    except ApiException as e:
        if e.status!=409:
            raise

    try:
        secret=k8s.V1Secret(
            metadata=k8s.V1ObjectMeta(name=REGISTRY_SECRET_NAME, namespace=k8s_namespace),
            type="kubernetes.io/dockerconfigjson",
            string_data={".dockerconfigjson": _docker_config_json()},
        )
        core_v1.create_namespaced_secret(namespace=k8s_namespace, body=secret)
        log.info("tenant_registry_secret_created", namespace=k8s_namespace)
    except ApiException as e:
        if e.status!=409:
            raise

    try:
        sa=k8s.V1ServiceAccount(
            metadata=k8s.V1ObjectMeta(name=TENANT_SERVICE_ACCOUNT_NAME, namespace=k8s_namespace),
            secrets=[k8s.V1ObjectReference(name=REGISTRY_SECRET_NAME)],
            image_pull_secrets=[k8s.V1LocalObjectReference(name=REGISTRY_SECRET_NAME)],
        )
        core_v1.create_namespaced_service_account(namespace=k8s_namespace, body=sa)
        log.info("tenant_service_account_created", namespace=k8s_namespace)
    except ApiException as e:
        if e.status!=409:
            raise


def create_kpack_image(k8s_namespace: str, image_name: str, git_url: str, git_revision: str, image_tag: str, git_sub_path: str|None=None):
    """Creates the kpack Image custom resource that triggers a build. Returns once the
    resource is created, the build itself runs asynchronously in the cluster;
    get_build_status() below is how the caller finds out how it's going."""
    custom_objects=get_custom_objects_api()

    source={"git": {"url": git_url, "revision": git_revision}}
    if git_sub_path: #kpack's SourceConfig has subPath as a sibling of git, not nested
        #inside it (confirmed against kpack's Go source, corev1alpha1.SourceConfig, after
        #an earlier attempt that nested it inside `git` silently no-op'd).
        source["subPath"]=git_sub_path

    image_manifest={
        "apiVersion": f"{KPACK_GROUP}/{KPACK_VERSION}",
        "kind": "Image",
        "metadata": {"name": image_name, "namespace": k8s_namespace},
        "spec": {
            #colon-separated tag within IMAGE_REGISTRY's single repo, not a slash-separated
            #nested path. ECR doesn't auto-create a new repository per path segment like
            #Docker Hub does (a NAME_UNKNOWN push failure during Phase 2 caught this). Every
            #tenant/build's image lives in the same ECR repo, distinguished only by tag.
            "tag": f"{IMAGE_REGISTRY}:{image_tag}",
            "serviceAccountName": TENANT_SERVICE_ACCOUNT_NAME,
            "builder": {"name": PLATFORM_CLUSTER_BUILDER_NAME, "kind": "ClusterBuilder"},
            "source": source,
        },
    }

    custom_objects.create_namespaced_custom_object(
        group=KPACK_GROUP, version=KPACK_VERSION, namespace=k8s_namespace, plural="images", body=image_manifest
    )
    log.info("kpack_image_created", namespace=k8s_namespace, image_name=image_name)


def get_build_status(k8s_namespace: str, image_name: str) -> dict:
    """Reads the Image resource's status. Returns a dict with at least `ready` (True/False/
    None-if-unknown-yet) and, once resolved, `latest_image` or `error_message`."""
    custom_objects=get_custom_objects_api()

    try:
        image=custom_objects.get_namespaced_custom_object(
            group=KPACK_GROUP, version=KPACK_VERSION, namespace=k8s_namespace, plural="images", name=image_name
        )
    except ApiException as e:
        if e.status==404:
            return {"ready": None, "detail": "Image resource not found yet"}
        raise

    status=image.get("status", {})
    conditions=status.get("conditions", [])
    ready_condition=next((c for c in conditions if c.get("type")=="Ready"), None)

    if ready_condition is None:
        return {"ready": None, "detail": "no Ready condition yet"}

    if ready_condition.get("status")=="True":
        return {"ready": True, "latest_image": status.get("latestImage")}

    if ready_condition.get("status")=="False":
        return {"ready": False, "error_message": ready_condition.get("message", "build failed")}

    return {"ready": None, "detail": ready_condition.get("message", "build in progress")}


#one stable Deployment+Service name per tenant namespace, not tied to a build_job_id. A
#redeploy upgrades this same app in place (create_tenant_deployment patches the image if it
#already exists), which gives the reverse proxy one fixed, discoverable address per tenant
#(`tenant-app` in their own namespace) regardless of which build is currently live.
TENANT_APP_NAME="tenant-app"
TENANT_APP_PORT=8080 #the $PORT convention Paketo's node-start buildpack launches on (checked against Phase 0/2's builds)


def create_tenant_deployment(k8s_namespace: str, image: str, replica_count: int, deployment_name: str=TENANT_APP_NAME):
    """Creates (or, on redeploy, updates in place) the Kubernetes Deployment running a
    tenant's successfully-built image inside their own isolated namespace, plus a matching
    ClusterIP Service, the fixed address Phase 5's reverse proxy routes to."""
    apps_v1=get_apps_v1_api()
    core_v1=get_core_v1_api()

    #restricted-PSA-compliant securityContext, matching what kpack's own build pods run
    #under. The tenant's namespace enforces this, so a non-compliant spec gets rejected.
    container=k8s.V1Container(
        name="app",
        image=image,
        ports=[k8s.V1ContainerPort(container_port=TENANT_APP_PORT)],
        security_context=k8s.V1SecurityContext(
            run_as_non_root=True,
            allow_privilege_escalation=False,
            capabilities=k8s.V1Capabilities(drop=["ALL"]),
            seccomp_profile=k8s.V1SeccompProfile(type="RuntimeDefault"),
        ),
        resources=k8s.V1ResourceRequirements(
            requests={"cpu": "100m", "memory": "128Mi"}, limits={"cpu": "500m", "memory": "512Mi"}
        ),
    )
    pod_spec=k8s.V1PodSpec(
        containers=[container],
        #reuses the ServiceAccount ensure_tenant_namespace() already wired with the
        #registry pull secret, since the build image lives in the same registry kpack
        #pushed it to. Without this, an ImagePullBackOff ("no basic auth credentials") hit
        #during Phase 3: the default ServiceAccount has no imagePullSecrets of its own.
        service_account_name=TENANT_SERVICE_ACCOUNT_NAME,
        security_context=k8s.V1PodSecurityContext(run_as_non_root=True, seccomp_profile=k8s.V1SeccompProfile(type="RuntimeDefault")),
    )
    deployment=k8s.V1Deployment(
        metadata=k8s.V1ObjectMeta(name=deployment_name, namespace=k8s_namespace),
        spec=k8s.V1DeploymentSpec(
            replicas=replica_count,
            selector=k8s.V1LabelSelector(match_labels={"app": deployment_name}),
            template=k8s.V1PodTemplateSpec(metadata=k8s.V1ObjectMeta(labels={"app": deployment_name}), spec=pod_spec),
        ),
    )

    try:
        apps_v1.create_namespaced_deployment(namespace=k8s_namespace, body=deployment)
        log.info("tenant_deployment_created", namespace=k8s_namespace, deployment_name=deployment_name, image=image)
    except ApiException as e:
        if e.status!=409:
            raise
        #redeploy: patch just the image and replica count, labels/selector are immutable
        #on a Deployment anyway so leave them alone.
        apps_v1.patch_namespaced_deployment(
            name=deployment_name, namespace=k8s_namespace,
            body={"spec": {"replicas": replica_count, "template": {"spec": {"containers": [{"name": "app", "image": image}]}}}},
        )
        log.info("tenant_deployment_updated", namespace=k8s_namespace, deployment_name=deployment_name, image=image)

    try:
        service=k8s.V1Service(
            metadata=k8s.V1ObjectMeta(name=deployment_name, namespace=k8s_namespace),
            spec=k8s.V1ServiceSpec(selector={"app": deployment_name}, ports=[k8s.V1ServicePort(port=TENANT_APP_PORT, target_port=TENANT_APP_PORT)]),
        )
        core_v1.create_namespaced_service(namespace=k8s_namespace, body=service)
        log.info("tenant_service_created", namespace=k8s_namespace, service_name=deployment_name)
    except ApiException as e:
        if e.status!=409: #Service selector never changes here (same stable label), nothing to update on redeploy
            raise


def get_deployment_status(k8s_namespace: str, deployment_name: str) -> dict:
    """Returns `running` (True/False/None-if-still-rolling-out) based on the Deployment's
    readyReplicas vs desired replicas, the same signal `kubectl rollout status` uses."""
    apps_v1=get_apps_v1_api()

    try:
        deployment=apps_v1.read_namespaced_deployment(name=deployment_name, namespace=k8s_namespace)
    except ApiException as e:
        if e.status==404:
            return {"running": None, "detail": "Deployment not found yet"}
        raise

    desired=deployment.spec.replicas or 0
    ready=deployment.status.ready_replicas or 0

    if ready>=desired and desired>0:
        return {"running": True}

    #a Deployment with no explicit failure condition just means "still rolling out",
    #distinguish that from an actual failure via the Progressing condition's reason, the
    #same signal `kubectl rollout status` relies on internally.
    conditions=deployment.status.conditions or []
    progressing=next((c for c in conditions if c.type=="Progressing"), None)
    if progressing and progressing.status=="False":
        return {"running": False, "error_message": progressing.message or "deployment failed to progress"}

    return {"running": None, "detail": f"{ready}/{desired} replicas ready"}


TENANT_VIEWER_SERVICE_ACCOUNT_NAME="tenant-viewer-sa"
TENANT_VIEWER_ROLE_NAME="tenant-viewer-role"


def ensure_tenant_rbac(k8s_namespace: str):
    """Idempotent, same pattern as ensure_tenant_namespace. Creates the ServiceAccount a
    tenant's downloadable kubeconfig (mint_tenant_kubeconfig, below) is scoped to, and a
    Role/RoleBinding restricted to `get`/`list` on pods and pod logs, deliberately not
    `exec`/`attach` (that would let a tenant shell into containers) and nothing outside
    this one namespace."""
    core_v1=get_core_v1_api()
    rbac_v1=get_rbac_v1_api()

    try:
        core_v1.create_namespaced_service_account(
            namespace=k8s_namespace,
            body=k8s.V1ServiceAccount(metadata=k8s.V1ObjectMeta(name=TENANT_VIEWER_SERVICE_ACCOUNT_NAME, namespace=k8s_namespace)),
        )
        log.info("tenant_viewer_service_account_created", namespace=k8s_namespace)
    except ApiException as e:
        if e.status!=409:
            raise

    try:
        role=k8s.V1Role(
            metadata=k8s.V1ObjectMeta(name=TENANT_VIEWER_ROLE_NAME, namespace=k8s_namespace),
            rules=[k8s.V1PolicyRule(api_groups=[""], resources=["pods", "pods/log"], verbs=["get", "list"])],
        )
        rbac_v1.create_namespaced_role(namespace=k8s_namespace, body=role)
        log.info("tenant_viewer_role_created", namespace=k8s_namespace)
    except ApiException as e:
        if e.status!=409:
            raise

    try:
        binding=k8s.V1RoleBinding(
            metadata=k8s.V1ObjectMeta(name="tenant-viewer-binding", namespace=k8s_namespace),
            subjects=[k8s.RbacV1Subject(kind="ServiceAccount", name=TENANT_VIEWER_SERVICE_ACCOUNT_NAME, namespace=k8s_namespace)],
            role_ref=k8s.V1RoleRef(api_group="rbac.authorization.k8s.io", kind="Role", name=TENANT_VIEWER_ROLE_NAME),
        )
        rbac_v1.create_namespaced_role_binding(namespace=k8s_namespace, body=binding)
        log.info("tenant_viewer_role_binding_created", namespace=k8s_namespace)
    except ApiException as e:
        if e.status!=409:
            raise


def mint_tenant_kubeconfig(k8s_namespace: str, expiration_seconds: int=3600) -> str:
    """Mints a short-lived token for the tenant's scoped ServiceAccount via the
    TokenRequest API (not a long-lived ServiceAccount token Secret, those are being phased
    out cluster-wide as of K8s 1.24+, and a short expiry limits how long a leaked kubeconfig
    stays useful) and wraps it in a ready-to-use kubeconfig YAML scoped to this one
    namespace. Whoever holds this file can `kubectl get pods` in their own namespace and
    nothing else: `get pods -n <other-namespace>` and `get nodes` both return Forbidden."""
    core_v1=get_core_v1_api()

    token_request=k8s.AuthenticationV1TokenRequest(spec=k8s.V1TokenRequestSpec(expiration_seconds=expiration_seconds))
    result=core_v1.create_namespaced_service_account_token(
        name=TENANT_VIEWER_SERVICE_ACCOUNT_NAME, namespace=k8s_namespace, body=token_request
    )
    token=result.status.token

    cfg=k8s.Configuration.get_default_copy()
    server=cfg.host
    with open(cfg.ssl_ca_cert, "rb") as f:
        ca_data=base64.b64encode(f.read()).decode()

    context_name=f"{k8s_namespace}-context"
    cluster_name="tenant-cluster"
    user_name=f"{k8s_namespace}-viewer"

    return f"""apiVersion: v1
kind: Config
clusters:
- name: {cluster_name}
  cluster:
    server: {server}
    certificate-authority-data: {ca_data}
contexts:
- name: {context_name}
  context:
    cluster: {cluster_name}
    namespace: {k8s_namespace}
    user: {user_name}
current-context: {context_name}
users:
- name: {user_name}
  user:
    token: {token}
"""
