"""HMAC-signed webhook delivery, the same shape as GitHub's X-Hub-Signature-256 or
Stripe's Stripe-Signature. Builds on self-healing-data-platform's webhook_dispatcher.py,
which had delivery/retry/audit but no signature or replay protection.
"""
import hmac
import hashlib
import json
import time
import httpx
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from shared.observability import get_logger
from control_plane.app.models.webhook_registrations import WebhookRegistration
from control_plane.app.models.webhook_deliveries import WebhookDelivery

log=get_logger(__name__)


def sign_payload(signing_secret: str, timestamp: int, raw_body: bytes) -> str:
    #signing over "{timestamp}.{body}" instead of just the body makes the timestamp
    #tamper-evident: replaying an old body with a forged new timestamp changes the signature.
    message=f"{timestamp}.".encode()+raw_body
    return hmac.new(signing_secret.encode(), message, hashlib.sha256).hexdigest()


async def dispatch_tenant_webhooks(tenant_id: int, event: str, payload: dict, session: AsyncSession):
    """Sends `payload` to every active WebhookRegistration for this tenant, signed and
    timestamped, and records the outcome of each attempt regardless of success or failure."""
    result=await session.execute(
        select(WebhookRegistration).where(WebhookRegistration.tenant_id==tenant_id, WebhookRegistration.is_active==True)
    )
    registrations=result.scalars().all()

    for registration in registrations:
        raw_body=json.dumps(payload, sort_keys=True).encode()
        timestamp=int(time.time())
        signature=sign_payload(registration.signing_secret, timestamp, raw_body)

        headers={
            "Content-Type": "application/json",
            "X-Platform-Signature": f"sha256={signature}",
            "X-Platform-Timestamp": str(timestamp),
        }

        delivery=WebhookDelivery(
            webhook_registration_id=registration.id, tenant_id=tenant_id, event=event,
            payload=payload, signature=signature, timestamp=timestamp, status="failed", #optimistic default overwritten below on success
        )

        try:
            async with httpx.AsyncClient(timeout=15) as client:
                response=await client.post(registration.callback_url, content=raw_body, headers=headers)
            delivery.http_status_code=response.status_code
            if 200<=response.status_code<300:
                delivery.status="success"
            else:
                delivery.error_message=f"Webhook returned non-2xx status code: {response.status_code}"
        except Exception as e:
            delivery.error_message=str(e)
            log.error("webhook_delivery_failed", tenant_id=tenant_id, event=event, error=str(e))

        session.add(delivery)

    if registrations:
        await session.commit()
