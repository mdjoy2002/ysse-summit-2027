"""Permission-gated management for private inquiries and public information."""

import csv

from django.contrib import admin
from django.http import HttpResponse
from django.utils import timezone

from .models import (
    ContactInquiry,
    FAQ,
    LegalDocument,
    NewsletterSubscription,
    PressRelease,
    PublicInformationSettings,
)


@admin.register(PublicInformationSettings)
class PublicInformationSettingsAdmin(admin.ModelAdmin):
    """Manage official channels without supplying fictional social URLs."""

    list_display = ("official_email", "office_address", "updated_at")
    readonly_fields = ("key", "updated_at")
    fields = (
        "key",
        "official_email",
        "hotline_numbers",
        "office_address",
        "facebook_url",
        "linkedin_url",
        "youtube_url",
        "instagram_url",
        "updated_at",
    )

    def has_add_permission(self, request):
        return not PublicInformationSettings.objects.exists() and super().has_add_permission(request)

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ContactInquiry)
class ContactInquiryAdmin(admin.ModelAdmin):
    """Keep visitor inquiries private and expose a modest review workflow."""

    list_display = ("subject", "name", "email", "status", "submitted_at", "reviewed_at")
    list_filter = ("status", "submitted_at")
    search_fields = ("name", "email", "subject", "message")
    date_hierarchy = "submitted_at"
    actions = ("export_selected_inquiries",)
    readonly_fields = ("name", "email", "subject", "message", "consent_given", "submitted_at", "reviewed_at")
    fieldsets = (
        ("Private inquiry", {"fields": ("name", "email", "subject", "message", "consent_given", "submitted_at")}),
        ("Staff review", {"fields": ("status", "review_notes", "reviewed_at")}),
    )

    @admin.action(
        permissions=("view",),
        description="Download selected inquiries as CSV",
    )
    def export_selected_inquiries(self, request, queryset):
        """Export only selected, permission-visible inquiries for staff review."""
        response = HttpResponse(content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = (
            'attachment; filename="ysse-contact-inquiries.csv"'
        )
        response["X-Content-Type-Options"] = "nosniff"
        response.write("\ufeff")
        writer = csv.writer(response)
        writer.writerow(
            (
                "name",
                "email",
                "subject",
                "message",
                "status",
                "consent_given",
                "submitted_at",
                "reviewed_at",
                "review_notes",
            )
        )
        for inquiry in queryset.order_by("submitted_at", "pk"):
            values = (
                inquiry.name,
                inquiry.email,
                inquiry.subject,
                inquiry.message,
                inquiry.get_status_display(),
                inquiry.consent_given,
                inquiry.submitted_at.isoformat(),
                inquiry.reviewed_at.isoformat() if inquiry.reviewed_at else "",
                inquiry.review_notes,
            )
            writer.writerow(
                tuple(
                    f"'{value}"
                    if isinstance(value, str)
                    and value.startswith(("=", "+", "-", "@"))
                    else value
                    for value in values
                )
            )
        return response

    def save_model(self, request, obj, form, change):
        old_status = None
        if change:
            old_status = ContactInquiry.objects.only("status").get(pk=obj.pk).status
        if not change or obj.status != old_status:
            obj.reviewed_at = timezone.now()
        super().save_model(request, obj, form, change)


@admin.register(FAQ)
class FAQAdmin(admin.ModelAdmin):
    """Order and publish frequently asked questions."""

    list_display = ("question", "category", "display_order", "is_published", "updated_at")
    list_editable = ("display_order", "is_published")
    list_filter = ("category", "is_published")
    search_fields = ("question", "answer")
    ordering = ("category", "display_order")
    readonly_fields = ("updated_at",)


@admin.register(LegalDocument)
class LegalDocumentAdmin(admin.ModelAdmin):
    """Keep policies unpublished until organizer review is explicitly recorded."""

    list_display = ("title", "kind", "organizer_reviewed", "is_published", "updated_at")
    list_filter = ("kind", "organizer_reviewed", "is_published")
    search_fields = ("title", "content")
    readonly_fields = ("updated_at",)
    fields = ("kind", "title", "content", "organizer_reviewed", "is_published", "updated_at")


@admin.register(PressRelease)
class PressReleaseAdmin(admin.ModelAdmin):
    """Manage public releases and their validated files."""

    list_display = ("title", "publication_date", "is_published", "updated_at")
    list_filter = ("is_published", "publication_date")
    search_fields = ("title", "content", "slug")
    prepopulated_fields = {"slug": ("title",)}
    date_hierarchy = "publication_date"
    readonly_fields = ("created_at", "updated_at")


@admin.register(NewsletterSubscription)
class NewsletterSubscriptionAdmin(admin.ModelAdmin):
    """Restrict subscriber access to staff with subscriber model permissions."""

    list_display = ("email", "status", "subscribed_at", "confirmed_at", "unsubscribed_at")
    list_filter = ("status", "subscribed_at")
    search_fields = ("email",)
    date_hierarchy = "subscribed_at"
    readonly_fields = (
        "email",
        "consent_given",
        "confirmation_token",
        "unsubscribe_token",
        "subscribed_at",
        "confirmed_at",
        "unsubscribed_at",
    )
    fields = readonly_fields + ("status",)
    actions = None

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
