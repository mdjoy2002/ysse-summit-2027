"""Transactional registration, payment review, and notification workflows."""

import logging
import smtplib
import uuid
from decimal import Decimal

from django.conf import settings
from django.core.mail import send_mail
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.home.models import SummitEvent

from .models import PassType, Registration, generate_registration_reference


logger = logging.getLogger("ysse.registrations")


class RegistrationError(Exception):
    """A registration action cannot be completed under current business rules."""


def guest_session_owns_registration(request, registration):
    """Allow guests only registrations recorded in their current browser session."""
    return (
        not request.user.is_authenticated
        and registration.participant_id is None
        and registration.pk in request.session.get("guest_registration_ids", [])
    )


def _email_after_commit(registration_id, notification):
    """Send notification after the corresponding database transaction commits."""
    transaction.on_commit(
        lambda: send_registration_notification(registration_id, notification),
        robust=True,
    )


def create_registration(*, participant=None, form, event):
    """Reserve one pass for an account or guest; enforce availability and uniqueness."""
    if not event.registration_available:
        raise RegistrationError("Summit registration is currently closed.")

    try:
        with transaction.atomic():
            locked_event = SummitEvent.objects.select_for_update().get(pk=event.pk)
            if (
                not locked_event.registration_available
                or locked_event.status
                in (SummitEvent.Status.CANCELLED, SummitEvent.Status.COMPLETED)
            ):
                raise RegistrationError("Summit registration is currently closed.")
            pass_type = PassType.objects.select_for_update().get(
                pk=form.cleaned_data["pass_type"].pk
            )
            now = timezone.now()
            if not pass_type.sales_are_open(now):
                raise RegistrationError("This pass is no longer available.")
            if pass_type.capacity is not None:
                active_count = Registration.objects.filter(
                    pass_type=pass_type
                ).exclude(status=Registration.Status.CANCELLED).count()
                if active_count >= pass_type.capacity:
                    raise RegistrationError("This pass has sold out.")
            duplicate_registration = Registration.objects.filter(event=event)
            if participant is None:
                duplicate_registration = duplicate_registration.filter(
                    participant__isnull=True,
                    contact_email__iexact=form.cleaned_data["contact_email"],
                )
            else:
                duplicate_registration = duplicate_registration.filter(
                    participant=participant
                )
            if duplicate_registration.exists():
                raise RegistrationError(
                    "A registration for this attendee already exists."
                )

            pass_is_free = pass_type.price == Decimal("0.00")
            status = (
                Registration.Status.PENDING
                if pass_type.requires_manual_approval or not pass_is_free
                else Registration.Status.CONFIRMED
            )
            payment_status = (
                Registration.PaymentStatus.NOT_REQUIRED
                if pass_is_free
                else Registration.PaymentStatus.PENDING
            )
            registration = Registration(
                reference=generate_registration_reference(
                    form.cleaned_data["full_name"]
                ),
                event=locked_event,
                participant=participant,
                pass_type=pass_type,
                full_name=form.cleaned_data["full_name"],
                contact_email=form.cleaned_data["contact_email"],
                phone_number=form.cleaned_data["phone_number"],
                pass_name_snapshot=pass_type.name,
                benefits_snapshot=pass_type.included_benefits,
                amount_due=pass_type.price,
                status=status,
                payment_status=payment_status,
            )
            if status == Registration.Status.CONFIRMED:
                registration.ticket_reference = uuid.uuid4()
            for attempt in range(5):
                try:
                    with transaction.atomic():
                        registration.save(force_insert=True)
                    break
                except IntegrityError:
                    # Retry only a reference collision; other integrity errors
                    # (such as duplicate participant registrations) must surface.
                    if Registration.objects.filter(
                        reference=registration.reference
                    ).exists():
                        if attempt == 4:
                            raise RegistrationError(
                                "Could not generate a unique registration reference."
                            )
                        registration.reference = generate_registration_reference(
                            form.cleaned_data["full_name"]
                        )
                        continue
                    raise
    except IntegrityError as error:
        duplicate_registration = Registration.objects.filter(event=event)
        if participant is None:
            duplicate_registration = duplicate_registration.filter(
                participant__isnull=True,
                contact_email__iexact=form.cleaned_data["contact_email"],
            )
        else:
            duplicate_registration = duplicate_registration.filter(
                participant=participant
            )
        if duplicate_registration.exists():
            raise RegistrationError(
                "A registration for this attendee already exists."
            ) from error
        raise

    _email_after_commit(registration.pk, "registration")
    if registration.ticket_reference:
        _email_after_commit(registration.pk, "ticket")
    return registration


