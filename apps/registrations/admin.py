"""Permission-aware management for pass inventory and private registrations."""

import csv

from django import forms
from django.contrib import admin, messages
from django.contrib.admin.helpers import ActionForm
from django.db import transaction
from django.http import HttpResponse, HttpResponseRedirect
from django.urls import reverse
from django.utils.html import format_html

from .models import (
    PaymentSettings,
    PassType,
    Registration,
    RegistrationEmailSettings,
)
from .services import (
    RegistrationError,
    cancel_registration,
    confirm_registration,
    reject_registration,
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


@admin.register(PaymentSettings)
class PaymentSettingsAdmin(admin.ModelAdmin):
    """Manage the merchant numbers displayed to participants at checkout."""

    fields = ("bkash_number", "nagad_number", "rocket_number", "updated_at")
    readonly_fields = ("updated_at",)

    def has_add_permission(self, request):
        return not PaymentSettings.objects.exists() and super().has_add_permission(request)

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(RegistrationEmailSettings)
class RegistrationEmailSettingsAdmin(admin.ModelAdmin):
    """Let authorized organizers edit registration decision emails."""

    fields = (
        "key",
        "confirmation_subject",
        "confirmation_message",
        "rejection_subject",
        "rejection_message",
        "updated_at",
    )
    readonly_fields = ("key", "updated_at")

    def has_add_permission(self, request):
        return (
            not RegistrationEmailSettings.objects.exists()
            and super().has_add_permission(request)
        )

    def has_delete_permission(self, request, obj=None):
        return False


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


class RegistrationActionForm(ActionForm):
    status_comment = forms.CharField(
        label="Decision comment",
        required=False,
        widget=forms.Textarea(attrs={"rows": 2, "cols": 60}),
        help_text="Required when confirming, rejecting, or cancelling registrations.",
    )


class RegistrationAdminForm(forms.ModelForm):
    """Validate status edits before the audited workflow services run."""

    def __init__(self, *args, request=None, **kwargs):
        self.request = request
        super().__init__(*args, **kwargs)

    def clean(self):
        cleaned_data = super().clean()
        if not self.instance.pk:
            return cleaned_data

        current = Registration.objects.get(pk=self.instance.pk)
        new_status = cleaned_data.get("status", current.status)
        new_payment_status = cleaned_data.get(
            "payment_status", current.payment_status
        )
        status_changed = new_status != current.status
        payment_changed = new_payment_status != current.payment_status

        final_payment_status = (
            new_payment_status if payment_changed else current.payment_status
        )
        if status_changed:
            if not self.request.user.has_perm(
                "registrations.manage_registration_status"
            ):
                self.add_error("status", "You cannot change registration status.")
            elif new_status == Registration.Status.CONFIRMED:
                if current.status != Registration.Status.PENDING:
                    self.add_error(
                        "status", "Only pending registrations can be confirmed."
                    )
                if final_payment_status not in (
                    Registration.PaymentStatus.PAID,
                    Registration.PaymentStatus.NOT_REQUIRED,
                ):
                    self.add_error(
                        "status", "Payment must be verified before confirmation."
                    )
            elif new_status == Registration.Status.REJECTED:
                if current.status != Registration.Status.PENDING:
                    self.add_error(
                        "status", "Only pending registrations can be rejected."
                    )
            elif new_status == Registration.Status.CANCELLED:
                if current.status in (
                    Registration.Status.CANCELLED,
                    Registration.Status.REJECTED,
                ):
                    self.add_error(
                        "status", "This registration is already closed."
                    )
            else:
                self.add_error("status", "That registration status change is not allowed.")

            if not cleaned_data.get("status_audit_note", "").strip():
                self.add_error(
                    "status_audit_note",
                    "Enter a decision comment before changing registration status.",
                )

        if status_changed and payment_changed:
            incompatible = (
                new_payment_status == Registration.PaymentStatus.PAID
                and new_status != Registration.Status.CONFIRMED
            ) or (
                new_payment_status
                in (
                    Registration.PaymentStatus.CANCELLED,
                    Registration.PaymentStatus.REFUNDED,
                )
                and new_status != Registration.Status.CANCELLED
            )
            if incompatible:
                self.add_error(
                    None,
                    "These registration and payment status choices conflict. Choose compatible statuses.",
                )

        if payment_changed:
            permission = (
                "registrations.reconcile_registration_payments"
                if new_payment_status
                in (
                    Registration.PaymentStatus.PAID,
                    Registration.PaymentStatus.REFUNDED,
                )
                else "registrations.verify_registration_payments"
            )
            if not self.request.user.has_perm(permission):
                self.add_error(
                    "payment_status",
                    "You do not have permission to make this payment decision.",
                )
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
                Registration.PaymentStatus.PAID: {
                    Registration.PaymentStatus.REFUNDED
                },
            }
            if new_payment_status not in transitions.get(
                current.payment_status, set()
            ):
                self.add_error(
                    "payment_status", "That payment status transition is not allowed."
                )
            if not cleaned_data.get("payment_audit_note", "").strip():
                self.add_error(
                    "payment_audit_note",
                    "Enter an audit note before changing payment status.",
                )

        return cleaned_data

    class Meta:
        model = Registration
        fields = "__all__"


