"""Permission-aware management for pass inventory and private registrations."""

import csv

from django.contrib import admin, messages
from django.http import HttpResponse
from .models import PassType, Registration
from .services import (
    RegistrationError,
    cancel_registration,
    confirm_registration,
    verify_payment,
)


@admin.register(PassType)
class PassTypeAdmin(admin.ModelAdmin):
    """Manage pass prices, availability, capacity, and included benefits."""

    list_display = (
        "name",
        "category",
        "price",
        "price_confirmed",
        "is_published",
        "is_available",
        "capacity",
        "display_order",
    )
    list_filter = ("category", "price_confirmed", "is_published", "is_available")
    list_editable = ("display_order",)
    search_fields = ("name", "description", "included_benefits")
    readonly_fields = ("created_at", "updated_at")
    fieldsets = (
        ("Pass details", {"fields": ("name", "category", "description", "included_benefits")}),
        (
            "Price and payment",
            {
                "fields": (
                    "price",
                    "price_confirmed",
                    "payment_instructions",
                    "requires_manual_approval",
                )
            },
        ),
        (
            "Availability",
            {
                "fields": (
                    "is_published",
                    "is_available",
                    "capacity",
                    "available_from",
                    "available_until",
                    "display_order",
                )
            },
        ),
        ("Audit", {"fields": ("created_at", "updated_at")}),
    )


def export_registration_report(modeladmin, request, queryset):
    """Export operational status only, omitting participant contact details."""
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = 'attachment; filename="summit-registrations.csv"'
    response["X-Content-Type-Options"] = "nosniff"
    writer = csv.writer(response)
    writer.writerow(
        ("reference", "pass", "amount due", "registration status", "payment status", "submitted at")
    )
    for registration in queryset.select_related("pass_type"):
        values = (
            str(registration.reference),
            registration.pass_name_snapshot,
            str(registration.amount_due),
            registration.get_status_display(),
            registration.get_payment_status_display(),
            registration.submitted_at.isoformat(),
        )
        writer.writerow(
            tuple(
                f"'{value}" if value.startswith(("=", "+", "-", "@")) else value
                for value in values
            )
        )
    return response


export_registration_report.short_description = "Export selected registration status report"


