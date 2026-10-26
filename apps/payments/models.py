"""Provider transactions and redacted event diagnostics."""

import uuid

from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import F, Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.registrations.models import Registration


class PaymentMethod(models.TextChoices):
    """Checkout channels that may be enabled by an approved provider."""

    BKASH = "bkash", _("bKash")
    NAGAD = "nagad", _("Nagad")
    ROCKET = "rocket", _("Rocket")
    CARD = "card", _("Debit or credit card")


class PaymentTransaction(models.Model):
    """An immutable-price payment attempt with safe provider references."""

    class Status(models.TextChoices):
        PENDING = "pending", _("Pending")
        SUCCEEDED = "succeeded", _("Succeeded")
        FAILED = "failed", _("Failed")
        CANCELLED = "cancelled", _("Cancelled")
        EXPIRED = "expired", _("Expired")
        VERIFICATION_FAILED = "verification_failed", _("Verification failed")
        REFUNDED = "refunded", _("Refunded")

    class Currency(models.TextChoices):
        BDT = "BDT", _("Bangladeshi Taka")

    reference = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    registration = models.ForeignKey(
        Registration,
        related_name="payment_transactions",
        on_delete=models.PROTECT,
    )
    provider = models.CharField(max_length=40, db_index=True)
    provider_transaction_reference = models.CharField(
        max_length=160, blank=True, db_index=True
    )
    method = models.CharField(max_length=12, choices=PaymentMethod.choices)
    status = models.CharField(
        max_length=24, choices=Status.choices, default=Status.PENDING, db_index=True
    )
    amount = models.DecimalField(
        max_digits=9,
        decimal_places=2,
        validators=[MinValueValidator(0)],
    )
    currency = models.CharField(
        max_length=3, choices=Currency.choices, default=Currency.BDT
    )
    checkout_url = models.URLField(max_length=1000, blank=True)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)
    expires_at = models.DateTimeField(null=True, blank=True, db_index=True)
    verified_at = models.DateTimeField(null=True, blank=True)
    diagnostic_code = models.CharField(max_length=64, blank=True)

    class Meta:
        ordering = ("-created_at", "-pk")
        indexes = [
            models.Index(
                fields=("registration", "status", "created_at"),
                name="payment_reg_state_idx",
            ),
            models.Index(
                fields=("provider", "provider_transaction_reference"),
                name="payment_provider_ref_idx",
            ),
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(amount__gt=0),
                name="payment_amount_positive",
            ),
            models.UniqueConstraint(
                fields=("registration",),
                condition=Q(status="pending"),
                name="one_pending_payment_per_reg",
            ),
            models.UniqueConstraint(
                fields=("provider", "provider_transaction_reference"),
                condition=~Q(provider_transaction_reference=""),
                name="payment_provider_ref_unique",
            ),
        ]
        permissions = [
            (
                "view_payment_diagnostics",
                "Can view safe payment diagnostics",
            ),
        ]

    def __str__(self):
        return str(self.reference)


class PaymentEvent(models.Model):
    """A minimal non-PII audit trail for provider and verification events."""

    transaction = models.ForeignKey(
        PaymentTransaction,
        related_name="events",
        on_delete=models.CASCADE,
    )
    event_type = models.CharField(max_length=40, db_index=True)
    source = models.CharField(max_length=24)
    diagnostic_code = models.CharField(max_length=64, blank=True)
    payload_digest = models.CharField(max_length=64, blank=True)
    provider_event_reference = models.CharField(max_length=160, blank=True)
    occurred_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ("occurred_at", "pk")
        constraints = [
            models.UniqueConstraint(
                fields=("transaction", "provider_event_reference"),
                condition=~Q(provider_event_reference=""),
                name="payment_provider_event_unique",
            ),
            models.UniqueConstraint(
                fields=("transaction", "payload_digest"),
                condition=~Q(payload_digest=""),
                name="payment_callback_digest_unique",
            ),
        ]
        indexes = [
            models.Index(
                fields=("transaction", "event_type", "occurred_at"),
                name="payment_event_timeline_idx",
            ),
        ]

    def __str__(self):
        return f"{self.event_type} — {self.transaction.reference}"
