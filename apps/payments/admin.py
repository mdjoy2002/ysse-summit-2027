"""Read-only payment diagnostics for explicitly authorized staff."""

from django.contrib import admin

from .models import PaymentEvent, PaymentTransaction


class PaymentEventInline(admin.TabularInline):
    """Display redacted event metadata without exposing callback payloads."""

    model = PaymentEvent
    extra = 0
    can_delete = False
    fields = (
        "event_type",
        "source",
        "diagnostic_code",
        "provider_event_reference",
        "payload_digest",
        "occurred_at",
    )
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False

    def has_view_permission(self, request, obj=None):
        return request.user.has_perm("payments.view_payment_diagnostics")


@admin.register(PaymentTransaction)
class PaymentTransactionAdmin(admin.ModelAdmin):
    """Allow lookup and diagnostics; never provide a paid-status edit action."""

    list_display = (
        "reference",
        "registration_reference",
        "provider",
        "provider_transaction_reference",
        "method",
        "amount",
        "currency",
        "status",
        "created_at",
        "verified_at",
    )
    list_filter = ("provider", "method", "status", "currency", "created_at")
    search_fields = (
        "=reference",
        "=provider_transaction_reference",
        "registration__reference",
    )
    date_hierarchy = "created_at"
    readonly_fields = (
        "reference",
        "registration",
        "provider",
        "provider_transaction_reference",
        "method",
        "status",
        "amount",
        "currency",
        "created_at",
        "updated_at",
        "expires_at",
        "verified_at",
        "diagnostic_code",
    )
    fields = readonly_fields
    inlines = (PaymentEventInline,)

    def registration_reference(self, obj):
        return obj.registration.reference

    registration_reference.short_description = "Registration reference"

    def has_add_permission(self, request):
        return False

    def has_view_permission(self, request, obj=None):
        return request.user.has_perm("payments.view_paymenttransaction")

    def has_change_permission(self, request, obj=None):
        return request.user.has_perm("payments.view_paymenttransaction")

    def has_delete_permission(self, request, obj=None):
        return False

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        if request.user.has_perm("payments.view_payment_diagnostics"):
            return queryset
        return queryset.defer("diagnostic_code")

    def get_fields(self, request, obj=None):
        fields = list(self.fields)
        if not request.user.has_perm("payments.view_payment_diagnostics"):
            fields.remove("diagnostic_code")
        return fields


@admin.register(PaymentEvent)
class PaymentEventAdmin(admin.ModelAdmin):
    """Expose only safe event codes and hashes to payment-diagnostics staff."""

    list_display = (
        "transaction",
        "event_type",
        "source",
        "diagnostic_code",
        "occurred_at",
    )
    list_filter = ("event_type", "source", "diagnostic_code", "occurred_at")
    search_fields = (
        "transaction__reference",
        "transaction__registration__reference",
        "provider_event_reference",
    )
    date_hierarchy = "occurred_at"
    readonly_fields = (
        "transaction",
        "event_type",
        "source",
        "diagnostic_code",
        "payload_digest",
        "provider_event_reference",
        "occurred_at",
    )
    fields = readonly_fields

    def has_add_permission(self, request):
        return False

    def has_view_permission(self, request, obj=None):
        return request.user.has_perm("payments.view_payment_diagnostics")

    def has_change_permission(self, request, obj=None):
        return request.user.has_perm("payments.view_payment_diagnostics")

    def has_delete_permission(self, request, obj=None):
        return False
