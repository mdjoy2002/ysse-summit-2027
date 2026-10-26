"""Provider-neutral checkout creation and authoritative callback processing."""

import hashlib
import json
import logging
import uuid
from datetime import timedelta
from decimal import Decimal
from urllib.parse import urlsplit

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.module_loading import import_string

from apps.registrations.models import Registration
from apps.registrations.services import send_registration_notification

from .models import PaymentEvent, PaymentMethod, PaymentTransaction
from .providers.base import ProviderError, safe_diagnostic_code


logger = logging.getLogger("ysse.payments")
PAYMENT_TTL_MINUTES = 20
ALLOWED_VERIFIED_STATUSES = {
    PaymentTransaction.Status.SUCCEEDED,
    PaymentTransaction.Status.FAILED,
    PaymentTransaction.Status.CANCELLED,
}


class PaymentIntegrationError(Exception):
    """A safe, user-displayable payment setup or provider error."""

    def __init__(self, code):
        self.code = safe_diagnostic_code(code)
        super().__init__(self.code)


class PaymentProcessingError(Exception):
    """A server-side payment operation failed and may be retried safely."""


def get_provider():
    """Load the explicitly configured provider adapter; default is fail-closed."""
    provider_path = getattr(
        settings,
        "PAYMENT_PROVIDER_CLASS",
        "apps.payments.providers.unconfigured.UnconfiguredProvider",
    )
    try:
        provider_class = import_string(provider_path)
        provider = provider_class()
    except (ImportError, AttributeError, TypeError) as error:
        raise ImproperlyConfigured(
            "PAYMENT_PROVIDER_CLASS must point to a valid payment provider."
        ) from error

    for method_name in (
        "initiate",
        "extract_transaction_reference",
        "verify_callback",
    ):
        if not callable(getattr(provider, method_name, None)):
            raise ImproperlyConfigured(
                f"Configured payment provider is missing {method_name}()."
            )
    methods = getattr(provider, "supported_methods", None)
    hosts = getattr(provider, "checkout_hosts", None)
    name = getattr(provider, "name", None)
    if (
        not isinstance(name, str)
        or not name
        or len(name) > 40
        or not isinstance(methods, (tuple, list, set, frozenset))
        or any(method not in PaymentMethod.values for method in methods)
        or not isinstance(hosts, (tuple, list, set, frozenset))
        or any(not isinstance(host, str) or not host for host in hosts)
        or (methods and not hosts and not getattr(provider, "is_demo", False))
    ):
        raise ImproperlyConfigured(
            "Configured payment provider has invalid name, methods, or checkout hosts."
        )
    if getattr(provider, "is_demo", False) and not settings.PAYMENT_DEMO_ENABLED:
        raise ImproperlyConfigured(
            "The demo payment provider is disabled in this environment."
        )
    return provider


