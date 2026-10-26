"""Local-only simulated checkout for development and manual demonstrations."""

import hashlib
import hmac
import uuid
from decimal import Decimal

from django.conf import settings

from apps.payments.models import PaymentMethod, PaymentTransaction

from .base import CheckoutSession, ProviderError, VerifiedPayment


class DemoProvider:
    """Simulate payment results without connecting to a gateway or moving money."""

    name = "demo-sandbox"
    is_demo = True
    checkout_hosts = ()
    supported_methods = tuple(PaymentMethod.values)

    def initiate(self, *, transaction, callback_url, return_url, cancel_url):
        """Create an internal demo attempt; no hosted merchant session is created."""
        return CheckoutSession(
            redirect_url="",
            provider_reference=f"demo-{transaction.reference}",
            expires_at=transaction.expires_at,
        )

    def extract_transaction_reference(self, payload, headers):
        """Read the attempt identifier from an internally signed demo result."""
        reference = payload.get("reference", "")
        if not isinstance(reference, str) or not reference:
            raise ProviderError("invalid_demo_reference")
        return reference

    def verify_callback(self, *, transaction, payload, headers):
        """Verify a server-signed simulation result using the local secret key."""
        result_name = payload.get("result")
        event_reference = payload.get("event_reference")
        signature = headers.get("X-Demo-Signature", "")
        if result_name not in ("success", "failed", "cancelled"):
            raise ProviderError("invalid_demo_result")
        if not isinstance(event_reference, str) or not event_reference:
            raise ProviderError("invalid_demo_event")

        expected_signature = _sign_demo_result(
            transaction.reference,
            result_name,
            event_reference,
        )
        if not hmac.compare_digest(signature, expected_signature):
            raise ProviderError("invalid_demo_signature")

        status = {
            "success": PaymentTransaction.Status.SUCCEEDED,
            "failed": PaymentTransaction.Status.FAILED,
            "cancelled": PaymentTransaction.Status.CANCELLED,
        }[result_name]
        return VerifiedPayment(
            is_verified=True,
            status=status,
            provider_reference=transaction.provider_transaction_reference,
            amount=Decimal(transaction.amount),
            currency=transaction.currency,
            event_reference=event_reference,
            diagnostic_code=f"demo_{result_name}",
        )


def signed_demo_result(*, reference, result):
    """Create a signed, non-production result for the local demo form."""
    event_reference = f"demo-event-{uuid.uuid4()}"
    payload = {
        "reference": str(reference),
        "result": result,
        "event_reference": event_reference,
    }
    signature = _sign_demo_result(reference, result, event_reference)
    return payload, {"X-Demo-Signature": signature}


def _sign_demo_result(reference, result, event_reference):
    """Bind the simulation status and one-time event id to the local secret."""
    message = f"{reference}:{result}:{event_reference}".encode("utf-8")
    return hmac.new(
        settings.SECRET_KEY.encode("utf-8"),
        message,
        hashlib.sha256,
    ).hexdigest()
