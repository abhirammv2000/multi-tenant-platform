import os
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL=os.getenv("DATABASE_URL") #asyncpg driver, used by the running app
DATABASE_URL_SYNC=os.getenv("DATABASE_URL_SYNC") #psycopg2 driver, used only by Alembic (alembic/env.py)
ENV=os.getenv("ENV", "dev")
API_KEY_SECRET=os.getenv("API_KEY_SECRET")
ADMIN_SECRET_KEY=os.getenv("ADMIN_SECRET_KEY")

#the K8s cluster this control plane manages tenant workloads on. In-cluster config is used
#when the control plane itself runs as a pod (has its own ServiceAccount token mounted);
#KUBECONFIG_PATH is for local dev against a kubeconfig context (e.g. minikube or a real
#cluster via `aws eks update-kubeconfig`).
KUBECONFIG_PATH=os.getenv("KUBECONFIG_PATH")
IN_CLUSTER=os.getenv("IN_CLUSTER", "false").strip().lower() in ("true", "1")

#registry tenant build images get pushed to (e.g. an ECR repo URI prefix). Credentials are
#the platform's own, tenants don't bring their own registry account. Copied into a fresh
#Secret in each tenant's namespace when it's created (control_plane/app/k8s_resources.py).
IMAGE_REGISTRY=os.getenv("IMAGE_REGISTRY")
REGISTRY_SERVER=os.getenv("REGISTRY_SERVER")
REGISTRY_USERNAME=os.getenv("REGISTRY_USERNAME")
REGISTRY_PASSWORD=os.getenv("REGISTRY_PASSWORD")

#the cluster-scoped kpack ClusterBuilder every tenant's Image resources reference, set up
#once via k8s/bootstrap/ manifests, not per-tenant. ClusterBuilder (not the namespaced
#Builder) because it's referenceable from any tenant namespace without copying it into each one.
PLATFORM_CLUSTER_BUILDER_NAME=os.getenv("PLATFORM_CLUSTER_BUILDER_NAME", "platform-builder")

#webhook signing, see shared/webhook_dispatcher.py. Each tenant also gets its own
#per-registration secret (control_plane/app/models/webhook_registrations.py); this one is
#used only as the HMAC key for internal/system-level callbacks, not tenant callbacks.
WEBHOOK_REPLAY_WINDOW_SECONDS=int(os.getenv("WEBHOOK_REPLAY_WINDOW_SECONDS", "300"))