def _require_permission(actor, permission):
    if (
        not actor.is_authenticated
        or not actor.is_staff
        or not actor.has_perm(permission)
    ):
        raise PermissionError("The required registration permission is missing.")


def verify_payment(*, registration_id, new_status, actor, audit_note):
    """Apply an audited payment decision made by an authorized staff member."""
    required_permission = (
        "registrations.reconcile_registration_payments"
        if new_status
        in (
            Registration.PaymentStatus.PAID,
            Registration.PaymentStatus.REFUNDED,
        )
        else "registrations.verify_registration_payments"
    )
    _require_permission(actor, required_permission)
    if not audit_note or not audit_note.strip():
        raise RegistrationError("Enter an audit note before changing payment status.")

    with transaction.atomic():
        registration = Registration.objects.select_for_update().get(pk=registration_id)
        current = registration.payment_status
        transitions = {
            Registration.PaymentStatus.PENDING: {
                Registration.PaymentStatus.PAID,
                Registration.PaymentStatus.FAILED,
                Registration.PaymentStatus.CANCELLED,
            },
            Registration.PaymentStatus.FAILED: {
                Registration.PaymentStatus.PENDING,
                Registration.PaymentStatus.PAID,
                Registration.PaymentStatus.CANCELLED,
            },
            Registration.PaymentStatus.PAID: {Registration.PaymentStatus.REFUNDED},
        }
        if new_status not in transitions.get(current, set()):
            raise RegistrationError("That payment status transition is not allowed.")

        registration.payment_status = new_status
        registration.payment_audit_note = audit_note.strip()
        registration.payment_reviewed_at = timezone.now()
        registration.payment_reviewed_by = actor
        notification = None
        if new_status == Registration.PaymentStatus.PAID:
            if not registration.pass_type.requires_manual_approval:
                registration.status = Registration.Status.CONFIRMED
                registration.status_reviewed_at = timezone.now()
                registration.status_reviewed_by = actor
                registration.status_audit_note = (
                    "Automatically confirmed after staff payment verification."
                )
            if registration.status == Registration.Status.CONFIRMED:
                registration.ticket_reference = registration.ticket_reference or uuid.uuid4()
            from apps.payments.models import PaymentEvent, PaymentTransaction

            late_verified_attempts = PaymentTransaction.objects.select_for_update().filter(
                registration=registration,
                status=PaymentTransaction.Status.VERIFICATION_FAILED,
                diagnostic_code="late_success_requires_reconciliation",
            )
            for payment in late_verified_attempts:
                payment.status = PaymentTransaction.Status.SUCCEEDED
                payment.diagnostic_code = "staff_reconciled_late_success"
                payment.save(update_fields=("status", "diagnostic_code", "updated_at"))
                PaymentEvent.objects.create(
                    transaction=payment,
                    event_type="late_success_reconciled",
                    source="staff",
                    diagnostic_code="staff_reconciled_late_success",
                )
            notification = "payment"
        elif new_status == Registration.PaymentStatus.REFUNDED:
            registration.status = Registration.Status.CANCELLED
            registration.status_reviewed_at = timezone.now()
            registration.status_reviewed_by = actor
            registration.status_audit_note = "Registration cancelled after refund."
            notification = "refund"
            from apps.payments.models import PaymentEvent, PaymentTransaction

            successful_transactions = PaymentTransaction.objects.select_for_update().filter(
                registration=registration,
                status=PaymentTransaction.Status.SUCCEEDED,
            )
            for payment in successful_transactions:
                payment.status = PaymentTransaction.Status.REFUNDED
                payment.diagnostic_code = "staff_refund_reconciled"
                payment.save(update_fields=("status", "diagnostic_code", "updated_at"))
                PaymentEvent.objects.create(
                    transaction=payment,
                    event_type="refund_reconciled",
                    source="staff",
                    diagnostic_code="staff_refund_reconciled",
                )
        elif new_status == Registration.PaymentStatus.FAILED:
            notification = "payment_failed"
        elif new_status == Registration.PaymentStatus.CANCELLED:
            registration.status = Registration.Status.CANCELLED
            registration.status_reviewed_at = timezone.now()
            registration.status_reviewed_by = actor
            registration.status_audit_note = "Registration cancelled by payment review."
            notification = "cancellation"
        registration.save(
            update_fields=(
                "payment_status",
                "payment_audit_note",
                "payment_reviewed_at",
                "payment_reviewed_by",
                "status",
                "ticket_reference",
                "status_reviewed_at",
                "status_reviewed_by",
                "status_audit_note",
                "updated_at",
            )
        )
        if notification:
            _email_after_commit(registration.pk, notification)
        if registration.ticket_reference and new_status == Registration.PaymentStatus.PAID:
            _email_after_commit(registration.pk, "ticket")
    return registration