def create_checkout(*, registration, method):
    """Create a recorded attempt, then obtain a provider-hosted checkout URL."""
    provider = get_provider()
    if method not in PaymentMethod.values or method not in provider.supported_methods:
        raise PaymentIntegrationError("payment_method_unavailable")
    is_demo = getattr(provider, "is_demo", False)
    public_base_url = "" if is_demo else _validated_public_base_url()
    if registration.status != Registration.Status.PENDING:
        raise PaymentIntegrationError("registration_not_payable")
    if registration.payment_status not in (
        Registration.PaymentStatus.PENDING,
        Registration.PaymentStatus.FAILED,
        Registration.PaymentStatus.CANCELLED,
    ):
        raise PaymentIntegrationError("registration_not_payable")
    if registration.amount_due <= Decimal("0.00"):
        raise PaymentIntegrationError("payment_not_required")

    with transaction.atomic():
        locked_registration = (
            Registration.objects.select_for_update()
            .select_related("event")
            .get(pk=registration.pk)
        )
        active_attempts = PaymentTransaction.objects.select_for_update().filter(
            registration=locked_registration,
            status=PaymentTransaction.Status.PENDING,
        )
        for active_attempt in active_attempts:
            if active_attempt.expires_at and active_attempt.expires_at > timezone.now():
                raise PaymentIntegrationError("payment_attempt_already_pending")
            active_attempt.status = PaymentTransaction.Status.EXPIRED
            active_attempt.diagnostic_code = "checkout_expired"
            active_attempt.save(
                update_fields=("status", "diagnostic_code", "updated_at")
            )
            PaymentEvent.objects.create(
                transaction=active_attempt,
                event_type="checkout_expired",
                source="system",
                diagnostic_code="checkout_expired",
            )
        if (
            locked_registration.status != Registration.Status.PENDING
            or locked_registration.payment_status
            not in (
                Registration.PaymentStatus.PENDING,
                Registration.PaymentStatus.FAILED,
                Registration.PaymentStatus.CANCELLED,
            )
            or locked_registration.amount_due <= Decimal("0.00")
        ):
            raise PaymentIntegrationError("registration_not_payable")
        payment = PaymentTransaction.objects.create(
            registration=locked_registration,
            provider=provider.name,
            method=method,
            amount=locked_registration.amount_due,
            currency=PaymentTransaction.Currency.BDT,
            expires_at=timezone.now()
            + timedelta(minutes=PAYMENT_TTL_MINUTES),
        )
        PaymentEvent.objects.create(
            transaction=payment,
            event_type="checkout_requested",
            source="participant",
        )

    callback_url = (
        "" if is_demo else f"{public_base_url}/payments/callback/"
    )
    return_url = (
        "" if is_demo else f"{public_base_url}/payments/return/{payment.reference}/"
    )
    cancel_url = return_url
    try:
        checkout = provider.initiate(
            transaction=payment,
            callback_url=callback_url,
            return_url=return_url,
            cancel_url=cancel_url,
        )
        parts = urlsplit(checkout.redirect_url)
        allowed_hosts = {
            host.casefold().rstrip(".")
            for host in getattr(provider, "checkout_hosts", ())
        }
        invalid_demo_checkout = is_demo and bool(checkout.redirect_url)
        invalid_provider_checkout = not is_demo and (
            parts.scheme != "https"
            or not parts.hostname
            or len(checkout.redirect_url) > 1000
            or parts.username
            or parts.password
            or parts.hostname.casefold().rstrip(".") not in allowed_hosts
        )
        if invalid_demo_checkout or invalid_provider_checkout or not checkout.provider_reference:
            raise ProviderError("invalid_checkout_response")
    except ProviderError as error:
        payment.status = PaymentTransaction.Status.FAILED
        payment.diagnostic_code = error.code[:64]
        payment.save(update_fields=("status", "diagnostic_code", "updated_at"))
        PaymentEvent.objects.create(
            transaction=payment,
            event_type="checkout_failed",
            source="provider",
            diagnostic_code=error.code[:64],
        )
        raise PaymentIntegrationError(error.code) from error
    except Exception as error:
        logger.error(
            "Payment checkout initiation failed transaction=%s exception_type=%s",
            payment.reference,
            type(error).__name__,
        )
        payment.status = PaymentTransaction.Status.FAILED
        payment.diagnostic_code = "provider_request_failed"
        payment.save(update_fields=("status", "diagnostic_code", "updated_at"))
        PaymentEvent.objects.create(
            transaction=payment,
            event_type="checkout_failed",
            source="provider",
            diagnostic_code="provider_request_failed",
        )
        raise PaymentIntegrationError("provider_request_failed") from error

    payment.provider_transaction_reference = checkout.provider_reference[:160]
    payment.checkout_url = checkout.redirect_url
    if checkout.expires_at:
        payment.expires_at = min(payment.expires_at, checkout.expires_at)
    payment.save(
        update_fields=(
            "provider_transaction_reference",
            "checkout_url",
            "expires_at",
            "updated_at",
        )
    )
    PaymentEvent.objects.create(
        transaction=payment,
        event_type="checkout_created",
        source="provider",
    )
    return payment


