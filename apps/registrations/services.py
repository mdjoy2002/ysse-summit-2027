"""Transactional registration, payment review, and notification workflows."""

import logging
import smtplib
import uuid
from decimal import Decimal
from email.mime.image import MIMEImage
from pathlib import Path

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.db import IntegrityError, transaction
from django.template.loader import render_to_string
from django.utils import timezone

from apps.home.models import SummitEvent

from .models import (
    PassType,
    Registration,
    RegistrationEmailSettings,
    generate_registration_reference,
)


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


def _email_after_commit(registration_id, notification, status_comment=None):
    """Send notification after the corresponding database transaction commits."""
    transaction.on_commit(
        lambda: send_registration_notification(
            registration_id,
            notification,
            status_comment=status_comment,
        ),
        robust=True,
    )


def create_registration(*, participant=None, form, event, manual_payment_method=""):
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
                ).exclude(
                    status__in=(
                        Registration.Status.CANCELLED,
                        Registration.Status.REJECTED,
                    )
                ).count()
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
                manual_payment_method=manual_payment_method,
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

    _email_after_commit(
        registration.pk,
        "confirmation"
        if registration.status == Registration.Status.CONFIRMED
        else "registration",
    )
    return registration


def _require_permission(actor, permission):
    if (
        not actor.is_authenticated
        or not actor.is_staff
        or not actor.has_perm(permission)
    ):
        raise PermissionError("The required registration permission is missing.")


