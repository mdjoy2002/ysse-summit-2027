"""Pass configuration, participant registrations, and verifiable tickets."""

import secrets
import string
import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import F, Q
from django.utils import timezone
from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _

from apps.home.models import SummitEvent


def generate_registration_reference(full_name=""):
    """Return a short name-prefixed reference with a random suffix."""
    name_prefix = slugify(full_name).replace("-", "").upper()[:4] or "REG"
    suffix_characters = string.ascii_uppercase + string.digits
    suffix = "".join(secrets.choice(suffix_characters) for _ in range(6))
    return f"{name_prefix}-{suffix}"


class PassType(models.Model):
    """An organizer-configured pass; prices remain draft until verified."""

    class Category(models.TextChoices):
        MEMBER = "member", _("YSSE Member Pass")
        GENERAL = "general", _("General Delegate Pass")
        VIP = "vip", _("VIP Pass")

    name = models.CharField(max_length=100)
    category = models.CharField(max_length=12, choices=Category.choices, unique=True)
    description = models.CharField(max_length=300, blank=True)
    price = models.DecimalField(
        max_digits=9, decimal_places=2, null=True, blank=True
    )
    price_confirmed = models.BooleanField(
        default=False,
        help_text=_("Enable only after the organizer confirms the final price."),
    )
    included_benefits = models.TextField(
        blank=True,
        help_text=_("Enter one benefit per line. Benefits are shown as configured."),
    )
    payment_instructions = models.TextField(
        blank=True,
        help_text=_("Optional organizer-approved payment instructions; no gateway is integrated."),
    )
    is_published = models.BooleanField(default=False, db_index=True)
    is_available = models.BooleanField(default=False, db_index=True)
    capacity = models.PositiveIntegerField(
        null=True,
        blank=True,
        validators=[MinValueValidator(1)],
        help_text=_("Leave blank for no configured capacity."),
    )
    available_from = models.DateTimeField(null=True, blank=True)
    available_until = models.DateTimeField(null=True, blank=True)
    requires_manual_approval = models.BooleanField(default=False)
    display_order = models.PositiveSmallIntegerField(default=0, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("display_order", "pk")
        indexes = [
            models.Index(
                fields=("is_published", "is_available", "display_order"),
                name="reg_pass_public_idx",
            ),
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(price__isnull=True) | Q(price__gte=0),
                name="reg_pass_price_nonnegative",
            ),
            models.CheckConstraint(
                condition=(
                    Q(available_from__isnull=True)
                    | Q(available_until__isnull=True)
                    | Q(available_until__gt=F("available_from"))
                ),
                name="reg_pass_window_order",
            ),
        ]
        verbose_name = _("Pass type")
        verbose_name_plural = _("Pass types")

    def clean(self):
        """Prevent draft or unpriced passes from being publicly available."""
        super().clean()
        errors = {}
        if (self.is_published or self.is_available) and (
            self.price is None or not self.price_confirmed
        ):
            errors["price_confirmed"] = _(
                "Confirm and configure the final pass price before publishing or opening sales."
            )
        if (
            self.available_from
            and self.available_until
            and self.available_until <= self.available_from
        ):
            errors["available_until"] = _(
                "The pass sale end must be after its start."
            )
        if errors:
            raise ValidationError(errors)

    @property
    def benefits(self):
        """Return non-empty configured benefits in their authored order."""
        return [line.strip() for line in self.included_benefits.splitlines() if line.strip()]

    def sales_are_open(self, at=None):
        """Check publication, availability, price confirmation, and sale window."""
        at = at or timezone.now()
        return (
            self.is_published
            and self.is_available
            and self.price is not None
            and self.price_confirmed
            and (self.available_from is None or self.available_from <= at)
            and (self.available_until is None or at < self.available_until)
        )

    @property
    def has_capacity(self):
        """Return whether a configured capacity still has an open place."""
        if self.capacity is None:
            return True
        return (
            self.registrations.exclude(status=Registration.Status.CANCELLED).count()
            < self.capacity
        )

    def __str__(self):
        return self.name