@admin.register(Registration)
class RegistrationAdmin(admin.ModelAdmin):
    """Keep participant details read-only and expose audited workflow actions."""

    list_display = (
        "reference",
        "full_name",
        "pass_name_snapshot",
        "status",
        "payment_status",
        "payment_reviewed_at",
        "payment_reviewed_by",
        "submitted_at",
    )
    list_filter = ("status", "payment_status", "pass_type", "submitted_at")
    search_fields = (
        "=reference",
        "=ticket_reference",
        "full_name",
        "contact_email",
        "participant__username",
    )
    date_hierarchy = "submitted_at"
    actions = (
        "confirm_selected",
        "mark_selected_paid",
        "mark_selected_failed",
        "refund_selected",
        "cancel_selected",
        export_registration_report,
    )
    fieldsets = (
        (
            "Participant and pass",
            {
                "fields": (
                    "reference",
                    "participant",
                    "full_name",
                    "contact_email",
                    "phone_number",
                    "event",
                    "pass_type",
                    "pass_name_snapshot",
                    "benefits_snapshot",
                    "amount_due",
                    "submitted_at",
                    "updated_at",
                )
            },
        ),
        (
            "Registration and ticket",
            {
                "fields": (
                    "status",
                    "ticket_reference",
                    "status_reviewed_at",
                    "status_reviewed_by",
                    "status_audit_note",
                )
            },
        ),
        (
            "Payment verification audit",
            {
                "fields": (
                    "payment_status",
                    "payment_reviewed_at",
                    "payment_reviewed_by",
                    "payment_audit_note",
                )
            },
        ),
    )

    def get_readonly_fields(self, request, obj=None):
        readonly = [
            "reference",
            "ticket_reference",
            "participant",
            "full_name",
            "contact_email",
            "phone_number",
            "event",
            "pass_type",
            "pass_name_snapshot",
            "benefits_snapshot",
            "amount_due",
            "submitted_at",
            "updated_at",
            "status",
            "payment_status",
            "payment_reviewed_at",
            "payment_reviewed_by",
            "status_reviewed_at",
            "status_reviewed_by",
        ]
        can_review_payment = (
            request.user.has_perm("registrations.verify_registration_payments")
            or request.user.has_perm("registrations.reconcile_registration_payments")
        )
        if not (
            can_review_payment
            or request.user.has_perm("registrations.manage_registration_status")
        ):
            readonly.append("payment_audit_note")
            readonly.append("status_audit_note")
        else:
            if not request.user.has_perm("registrations.manage_registration_status"):
                readonly.append("status_audit_note")
            if not can_review_payment:
                readonly.append("payment_audit_note")
        return readonly

    def has_add_permission(self, request):
        return False

    def has_verify_registration_payments_permission(self, request):
        return request.user.has_perm("registrations.verify_registration_payments")

    def has_reconcile_registration_payments_permission(self, request):
        return request.user.has_perm(
            "registrations.reconcile_registration_payments"
        )

    def has_manage_registration_status_permission(self, request):
        return request.user.has_perm("registrations.manage_registration_status")

    def get_actions(self, request):
        actions = super().get_actions(request)
        if not request.user.has_perm(
            "registrations.reconcile_registration_payments"
        ):
            for action_name in ("mark_selected_paid", "refund_selected"):
                actions.pop(action_name, None)
        if not self.has_verify_registration_payments_permission(request):
            actions.pop("mark_selected_failed", None)
        if not self.has_manage_registration_status_permission(request):
            for action_name in ("confirm_selected", "cancel_selected"):
                actions.pop(action_name, None)
        return actions

    def _run_action(self, request, queryset, operation, success_message):
        completed = 0
        for registration in queryset:
            try:
                operation(registration)
            except RegistrationError as error:
                self.message_user(request, f"{registration.reference}: {error}", level=messages.ERROR)
            else:
                completed += 1
        if completed:
            self.message_user(request, success_message.format(count=completed), level=messages.SUCCESS)

    @admin.action(permissions=["manage_registration_status"], description="Confirm eligible registrations")
    def confirm_selected(self, request, queryset):
        self._run_action(
            request,
            queryset,
            lambda registration: confirm_registration(
                registration_id=registration.pk,
                actor=request.user,
                audit_note=registration.status_audit_note,
            ),
            "Confirmed {count} registration(s).",
        )

    @admin.action(
        permissions=["reconcile_registration_payments"],
        description="Reconcile payments after independent verification (audit note required)",
    )
    def mark_selected_paid(self, request, queryset):
        self._run_action(
            request,
            queryset,
            lambda registration: verify_payment(
                registration_id=registration.pk,
                new_status=Registration.PaymentStatus.PAID,
                actor=request.user,
                audit_note=registration.payment_audit_note,
            ),
            "Verified {count} payment(s).",
        )

    @admin.action(permissions=["verify_registration_payments"], description="Mark selected payments failed")
    def mark_selected_failed(self, request, queryset):
        self._run_action(
            request,
            queryset,
            lambda registration: verify_payment(
                registration_id=registration.pk,
                new_status=Registration.PaymentStatus.FAILED,
                actor=request.user,
                audit_note=registration.payment_audit_note,
            ),
            "Marked {count} payment(s) failed.",
        )

    @admin.action(
        permissions=["reconcile_registration_payments"],
        description="Record verified refunds (audit note required)",
    )
    def refund_selected(self, request, queryset):
        self._run_action(
            request,
            queryset,
            lambda registration: verify_payment(
                registration_id=registration.pk,
                new_status=Registration.PaymentStatus.REFUNDED,
                actor=request.user,
                audit_note=registration.payment_audit_note,
            ),
            "Recorded {count} refund(s).",
        )

    @admin.action(permissions=["manage_registration_status"], description="Cancel selected registrations (audit note required)")
    def cancel_selected(self, request, queryset):
        self._run_action(
            request,
            queryset,
            lambda registration: cancel_registration(
                registration_id=registration.pk,
                actor=request.user,
                audit_note=registration.status_audit_note,
            ),
            "Cancelled {count} registration(s).",
        )