def confirm_registration(*, registration_id, actor, audit_note):
    """Confirm eligible registrations and issue an opaque, non-PII ticket id."""
    _require_permission(actor, "registrations.manage_registration_status")
    if not audit_note or not audit_note.strip():
        raise RegistrationError("Enter an audit note before confirming.")
    with transaction.atomic():
        registration = Registration.objects.select_for_update().get(pk=registration_id)
        if registration.status != Registration.Status.PENDING:
            raise RegistrationError("Only pending registrations can be confirmed.")
        if registration.payment_status not in (
            Registration.PaymentStatus.PAID,
            Registration.PaymentStatus.NOT_REQUIRED,
        ):
            raise RegistrationError("Payment must be verified before confirmation.")
        registration.status = Registration.Status.CONFIRMED
        registration.ticket_reference = registration.ticket_reference or uuid.uuid4()
        registration.status_reviewed_at = timezone.now()
        registration.status_reviewed_by = actor
        registration.status_audit_note = audit_note.strip()
        registration.save(
            update_fields=(
                "status",
                "ticket_reference",
                "status_reviewed_at",
                "status_reviewed_by",
                "status_audit_note",
                "updated_at",
            )
        )
        _email_after_commit(registration.pk, "confirmation")
        _email_after_commit(registration.pk, "ticket")
    return registration


def cancel_registration(*, registration_id, actor, audit_note):
    """Cancel a registration without implying that a refund has been issued."""
    _require_permission(actor, "registrations.manage_registration_status")
    if not audit_note or not audit_note.strip():
        raise RegistrationError("Enter an audit note before cancelling.")
    with transaction.atomic():
        registration = Registration.objects.select_for_update().get(pk=registration_id)
        if registration.status == Registration.Status.CANCELLED:
            raise RegistrationError("This registration is already cancelled.")
        registration.status = Registration.Status.CANCELLED
        registration.status_reviewed_at = timezone.now()
        registration.status_reviewed_by = actor
        registration.status_audit_note = audit_note.strip()
        if registration.payment_status in (
            Registration.PaymentStatus.PENDING,
            Registration.PaymentStatus.FAILED,
        ):
            registration.payment_status = Registration.PaymentStatus.CANCELLED
        registration.save(
            update_fields=(
                "status",
                "payment_status",
                "status_reviewed_at",
                "status_reviewed_by",
                "status_audit_note",
                "updated_at",
            )
        )
        _email_after_commit(registration.pk, "cancellation")
    return registration


def send_registration_notification(registration_id, notification):
    """Email operational updates without logging addresses or personal details."""
    registration = Registration.objects.select_related("event").get(pk=registration_id)
    event = registration.event
    subject_by_kind = {
        "registration": "Summit registration received",
        "payment": "Summit payment confirmed",
        "payment_failed": "Summit payment needs attention",
        "confirmation": "Summit registration confirmed",
        "ticket": "Your YSSE Summit ticket",
        "cancellation": "Summit registration cancelled",
        "refund": "Summit payment refund recorded",
    }
    body = (
        f"Hello {registration.full_name},\n\n"
        f"{subject_by_kind[notification]} for {event.event_title}.\n"
        f"Registration reference: {registration.reference}\n"
        f"Pass: {registration.pass_name_snapshot}\n"
        f"Event date: {timezone.localtime(event.event_start_at).strftime('%A, %B %d, %Y %I:%M %p')}\n"
        f"Venue: {event.venue or 'To be announced'}\n"
        f"Registration status: {registration.get_status_display()}\n"
        f"Payment status: {registration.get_payment_status_display()}\n"
    )
    if registration.ticket_reference and notification in ("ticket", "payment", "confirmation"):
        ticket_path = f"/registrations/ticket/{registration.ticket_reference}/"
        public_base_url = getattr(settings, "PUBLIC_BASE_URL", "").rstrip("/")
        ticket_url = f"{public_base_url}{ticket_path}" if public_base_url else ticket_path
        body += (
            f"Ticket reference: {registration.ticket_reference}\n"
            f"Open your ticket at: {ticket_url}\n"
        )
    if notification == "registration" and registration.payment_status == Registration.PaymentStatus.PENDING:
        instructions = registration.pass_type.payment_instructions.strip()
        body += "\nPayment instructions:\n" + (
            instructions if instructions else "The summit team will share payment instructions."
        )
    try:
        send_mail(
            subject_by_kind[notification],
            body,
            None,
            [registration.contact_email],
            fail_silently=False,
        )
    except (OSError, smtplib.SMTPException):
        logger.exception(
            "Could not send registration notification kind=%s reference=%s",
            notification,
            registration.reference,
        )
