from fastapi import FastAPI, Depends
from control_plane.app.routes.tenants import router as tenant_router
from control_plane.app.routes.api_keys import router as api_keys_router
from control_plane.app.routes.build_jobs import router as build_jobs_router
from control_plane.app.routes.deployments import router as deployments_router
from control_plane.app.routes.webhook_registrations import router as webhook_registrations_router
from control_plane.app.routes.kubeconfig import router as kubeconfig_router
from control_plane.app.dependencies import verify_tenant
from shared.observability import setup_logging

setup_logging()

app=FastAPI()

@app.get("/")
def get_root():
    return {"message":"multi-tenant-platform control plane is running!","docs":"/docs"}

@app.get("/health")
def get_health():
    return {"status": "healthy"}

app.include_router(tenant_router,prefix="/tenants",tags=["Tenants"])
app.include_router(api_keys_router,prefix="/tenants/{tenant_id}/keys",tags=["API_keys"], dependencies=[Depends(verify_tenant)])
app.include_router(build_jobs_router,prefix="/tenants/{tenant_id}/builds",tags=["Build_jobs"], dependencies=[Depends(verify_tenant)])
app.include_router(deployments_router,prefix="/tenants/{tenant_id}/deployments",tags=["Deployments"], dependencies=[Depends(verify_tenant)])
app.include_router(webhook_registrations_router,prefix="/tenants/{tenant_id}/webhooks",tags=["Webhook_registrations"], dependencies=[Depends(verify_tenant)])
app.include_router(kubeconfig_router,prefix="/tenants/{tenant_id}/kubeconfig",tags=["Kubeconfig"], dependencies=[Depends(verify_tenant)])
