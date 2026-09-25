from kubernetes import client, config
from shared.config import IN_CLUSTER, KUBECONFIG_PATH

_configured=False

def _ensure_configured():
    #kpack's CRDs (Image, Build, Builder, ClusterStore, ClusterStack) have no typed client,
    #they're accessed via CustomObjectsApi with their group/version/plural, same as `kubectl
    #apply -f` would send. CoreV1Api covers plain Namespace creation.
    global _configured
    if _configured:
        return
    if IN_CLUSTER:
        config.load_incluster_config() #the control plane's own ServiceAccount token, when it runs as a pod
    elif KUBECONFIG_PATH:
        config.load_kube_config(config_file=KUBECONFIG_PATH)
    else:
        config.load_kube_config() #default ~/.kube/config context, for local dev against minikube

def get_core_v1_api() -> client.CoreV1Api:
    _ensure_configured()
    return client.CoreV1Api()

def get_custom_objects_api() -> client.CustomObjectsApi:
    _ensure_configured()
    return client.CustomObjectsApi()

def get_apps_v1_api() -> client.AppsV1Api:
    _ensure_configured()
    return client.AppsV1Api()

def get_networking_v1_api() -> client.NetworkingV1Api:
    _ensure_configured()
    return client.NetworkingV1Api()

def get_rbac_v1_api() -> client.RbacAuthorizationV1Api:
    _ensure_configured()
    return client.RbacAuthorizationV1Api()

KPACK_GROUP="kpack.io"
KPACK_VERSION="v1alpha2"