@admin.register(Registration)
class RegistrationAdmin(admin.ModelAdmin):
    """Keep participant details read-only and expose audited workflow actions."""

    action_form = RegistrationActionForm
    change_form_template = (
        "admin/registrations/registration/change_form.html"
    )
    list_display = (
        "reference",
        "full_name",
        "pass_name_snapshot",
        "status",
        "payment_status",
        "manual_payment_method",
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
        "reject_selected",
        "mark_selected_paid",
        "mark_selected_failed",
        "refund_selected",
        "cancel_selected",
        "confirm_manual_payments",
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
                    "manual_payment_method",
                    "payment_transaction_id",
                    "payment_proof_link",
                    "payment_submitted_at",
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
            "manual_payment_method",
            "payment_transaction_id",
            "payment_proof_link",
            "payment_submitted_at",
            "payment_reviewed_at",
            "payment_reviewed_by",
            "status_reviewed_at",
            "status_reviewed_by",
        ]
        if not request.user.has_perm("registrations.manage_registration_status"):
            readonly.append("status")
        can_review_payment = (
            request.user.has_perm("registrations.verify_registration_payments")
            or request.user.has_perm("registrations.reconcile_registration_payments")
        )
        if not can_review_payment:
            readonly.append("payment_status")
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

    def get_form(self, request, obj=None, **kwargs):
        kwargs["form"] = RegistrationAdminForm
        form_class = super().get_form(request, obj, **kwargs)

        class RequestAwareRegistrationAdminForm(form_class):
            def __init__(self, *args, **form_kwargs):
                form_kwargs["request"] = request
                super().__init__(*args, **form_kwargs)

        return RequestAwareRegistrationAdminForm

    def save_model(self, request, obj, form, change):
        previous = (
            Registration.objects.get(pk=obj.pk)
            if change
            else None
        )
        requested_status = obj.status
        requested_payment_status = obj.payment_status
        status_note = obj.status_audit_note
        payment_note = obj.payment_audit_note
        if previous:
            obj.status = previous.status
            obj.payment_status = previous.payment_status
        super().save_model(request, obj, form, change)

        if not previous:
            return
        status_changed = requested_status != previous.status
        payment_changed = requested_payment_status != previous.payment_status
        if status_changed and payment_changed:
            with transaction.atomic():
                verify_payment(
                    registration_id=obj.pk,
                    new_status=requested_payment_status,
                    actor=request.user,
                    audit_note=payment_note,
                    status_audit_note=status_note,
                    notify=not status_changed,
                    auto_confirm=not status_changed,
                )
                current = Registration.objects.get(pk=obj.pk)
                if current.status != requested_status:
                    operation = {
                        Registration.Status.CONFIRMED: confirm_registration,
                        Registration.Status.REJECTED: reject_registration,
                        Registration.Status.CANCELLED: cancel_registration,
                    }[requested_status]
                    operation(
                        registration_id=obj.pk,
                        actor=request.user,
                        audit_note=status_note,
                    )
                elif requested_status == Registration.Status.CONFIRMED:
                    Registration.objects.filter(pk=obj.pk).update(
                        status_audit_note=status_note.strip()
                    )
            self.message_user(
                request,
                "Registration and payment status updated; participant notified.",
                level=messages.SUCCESS,
            )
        elif status_changed:
            operation = {
                Registration.Status.CONFIRMED: confirm_registration,
                Registration.Status.REJECTED: reject_registration,
                Registration.Status.CANCELLED: cancel_registration,
            }[requested_status]
            operation(
                registration_id=obj.pk,
                actor=request.user,
                audit_note=status_note,
            )
            self.message_user(
                request,
                f"Registration {requested_status} and participant notified.",
                level=messages.SUCCESS,
            )
        elif payment_changed:
            verify_payment(
                registration_id=obj.pk,
                new_status=requested_payment_status,
                actor=request.user,
                audit_note=payment_note,
            )
            self.message_user(
                request,
                "Payment status updated and participant notified.",
                level=messages.SUCCESS,
            )

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

    def render_change_form(self, request, context, *args, **kwargs):
        context["can_manage_registration_status"] = (
            self.has_manage_registration_status_permission(request)
        )
        return super().render_change_form(request, context, *args, **kwargs)

    def response_change(self, request, obj):
        action = (
            "confirm"
            if "_confirm_registration" in request.POST
            else "reject"
            if "_reject_registration" in request.POST
            else None
        )
        if action is None:
            return super().response_change(request, obj)

        if not self.has_manage_registration_status_permission(request):
            self.message_user(
                request,
                "You do not have permission to change registration status.",
                level=messages.ERROR,
            )
        else:
            try:
                operation = (
                    confirm_registration
                    if action == "confirm"
                    else reject_registration
                )
                operation(
                    registration_id=obj.pk,
                    actor=request.user,
                    audit_note=request.POST.get("status_audit_note", ""),
                )
            except RegistrationError as error:
                self.message_user(request, str(error), level=messages.ERROR)
            else:
                self.message_user(
                    request,
                    "Registration confirmed and participant notified."
                    if action == "confirm"
                    else "Registration rejected and participant notified.",
                    level=messages.SUCCESS,
                )

        return HttpResponseRedirect(
            reverse(
                "admin:registrations_registration_change",
                args=(obj.pk,),
            )
        )

    @admin.display(description="Payment proof")
    def payment_proof_link(self, obj):
        if not obj.payment_proof:
            return "No payment proof uploaded"
        url = reverse(
            "registrations:payment-proof",
            kwargs={"reference": obj.reference},
        )
        return format_html('<a href="{}">Download private payment proof</a>', url)

    def get_actions(self, request):
        actions = super().get_actions(request)
        if not request.user.has_perm(
            "registrations.reconcile_registration_payments"
        ):
            for action_name in (
                "mark_selected_paid",
                "refund_selected",
                "confirm_manual_payments",
            ):
                actions.pop(action_name, None)
        if not self.has_verify_registration_payments_permission(request):
            actions.pop("mark_selected_failed", None)
        if not self.has_manage_registration_status_permission(request):
            for action_name in (
                "confirm_selected",
                "reject_selected",
                "cancel_selected",
            ):
                actions.pop(action_name, None)
        return actions

    def _status_action_comment(self, request):
        comment = request.POST.get("status_comment", "").strip()
        if not comment:
            self.message_user(
                request,
                "Enter a decision comment before changing registration status.",
                level=messages.ERROR,
            )
            return None
        return comment

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
        comment = self._status_action_comment(request)
        if comment is None:
            return
        self._run_action(
            request,
            queryset,
            lambda registration: confirm_registration(
                registration_id=registration.pk,
                actor=request.user,
                audit_note=comment,
            ),
            "Confirmed {count} registration(s).",
        )

    @admin.action(
        permissions=["manage_registration_status"],
        description="Reject selected pending registrations",
    )
    def reject_selected(self, request, queryset):
        comment = self._status_action_comment(request)
        if comment is None:
            return
        self._run_action(
            request,
            queryset,
            lambda registration: reject_registration(
                registration_id=registration.pk,
                actor=request.user,
                audit_note=comment,
            ),
            "Rejected {count} registration(s).",
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

    @admin.action(
        permissions=["reconcile_registration_payments"],
        description="Confirm selected submitted mobile payments",
    )
    def confirm_manual_payments(self, request, queryset):
        def confirm(registration):
            if not (
                registration.manual_payment_method
                and registration.payment_transaction_id
                and registration.payment_proof
                and registration.payment_submitted_at
            ):
                raise RegistrationError(
                    "A mobile banking method, transaction ID, screenshot, and submission time are required."
                )
            return verify_payment(
                registration_id=registration.pk,
                new_status=Registration.PaymentStatus.PAID,
                actor=request.user,
                audit_note=registration.payment_audit_note,
            )

        self._run_action(
            request,
            queryset,
            confirm,
            "Confirmed {count} submitted mobile payment(s).",
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
        comment = self._status_action_comment(request)
        if comment is None:
            return
        self._run_action(
            request,
            queryset,
            lambda registration: cancel_registration(
                registration_id=registration.pk,
                actor=request.user,
                audit_note=comment,
            ),
            "Cancelled {count} registration(s).",
        )