class Registration(models.Model):
    """A single participant's pass reservation and ticket audit record."""

    class Status(models.TextChoices):
        PENDING = "pending", _("Pending")
        CONFIRMED = "confirmed", _("Confirmed")
        CANCELLED = "cancelled", _("Cancelled")

    class PaymentStatus(models.TextChoices):
        NOT_REQUIRED = "not_required", _("Not required")
        PENDING = "pending", _("Pending")
        PAID = "paid", _("Paid")
        FAILED = "failed", _("Failed")
        REFUNDED = "refunded", _("Refunded")
        CANCELLED = "cancelled", _("Cancelled")

    event = models.ForeignKey(
        SummitEvent,
        related_name="registrations",
        on_delete=models.PROTECT,
    )
    participant = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="summit_registrations",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
    )
    pass_type = models.ForeignKey(
        PassType,
        related_name="registrations",
        on_delete=models.PROTECT,
    )
    reference = models.CharField(
        max_length=36,
        unique=True,
        editable=False,
        default=generate_registration_reference,
    )
    ticket_reference = models.UUIDField(
        null=True, blank=True, unique=True, editable=False
    )
    full_name = models.CharField(max_length=120)
    contact_email = models.EmailField(max_length=254)
    phone_number = models.CharField(max_length=32, blank=True)
    pass_name_snapshot = models.CharField(max_length=100)
    benefits_snapshot = models.TextField(blank=True)
    amount_due = models.DecimalField(max_digits=9, decimal_places=2)
    status = models.CharField(
        max_length=12, choices=Status.choices, default=Status.PENDING, db_index=True
    )
    payment_status = models.CharField(
        max_length=16,
        choices=PaymentStatus.choices,
        default=PaymentStatus.PENDING,
        db_index=True,
    )
    submitted_at = models.DateTimeField(default=timezone.now, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)
    payment_reviewed_at = models.DateTimeField(null=True, blank=True)
    payment_reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="reviewed_summit_payments",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    payment_audit_note = models.TextField(
        blank=True,
        help_text=_("Record a safe organizer reference; never enter credentials or card data."),
    )
    status_reviewed_at = models.DateTimeField(null=True, blank=True)
    status_reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="reviewed_summit_registrations",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    status_audit_note = models.TextField(
        blank=True,
        help_text=_("Explain staff confirmation or cancellation; do not include sensitive payment data."),
    )

    class Meta:
        ordering = ("-submitted_at", "-pk")
        indexes = [
            models.Index(
                fields=("event", "status", "submitted_at"),
                name="reg_event_status_time_idx",
            ),
            models.Index(
                fields=("pass_type", "status"),
                name="reg_pass_status_idx",
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=("event", "participant"),
                name="reg_one_per_event_user",
            ),
            models.UniqueConstraint(
                fields=("event", "contact_email"),
                condition=Q(participant__isnull=True),
                name="reg_one_guest_email_per_event",
            ),
            models.CheckConstraint(
                condition=Q(amount_due__gte=0),
                name="reg_amount_nonnegative",
            ),
        ]
        permissions = [
            ("verify_registration_payments", "Can verify registration payments"),
            (
                "reconcile_registration_payments",
                "Can reconcile manually verified registration payments",
            ),
            ("manage_registration_status", "Can confirm or cancel registrations"),
            ("verify_tickets_at_entry", "Can verify tickets at event entry"),
        ]
        verbose_name = _("Summit registration")
        verbose_name_plural = _("Summit registrations")

    @property
    def ticket_is_valid(self):
        """A ticket is valid only for confirmed, paid or free registrations."""
        return (
            self.ticket_reference is not None
            and self.status == self.Status.CONFIRMED
            and self.payment_status
            in (self.PaymentStatus.PAID, self.PaymentStatus.NOT_REQUIRED)
        )

    def __str__(self):
        return f"{self.reference} — {self.pass_name_snapshot}"

    @property
    def included_benefits(self):
        """Return the benefits recorded at the time of registration."""
        return [
            line.strip()
            for line in self.benefits_snapshot.splitlines()
            if line.strip()
        ]
