"""Common provider contract for redirect checkout integrations."""

from dataclasses import dataclass
from decimal import Decimal
import re
from typing import Protocol

from apps.payments.models import PaymentMethod


class ProviderError(Exception):
    """A safe, non-sensitive provider integration error."""

    def __init__(self, code):
        self.code = safe_diagnostic_code(code)
        super().__init__(self.code)


def safe_diagnostic_code(value, default="provider_error"):
    """Keep diagnostics to short, non-sensitive machine codes."""
    value = str(value or "")
    if len(value) > 64 or not re.fullmatch(r"[A-Za-z0-9_.-]+", value):
        return default
    return value


@dataclass(frozen=True)
class CheckoutSession:
    """A provider-generated hosted checkout session."""

    redirect_url: str
    provider_reference: str
    expires_at: object | None = None


@dataclass(frozen=True)
class VerifiedPayment:
    """Authoritative result returned only after provider-side verification."""

    is_verified: bool
    status: str
    provider_reference: str
    amount: Decimal
    currency: str
    event_reference: str = ""
    diagnostic_code: str = ""


class PaymentProvider(Protocol):
    """Provider contract; adapters must verify with the provider, not browser data."""

    name: str
    checkout_hosts: tuple[str, ...]
    supported_methods: tuple[PaymentMethod, ...]

    def initiate(
        self,
        *,
        transaction,
        callback_url: str,
        return_url: str,
        cancel_url: str,
    ) -> CheckoutSession:
        """Create a hosted checkout using server-owned transaction values."""
        ...

    def extract_transaction_reference(self, payload, headers) -> str:
        """Extract a lookup reference from an untrusted callback without trusting it."""
        ...

    def verify_callback(self, *, transaction, payload, headers) -> VerifiedPayment:
        """Verify signature and query the provider's authoritative transaction API."""
        ...
