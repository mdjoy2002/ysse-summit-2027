"""Fail-closed provider used until an approved merchant adapter is configured."""

from .base import ProviderError


class UnconfiguredProvider:
    """Explicitly disable payments instead of pretending a checkout is available."""

    name = "unconfigured"
    checkout_hosts = ()
    supported_methods = ()

    def initiate(self, **kwargs):
        raise ProviderError("provider_not_configured")

    def extract_transaction_reference(self, payload, headers):
        raise ProviderError("provider_not_configured")

    def verify_callback(self, *, transaction, payload, headers):
        raise ProviderError("provider_not_configured")