def get_supported_methods():
    """Return only methods explicitly advertised by the configured provider."""
    provider = get_provider()
    methods = tuple(getattr(provider, "supported_methods", ()))
    if not methods:
        return []
    if not getattr(provider, "is_demo", False):
        try:
            _validated_public_base_url()
        except PaymentIntegrationError as error:
            logger.info("Payment methods unavailable code=%s", error.code)
            return []
    return [
        (method.value, method.label)
        for method in PaymentMethod
        if method.value in methods
    ]


def is_demo_provider():
    """Report whether the configured provider is the enabled local demo adapter."""
    return bool(getattr(get_provider(), "is_demo", False))


def _validated_public_base_url():
    """Use a fixed canonical HTTPS origin instead of an untrusted Host header."""
    value = getattr(settings, "PUBLIC_BASE_URL", "").rstrip("/")
    parts = urlsplit(value)
    if (
        parts.scheme != "https"
        or not parts.hostname
        or parts.username
        or parts.password
        or parts.path not in ("", "/")
        or parts.query
        or parts.fragment
    ):
        raise PaymentIntegrationError("public_base_url_not_configured")
    return value


def _callback_payload(request):
    """Decode provider callback data without persisting raw fields."""
    content_length = request.META.get("CONTENT_LENGTH")
    if content_length:
        try:
            if int(content_length) > 64 * 1024:
                raise PaymentIntegrationError("callback_payload_too_large")
        except ValueError as error:
            raise PaymentIntegrationError("invalid_callback_payload") from error
    if len(request.body) > 64 * 1024:
        raise PaymentIntegrationError("callback_payload_too_large")
    content_type = request.headers.get("Content-Type", "").split(";", 1)[0].strip()
    if content_type == "application/json":
        try:
            value = json.loads(request.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise PaymentIntegrationError("invalid_callback_payload") from error
        if not isinstance(value, dict):
            raise PaymentIntegrationError("invalid_callback_payload")
        return value
    if content_type == "application/x-www-form-urlencoded" or not content_type:
        return request.POST.dict()
    raise PaymentIntegrationError("unsupported_callback_content_type")


def process_provider_callback(*, payload, headers, raw_body):
    """Verify a provider callback server-side and apply its result once."""
    provider = get_provider()
    payload_digest = hashlib.sha256(raw_body).hexdigest()
    try:
        reference = provider.extract_transaction_reference(payload, headers)
    except ProviderError as error:
        raise PaymentIntegrationError(error.code) from error
    try:
        payment = PaymentTransaction.objects.get(reference=reference)
    except (PaymentTransaction.DoesNotExist, ValueError, ValidationError) as error:
        raise PaymentIntegrationError("transaction_not_found") from error
    if payment.provider != provider.name:
        raise PaymentIntegrationError("provider_mismatch")

    try:
        result = provider.verify_callback(
            transaction=payment,
            payload=payload,
            headers=headers,
        )
    except ProviderError as error:
        _record_callback_rejection(
            transaction=payment,
            diagnostic_code=error.code,
            payload_digest=payload_digest,
        )
        raise PaymentIntegrationError(error.code) from error
    except Exception as error:
        logger.error(
            "Payment verification failed transaction=%s exception_type=%s",
            payment.reference,
            type(error).__name__,
        )
        raise PaymentProcessingError from error

    if not result.is_verified:
        _record_callback_rejection(
            transaction=payment,
            diagnostic_code=safe_diagnostic_code(
                result.diagnostic_code, "provider_unverified"
            ),
            payload_digest=payload_digest,
            provider_event_reference=result.event_reference or "",
        )
        raise PaymentIntegrationError("provider_unverified")
    if (
        not result.provider_reference
        or result.amount != payment.amount
        or result.currency != payment.currency
    ):
        _record_verification_mismatch(
            payment=payment,
            payload_digest=payload_digest,
            result=result,
        )
        raise PaymentIntegrationError("transaction_mismatch")
    if result.status not in ALLOWED_VERIFIED_STATUSES:
        raise PaymentIntegrationError("unsupported_provider_status")

    try:
        return _apply_verified_result(
            payment=payment,
            result=result,
            payload_digest=payload_digest,
        )
    except IntegrityError as error:
        logger.warning(
            "Payment provider reference uniqueness conflict transaction=%s",
            payment.reference,
        )
        _mark_verification_failed(
            payment=payment,
            diagnostic_code="provider_reference_conflict",
            payload_digest=payload_digest,
        )
        raise PaymentIntegrationError("provider_reference_conflict") from error


def _record_verification_mismatch(*, payment, payload_digest, result):
    """Persist a safe mismatch code and never mark registration paid."""
    with transaction.atomic():
        locked = PaymentTransaction.objects.select_for_update().get(pk=payment.pk)
        if locked.status == PaymentTransaction.Status.SUCCEEDED:
            return
        if PaymentEvent.objects.filter(
            transaction=locked,
            payload_digest=payload_digest,
        ).exists():
            return
        if result.event_reference and PaymentEvent.objects.filter(
            transaction=locked,
            provider_event_reference=result.event_reference,
        ).exists():
            return
        locked.status = PaymentTransaction.Status.VERIFICATION_FAILED
        locked.diagnostic_code = "amount_or_currency_mismatch"
        locked.save(update_fields=("status", "diagnostic_code", "updated_at"))
        PaymentEvent.objects.create(
            transaction=locked,
            event_type="verification_mismatch",
            source="provider",
            diagnostic_code="amount_or_currency_mismatch",
            payload_digest=payload_digest,
            provider_event_reference=(result.event_reference or "")[:160],
        )


def _record_callback_rejection(
    *, transaction, diagnostic_code, payload_digest, provider_event_reference=""
):
    """Record a callback rejection once, without retaining the callback payload."""
    if PaymentEvent.objects.filter(
        transaction=transaction,
        provider_event_reference=provider_event_reference,
    ).exclude(provider_event_reference="").exists():
        return
    PaymentEvent.objects.get_or_create(
        transaction=transaction,
        payload_digest=payload_digest,
        defaults={
            "event_type": "verification_rejected",
            "source": "provider",
            "diagnostic_code": safe_diagnostic_code(diagnostic_code),
            "provider_event_reference": provider_event_reference[:160],
        },
    )


def _mark_verification_failed(*, payment, diagnostic_code, payload_digest):
    with transaction.atomic():
        locked = PaymentTransaction.objects.select_for_update().get(pk=payment.pk)
        if PaymentEvent.objects.filter(
            transaction=locked,
            payload_digest=payload_digest,
        ).exists():
            return
        locked.status = PaymentTransaction.Status.VERIFICATION_FAILED
        locked.diagnostic_code = diagnostic_code
        locked.save(update_fields=("status", "diagnostic_code", "updated_at"))
        PaymentEvent.objects.create(
            transaction=locked,
            event_type="verification_failed",
            source="provider",
            diagnostic_code=diagnostic_code,
            payload_digest=payload_digest,
        )


def _apply_verified_result(*, payment, result, payload_digest):
    """Update one attempt and its registration atomically, idempotently."""
    notify = None
    with transaction.atomic():
        locked = (
            PaymentTransaction.objects.select_for_update()
            .select_related("registration__pass_type")
            .get(pk=payment.pk)
        )
        if locked.status == PaymentTransaction.Status.SUCCEEDED:
            return locked
        if locked.status in (
            PaymentTransaction.Status.CANCELLED,
            PaymentTransaction.Status.EXPIRED,
            PaymentTransaction.Status.REFUNDED,
        ):
            return locked
        if PaymentEvent.objects.filter(
            transaction=locked,
            payload_digest=payload_digest,
        ).exists():
            return locked
        if PaymentEvent.objects.filter(
            transaction=locked,
            provider_event_reference=result.event_reference,
        ).exists() and result.event_reference:
            return locked

        if (
            locked.expires_at is not None
            and timezone.now() >= locked.expires_at
            and result.status == PaymentTransaction.Status.SUCCEEDED
        ):
            locked.status = PaymentTransaction.Status.VERIFICATION_FAILED
            locked.diagnostic_code = "late_success_requires_reconciliation"
            event_type = "late_success_requires_reconciliation"
        else:
            locked.status = result.status
            locked.diagnostic_code = safe_diagnostic_code(result.diagnostic_code, "")
            event_type = f"verified_{result.status}"
        locked.provider_transaction_reference = result.provider_reference[:160]
        locked.verified_at = timezone.now()

        locked.save(
            update_fields=(
                "status",
                "provider_transaction_reference",
                "verified_at",
                "diagnostic_code",
                "updated_at",
            )
        )
        PaymentEvent.objects.create(
            transaction=locked,
            event_type=event_type,
            source="provider",
            diagnostic_code=locked.diagnostic_code,
            payload_digest=payload_digest,
            provider_event_reference=(result.event_reference or "")[:160],
        )

        registration = (
            Registration.objects.select_for_update()
            .select_related("pass_type")
            .get(pk=locked.registration_id)
        )
        if (
            locked.status == PaymentTransaction.Status.SUCCEEDED
            and registration.payment_status != Registration.PaymentStatus.PAID
        ):
            registration.payment_status = Registration.PaymentStatus.PAID
            registration.payment_reviewed_at = timezone.now()
            registration.payment_audit_note = (
                f"Provider {locked.provider} verified transaction "
                f"{locked.provider_transaction_reference}."
            )
            if (
                registration.status == Registration.Status.PENDING
                and not registration.pass_type.requires_manual_approval
            ):
                registration.status = Registration.Status.CONFIRMED
                registration.status_reviewed_at = timezone.now()
                registration.status_audit_note = (
                    "Automatically confirmed after provider payment verification."
                )
                registration.ticket_reference = (
                    registration.ticket_reference or uuid.uuid4()
                )
                notify = "confirmation"
            elif registration.status == Registration.Status.CANCELLED:
                locked.diagnostic_code = "paid_after_registration_cancelled"
                PaymentEvent.objects.create(
                    transaction=locked,
                    event_type="paid_after_registration_cancelled",
                    source="system",
                    diagnostic_code=locked.diagnostic_code,
                )
            registration.save(
                update_fields=(
                    "payment_status",
                    "payment_reviewed_at",
                    "payment_audit_note",
                    "status",
                    "status_reviewed_at",
                    "status_audit_note",
                    "ticket_reference",
                    "updated_at",
                )
            )
        elif locked.status == PaymentTransaction.Status.FAILED:
            if registration.payment_status != Registration.PaymentStatus.PAID:
                registration.payment_status = Registration.PaymentStatus.FAILED
                registration.save(update_fields=("payment_status", "updated_at"))
        elif locked.status in (
            PaymentTransaction.Status.CANCELLED,
            PaymentTransaction.Status.EXPIRED,
        ):
            if registration.payment_status not in (
                Registration.PaymentStatus.PAID,
                Registration.PaymentStatus.REFUNDED,
            ):
                registration.payment_status = Registration.PaymentStatus.CANCELLED
                registration.save(update_fields=("payment_status", "updated_at"))

        if notify:
            transaction.on_commit(
                lambda: send_registration_notification(
                    registration.pk,
                    "confirmation",
                ),
                robust=True,
            )
            transaction.on_commit(
                lambda: send_registration_notification(registration.pk, "ticket"),
                robust=True,
            )
    return locked