def verify_payment(
    *,
    registration_id,
    new_status,
    actor,
    audit_note,
    status_audit_note=None,
    notify=True,
    auto_confirm=True,
):
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
            if (
                auto_confirm
                and not registration.pass_type.requires_manual_approval
            ):
                registration.status = Registration.Status.CONFIRMED
                registration.status_reviewed_at = timezone.now()
                registration.status_reviewed_by = actor
                registration.status_audit_note = (
                    status_audit_note.strip()
                    if status_audit_note and status_audit_note.strip()
                    else "Automatically confirmed after staff payment verification."
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
            notification = (
                "confirmation"
                if registration.status == Registration.Status.CONFIRMED
                else "payment"
            )
        elif new_status == Registration.PaymentStatus.REFUNDED:
            registration.status = Registration.Status.CANCELLED
            registration.status_reviewed_at = timezone.now()
            registration.status_reviewed_by = actor
            registration.status_audit_note = (
                status_audit_note.strip()
                if status_audit_note and status_audit_note.strip()
                else "Registration cancelled after refund."
            )
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
            registration.status_audit_note = (
                status_audit_note.strip()
                if status_audit_note and status_audit_note.strip()
                else "Registration cancelled by payment review."
            )
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
        if notification and notify:
            _email_after_commit(registration.pk, notification)
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
        _email_after_commit(
            registration.pk,
            "confirmation",
            status_comment=registration.status_audit_note,
        )
    return registration


def reject_registration(*, registration_id, actor, audit_note):
    """Reject a pending registration and email the reviewer's comment."""
    _require_permission(actor, "registrations.manage_registration_status")
    if not audit_note or not audit_note.strip():
        raise RegistrationError("Enter a comment before rejecting.")
    with transaction.atomic():
        registration = Registration.objects.select_for_update().get(pk=registration_id)
        if registration.status != Registration.Status.PENDING:
            raise RegistrationError("Only pending registrations can be rejected.")
        registration.status = Registration.Status.REJECTED
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
        _email_after_commit(
            registration.pk,
            "rejection",
            status_comment=registration.status_audit_note,
        )
    return registration


def cancel_registration(*, registration_id, actor, audit_note):
    """Cancel a registration without implying that a refund has been issued."""
    _require_permission(actor, "registrations.manage_registration_status")
    if not audit_note or not audit_note.strip():
        raise RegistrationError("Enter an audit note before cancelling.")
    with transaction.atomic():
        registration = Registration.objects.select_for_update().get(pk=registration_id)
        if registration.status in (
            Registration.Status.CANCELLED,
            Registration.Status.REJECTED,
        ):
            raise RegistrationError("This registration is already closed.")
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
        _email_after_commit(
            registration.pk,
            "cancellation",
            status_comment=registration.status_audit_note,
        )
    return registration


def send_registration_notification(registration_id, notification, status_comment=None):
    """Email operational updates without logging addresses or personal details."""
    registration = Registration.objects.select_related(
        "event",
        "pass_type",
    ).get(pk=registration_id)
    event = registration.event
    subject_by_kind = {
        "registration": "Summit registration received",
        "payment": "Summit payment confirmed",
        "payment_failed": "Summit payment needs attention",
        "confirmation": "Summit registration confirmed",
        "rejection": "Summit registration not approved",
        "ticket": "Your YSSE Summit ticket",
        "cancellation": "Summit registration cancelled",
        "refund": "Summit payment refund recorded",
    }
    ticket_path = (
        f"/registrations/ticket/{registration.ticket_reference}/"
        if registration.ticket_reference
        else ""
    )
    public_base_url = getattr(settings, "PUBLIC_BASE_URL", "").rstrip("/")
    ticket_url = f"{public_base_url}{ticket_path}" if public_base_url else ""
    event_date = timezone.localtime(event.event_start_at).strftime(
        "%A, %B %d, %Y %I:%M %p"
    )
    status_comment = status_comment or registration.status_audit_note
    notification_message = ""
    status_comment_in_message = False
    if notification in ("confirmation", "rejection"):
        email_settings = RegistrationEmailSettings.objects.get(
            key=RegistrationEmailSettings.Key.MAIN
        )
        field_prefix = notification
        subject_by_kind[notification] = getattr(
            email_settings, f"{field_prefix}_subject"
        )
        message_template = getattr(
            email_settings, f"{field_prefix}_message"
        )
        template_values = {
            "full_name": registration.full_name,
            "event_title": event.event_title,
            "registration_id": registration.reference,
            "pass_name": registration.pass_name_snapshot,
            "amount": f"BDT {registration.amount_due:.2f}",
            "event_date": event_date,
            "venue": event.venue or "To be announced",
            "status_comment": status_comment or "",
            "ticket_url": ticket_url,
        }
        subject_by_kind[notification] = subject_by_kind[
            notification
        ].format_map(
            {
                name: " ".join(value.split())
                for name, value in template_values.items()
            }
        )
        status_comment_in_message = "{status_comment}" in message_template
        notification_message = message_template.format_map(template_values)

    body = f"Hello {registration.full_name},\n\n"
    if notification_message:
        body += f"{notification_message}\n\n"
    else:
        body += f"{subject_by_kind[notification]} for {event.event_title}.\n\n"
    body += (
        f"Registration ID: {registration.reference}\n"
        f"Pass: {registration.pass_name_snapshot}\n"
        f"Full name: {registration.full_name}\n"
        f"Email: {registration.contact_email}\n"
        f"Phone: {registration.phone_number or 'Not provided'}\n"
        f"Amount: BDT {registration.amount_due:.2f}\n"
        f"Event date: {event_date}\n"
        f"Venue: {event.venue or 'To be announced'}\n"
        f"Registration status: {registration.get_status_display()}\n"
        f"Payment status: {registration.get_payment_status_display()}\n"
    )
    if status_comment and not status_comment_in_message:
        body += f"\nComment from the registration team:\n{status_comment}\n"
    if registration.ticket_reference and notification in (
        "ticket",
        "payment",
        "confirmation",
    ):
        body += f"Ticket reference: {registration.ticket_reference}\n"
        if ticket_url:
            body += f"Open your ticket at: {ticket_url}\n"
    if notification == "registration" and registration.payment_status == Registration.PaymentStatus.PENDING:
        if registration.payment_submitted_at:
            body += (
                "\nPayment details submitted:\n"
                f"Payment service: {registration.get_manual_payment_method_display()}\n"
                f"Transaction ID: {registration.payment_transaction_id}\n"
                f"Submission time: {timezone.localtime(registration.payment_submitted_at).strftime('%Y-%m-%d %I:%M %p')}\n"
                "Payment screenshot: uploaded and received.\n"
            )
        else:
            body += "\nPayment: awaiting submission.\n"
    tracking_path = "/registrations/track/"
    public_base_url = getattr(settings, "PUBLIC_BASE_URL", "").rstrip("/")
    tracking_url = f"{public_base_url}{tracking_path}" if public_base_url else ""
    html_body = render_to_string(
        "registrations/emails/notification.html",
        {
            "registration": registration,
            "event": event,
            "notification": notification,
            "subject": subject_by_kind[notification],
            "status_comment": status_comment,
            "notification_message": notification_message,
            "show_status_comment": bool(
                status_comment and not status_comment_in_message
            ),
            "ticket_url": ticket_url,
            "event_date": event_date,
            "pass_category_display": registration.pass_type.get_category_display(),
            "pass_style": {
                PassType.Category.MEMBER: {
                    "background": "#e9f5ed",
                    "accent": "#157347",
                    "label": "YSSE MEMBER PASS",
                },
                PassType.Category.GENERAL: {
                    "background": "#edf3fb",
                    "accent": "#315b8a",
                    "label": "GENERAL DELEGATE PASS",
                },
                PassType.Category.VIP: {
                    "background": "#fff4d8",
                    "accent": "#8b6417",
                    "label": "VIP PASS",
                },
            }[registration.pass_type.category],
            "has_payment_submission": bool(registration.payment_submitted_at),
            "tracking_url": tracking_url,
        },
    )
    try:
        message = EmailMultiAlternatives(
            subject_by_kind[notification],
            body,
            settings.DEFAULT_FROM_EMAIL,
            [registration.contact_email],
        )
        message.attach_alternative(html_body, "text/html")
        if (
            notification == "confirmation"
            and registration.ticket_reference
            and registration.ticket_is_valid
        ):
            message.mixed_subtype = "related"
            for filename, content_id, subtype in (
                ("ysse-logo.png", "ysse-logo", "png"),
                ("ysse-12th-anniversary.jpg", "ysse-anniversary", "jpeg"),
            ):
                image_path = Path(settings.BASE_DIR) / "static" / "images" / filename
                image = MIMEImage(image_path.read_bytes(), _subtype=subtype)
                image.add_header("Content-ID", f"<{content_id}>")
                image.add_header(
                    "Content-Disposition",
                    "inline",
                    filename=filename,
                )
                message.attach(image)
        message.send(fail_silently=False)
    except (OSError, smtplib.SMTPException):
        logger.exception(
            "Could not send registration notification kind=%s reference=%s",
            notification,
            registration.reference,
        )
